# Issue Fixes

## Phase 1: Unwedge the Companion

### Slice 1: A stuck tool call ends the turn instead of freezing the session

**What this delivers:** A tool call that never returns fails the turn with a visible, recoverable error after a bounded wait, and `✕ Stop` ends it immediately — replacing the current behaviour where the status pill sits on `search_papers…` forever, `Stop` does nothing, and every later message is rejected until the backend process is restarted.

Root cause: the turn loop's cancellation is entirely cooperative and only wired into one of its two waits. `run_turn` checks `cancel_flag.is_set()` between streamed chunks (`backend/harness/__init__.py:411`) and bounds the completion with `_COMPLETION_TIMEOUT_S = 90`, but the tool-dispatch branch `result = await dispatch_tool(...)` (`:456`) has neither a timeout nor a flag check. `interrupt` only calls `flag.set()` (`:508–512`) and never cancels the task, so a coroutine blocked inside that await ignores it indefinitely. Because the `_in_flight.pop(key, None)` that releases the session (`:505`) is downstream of that await, the slot is never freed either — which is why `begin_turn` then rejects every subsequent turn for the life of the process. The fix is a timeout on the dispatch await raced against `cancel_flag`, and releasing the in-flight slot from a `finally` so no failure path can leak it.

**Depends on:** none

**Touches:** `Agent Harness` (`backend/harness/`) — `run_turn`'s tool-dispatch branch, `interrupt`, `begin_turn`, `_in_flight`, a new per-tool timeout constant alongside `_COMPLETION_TIMEOUT_S`; `messages` rows of role `tool_call` / `tool_result`. `Session Transport` (`backend/ws/`) — the `ErrorEvent` / `TurnCompleteEvent` emitted on a timed-out or interrupted tool call.

### Slice 2: A cold reranker fails fast instead of blocking the first search

**What this delivers:** The first `search_papers` of a session returns results (reranked, or explicitly un-reranked, naming the degradation the way a failed literature source already does) within a bounded time even with no cached model and no network, instead of hanging.

Root cause: `rerank` lazily constructs the cross-encoder on first call — `_model = await asyncio.to_thread(CrossEncoder, _MODEL_ID)` (`backend/search/reranker.py`) — which downloads from the HuggingFace Hub with no timeout on the first run. In the captured hang the sidecar's last log line is the auxiliary-tier `understand_query` completion, and the `No modules.json found for cross-encoder/...` line that a successful load prints never appears at all, so the block is inside that construction. Two properties make it unrecoverable: the load is unbounded, and `asyncio.to_thread` cannot be cancelled, so Slice 1's timeout frees the *turn* but leaves the thread running — the load itself has to be bounded here.

**Decision (user, 2026-08-05): scope cut to bound-and-degrade.** `main.py`'s `llm` / `search` / `embeddings` / `reranker` / `voice` readiness capabilities are all initialized `"pending"` and **none are ever updated anywhere in the codebase** — there is no existing pattern for a lazy capability reporting into `Readiness`, so wiring `reranker` alone would invent a one-off mechanism the other four still wouldn't follow. Out of scope here; `reranker` stays `"pending"` for the life of the process, same as today. This slice only bounds the load and degrades the search response, reusing `search_papers`'s existing "one source failing degrades, never blocks" pattern (`_fan_out`, `backend/search/__init__.py`) for the reranker instead of inventing a second one.

**Depends on:** 1

**Touches:** `Search Federation` (`backend/search/`) — `reranker.rerank`, `_get_model`, and `search_papers`'s degrade-to-unranked path.

### Slice 3: The composer says why it will not send

**What this delivers:** Typing a message while a turn is running produces a visible response — the message is queued or refused with a stated reason and the `Stop` affordance — instead of vanishing with no bubble, no error, and no change on screen.

Root cause: `sendMessage` opens with `if (turnInFlight || !text.trim()) return;` (`frontend/src/companion/CompanionPane.tsx:196`), a silent early return that renders nothing. The backend already broadcasts a `turn_in_progress` `ErrorEvent` for exactly this case (`backend/ws/__init__.py`'s `handle_message`) and the pane already renders `error` transcript entries (`:119–122`), but the guard means the send never leaves the client, so that error is never triggered. This is a separate defect from Slice 1 and survives it: even with tool calls bounded, a user typing during a normal 90-second turn gets the same silent drop.

**Depends on:** 1

**Touches:** `Companion Pane` (`frontend/src/companion/`) — `sendMessage`, `handleSubmit`, the composer's disabled/queued state and `CompanionPane.css`.

## Phase 2: Make paper extraction complete

### Slice 4: A rate-limited request retries fitted to the provider's real ceiling

**What this delivers:** Extraction of a full-length paper completes on a small free-tier model — a request rejected with `Limit 6000, Requested 14327` is refitted to the ceiling named in that error and retried, and the learned ceiling is persisted per model so the next call is sized correctly on the first attempt.

Root cause: the Phase 1.1 budget guard is inert in practice and mis-keyed in principle. `_fit_to_budget` returns immediately when `budget is None` (`backend/llm/__init__.py:164–165`), and `providers->'groq'->>'request_token_budget'` is NULL in `api_keys` — nothing ever set it, so the 14,327-token request went out unbounded. Even set, it could not work: `request_token_budget` lives on `ProviderCredentials` per *provider* (`backend/settings/models.py`), while `primary_model = groq/openai/gpt-oss-20b` and `auxiliary_model = groq/llama-3.1-8b-instant` share the `groq` provider with different TPM limits, so one value cannot serve both tiers. The provider states its true limit in the 429 body, which makes the ceiling derivable rather than something the user must know.

**Decision (user, 2026-08-05): `providers[provider].request_token_budget` (one number) is replaced by
`providers[provider].model_budgets: dict[str, int]`, keyed by the full model string** (e.g.
`"groq/llama-3.1-8b-instant": 6000`) — nested inside the existing JSONB `providers` column, no new
table, no Alembic migration. `_resolve_tier` looks up `model_budgets[model]` instead of the flat
field. `ProviderCredentials`/`ProviderInfo` (`backend/settings/models.py`) and Schema.md's
`api_keys.providers` business-meaning prose both need updating to match.

**Depends on:** none

**Touches:** `LLM Gateway` (`backend/llm/`) — `_fit_to_budget`, `_resolve_tier`, `complete`, `complete_structured`, and the `RateLimitError` retry path. `Settings Store` (`backend/settings/`) — `model_budgets` replacing the per-provider `request_token_budget` on `ProviderCredentials`/`ProviderInfo`; `api_keys.providers` shape (documented in `Schema.md`, no migration).

### Slice 5: The learned token budget is visible and overridable

**What this delivers:** The Settings panel shows the per-model request-token ceiling currently in force for the primary and auxiliary models, whether it was learned from a provider error or set by hand, and lets it be edited — so a known free-tier limit can be entered before the first failure rather than after it.

Root cause: `request_token_budget` is accepted by the settings endpoint and present in the generated client (`packages/api-client/src/client/types.gen.ts`), but no UI in `frontend/src/settings/` or `frontend/src/onboarding/` ever writes it, so the only value the field can hold is the one Slice 4 learns. Surfacing it also makes Slice 4's inference auditable — a wrong learned ceiling is otherwise invisible and indistinguishable from a model that simply cannot do the task. Reads and writes the `model_budgets[model]` shape Slice 4 establishes, keyed by whichever model string is currently set as primary/auxiliary.

**Depends on:** 4

**Touches:** `Settings Panel` (`frontend/src/settings/`) — the Models section alongside the existing primary/auxiliary model controls. `REST API` (`backend/api/`) — the settings read/write endpoints carrying the per-model budget. `Settings Store` (`backend/settings/`) — `get_settings`, the budget writer; `api_keys` table.

### Slice 6: A small model's malformed extraction is repaired instead of discarding the paper

**What this delivers:** An extraction window whose structured output does not match `_ExtractedCard` is repaired or retried once before being skipped, so a paper is only marked `Extract: Failed` when every window genuinely produced nothing usable — closing the second failure mode seen on the same retry, where the model returned bare strings for object fields and echoed prompt text back as values.

Root cause: `groq/llama-3.1-8b-instant` rejects the nested `_ExtractedSpan` shape with `tool call validation failed: ... /problem: expected object, but got string`, and its `failed_generation` shows it flattened every field to a string and filled them with fragments of the instructions rather than paper text. `extract_card_job` treats any `LLMError` as a skipped window (`backend/papers/__init__.py`), so with the schema mismatch hitting every window the job raises `every extraction window failed` and the paper is left permanently degraded. A model this size needs either a flatter schema it can satisfy or one repair attempt, not a per-window skip that adds up to total loss. The `logger.warning` added during QA is what makes the failure visible; this slice is what makes it survivable.

**Depends on:** 4

**Touches:** `Paper Pipeline` (`backend/papers/`) — `extract_card_job`, `_ExtractedCard`, `_ExtractedSpan`, `_EXTRACTION_PROMPT`, `_MAX_EXTRACTION_CHARS`; `papers.extract_state`, `paper_cards`, `quote_anchors` tables. `LLM Gateway` (`backend/llm/`) — `complete_structured`'s schema-violation handling. `Provenance` (`backend/provenance/`) — `validate_and_anchor` over the repaired spans.

## Phase 3: Trustworthy citation rendering

### Slice 7: A malformed citation tag never reaches the transcript as raw markup

**What this delivers:** An `⚠ unverified` block always contains clean quoted text, never literal `<cite>…</cite>` markup rendered as visible characters, for every shape of tag the model emits — including a tag it wrapped in its own quote marks and a tag nested inside another.

Root cause: `_CITE_OR_QUOTE_PATTERN` (`backend/harness/__init__.py:75–83`) matches `<cite>(?P<cite>.*?)</cite>` non-greedily beside two quote-span alternatives with a `_MIN_UNTAGGED_QUOTE_CHARS = 20` floor. When the model emits a doubled or quote-wrapped tag, a captured span can itself still contain literal tag markup. `_validate_citations` then fails that span's substring check — it is not verbatim paper text — and re-wraps the whole thing as `<unverified>…</unverified>`, producing a real tag nested inside a real tag. `renderAssistantContent` splits on the outermost match and deliberately does not recurse into an already-matched span (`frontend/src/companion/parseCitations.tsx`), so the inner tag prints verbatim. The span has to be cleaned of tag markup before it is re-wrapped, on the backend, where `messages.citations` is written.

**Depends on:** none

**Touches:** `Agent Harness` (`backend/harness/`) — `_CITE_OR_QUOTE_PATTERN`, `_validate_citations`; `messages.citations`. `Companion Pane` (`frontend/src/companion/`) — `parseCitations.tsx`'s handling of a span that still contains markup.

## Phase 4: Writing and notebook ergonomics

### Slice 8: A new draft shows an empty state instead of a compiler error

**What this delivers:** Creating a draft and typing nothing shows a quiet "nothing to preview yet" panel; the preview compiles as soon as the document has real body content — replacing the `error: the xdvipdfmx engine had an unrecoverable error` / `No pages of output.` stack that every new draft currently opens with.

Root cause: the compile guard tests the wrong emptiness. `useManuscriptPreview.ts:32` skips only when `tex.trim() === ""`, but a new draft is seeded with `STARTER_TEX = "\\documentclass{article}\n\\begin{document}\n\n\\end{document}\n"` (`frontend/src/writing/ManuscriptTab.tsx:22`) — a non-empty string with an empty *body*. It therefore compiles, Tectonic produces zero pages, and that is a fatal condition rather than a benign one. The guard needs to test the document body between `\begin{document}` and `\end{document}`, not the source string.

**Depends on:** none

**Touches:** `Manuscript Editor` (`frontend/src/writing/`) — `useManuscriptPreview.ts`'s compile guard, `ManuscriptTab.tsx`'s `STARTER_TEX` and preview panel, `ManuscriptTab.css`.

### Slice 9: The live notebook is usable at the default pane width

**What this delivers:** Opening an experiment's notebook with the nav and Companion panes at their default widths shows a usable notebook — kernel picker, toolbar and cell gutter reachable without horizontal scrolling — instead of a ~200px column that takes several scrolls to run one cell.

Root cause: `LiveNotebookPanel` embeds the real Jupyter UI in an `<iframe>` (`frontend/src/experiments/LiveNotebookPanel.tsx`) sized by whatever the experiments column happens to be, with no minimum width and no accommodation for the fact that the framed application has its own irreducible layout. The centre pane is now user-resizable (Phase 3.1), so the fix is a floor on the notebook's own width plus a scroll/expand affordance, not a fixed pane width.

**Depends on:** none

**Touches:** `Experiments Board` (`frontend/src/experiments/`) — `LiveNotebookPanel.tsx` and its stylesheet. `App Shell` (`frontend/src/app/`) — the centre-pane minimum width the notebook needs from `AppShell.css`.

### Slice 10: The notebook iframe loads with a clean console

**What this delivers:** Opening an experiment's notebook produces no `TypeError: Cannot read properties of undefined (reading 'schema')`, no `[yjs#509] Not same Y.Doc`, and no duplicated menu-entry warnings — or, if the cause is proven to be upstream and unfixable at a pinned version, a recorded finding in this file and a pinned version that is known-good.

Root cause: not yet established, and it lives in JupyterLab's own bundle inside the iframe, so it is bounded by what the image and its config can change. The strongest available lead is that `docker/experiment-base.Dockerfile:24` installs `notebook` completely unpinned, so the image silently takes whatever release is current at build time and the Jupyter version is not reproducible between rebuilds. Duplicated command registrations (`filemenu:close-and-cleanup`, `debugger:show-panel`, `toc:show-panel`) and `Not same Y.Doc` are both classic symptoms of an extension set being registered twice, which a `notebook`/`jupyterlab` version pairing can cause. A second, independent inconsistency worth resolving in the same pass: `jupyterlab_overrides.json` is installed to `/usr/local/share/jupyter/lab/settings/overrides.json` (`:36`) while the server runs the Notebook 7 application, which reads its own settings directory. Pinning the versions is the deliverable even if the console noise proves to be upstream.

**Finding (implementation, 2026-08-05):** both leads investigated against the real image (`docker exec` into the running `research-os-experiment-base` container, plus a fresh `docker build`), not guessed.

- **Lead (b) — the overrides path — is not a bug.** `jupyter lab path` inside the container reports `Application directory: /usr/local/share/jupyter/lab`, byte-for-byte the same path the Dockerfile installs `overrides.json` under. Notebook 7 runs *as* a JupyterLab application (`notebook` depends on `jupyterlab`; the settings directory is JupyterLab's own, computed from `<sys-prefix>/share/jupyter/lab` per JupyterLab's own "Advanced Usage" docs — https://jupyterlab.readthedocs.io/en/stable/user/directories.html), so there is no separate Notebook-7-era path it silently ignores. The override file was already being read from the right place; nothing here was changed.
- **Lead (a) — the unpinned install — was real, and is now fixed.** An unpinned `pip install notebook` today (2026-08-05) resolves to `notebook==7.6.1`, which itself pins `jupyterlab<4.7,>=4.6.2` and `jupyter-server<3,>=2.19.0` — so the running image was already landing on `jupyterlab==4.6.2` / `jupyter-server==2.20.0`, confirmed with `pip show` in the live container. These are simultaneously the *newest* versions on PyPI today and the newest versions `notebook==7.6.1`'s own constraints permit — there is no older "known-good" triple to fall back to; latest already is the most-patched option. All three are now pinned explicitly in `docker/experiment-base.Dockerfile` so the image is reproducible across rebuilds regardless of what's current on PyPI later.
- **The three console symptoms trace to two distinct, cited upstream mechanisms, neither fixable by a version pin alone:**
  - The duplicated menu-entry / command-registration warnings match jupyterlab/jupyterlab#16113 ("a plugin should only notify the commands it registered"), fixed by PR #16273 (`notifyCommandChanged` now checks the command is registered first) and shipped starting in the 4.2.x milestone. `jupyterlab==4.6.2` already contains that fix. The PR author's own words — "Still not sure it's the 'right' fix, but maybe it can already help limit the amount of errors seen in the dev tools console" — describe it as a mitigation, not a structural fix, which matches residual duplicate-registration noise still being plausible at the current version.
  - `[yjs#509] Not same Y.Doc` is not JupyterLab-specific: it is Yjs's own `UndoManager` sanity check (github.com/yjs/yjs, issue #509), which logs this exact string when it is handed `Y.Type`s that don't all belong to the same `Y.Doc`. JupyterLab 4's document/undo architecture is Y.Doc-based per open document even with no collaboration extension installed (confirmed: `jupyter-collaboration`/`pycrdt`/`y-py` are absent from the pinned image's dependency set), so this fires from inside JupyterLab's own document-lifecycle code, not from anything this Dockerfile or `jupyter_server_config.py` controls.
  - The exact `TypeError: Cannot read properties of undefined (reading 'schema')` string could not be tied to a specific, citable upstream issue number despite a genuine search of JupyterLab's issue tracker and the Jupyter Discourse; the closest matches are SettingRegistry schema-loading errors (jupyterlab's own `packages/settingregistry/src/settingregistry.ts`), consistent with a plugin's settings schema not being resolved yet at the moment something reads it, but this is inference, not a confirmed match, and is reported as such rather than presented as a fix.
- **Frontend ruled out as a contributor.** `LiveNotebookPanel.tsx`'s start/stop effect already carries the `cancelled` guard this codebase used to fix the exact same category of bug in `useProjectSocket.ts` (commit 838c3dc, "fix StrictMode double-connect race"), and `backend/sandbox/start_notebook_server`/`stop_notebook_server` additionally serialize on `_get_live_server_lock` per experiment specifically to prevent "React StrictMode's dev-only double-mount ... produc[ing] two containers for one experiment" (that function's own docstring). Tracing the actual StrictMode sequence through that lock: the throwaway first mount's `start` and `stop` calls fully resolve (creating and tearing down one throwaway container) before the surviving mount's `start` call acquires the lock, so only one container's URL is ever set into React state and only one iframe navigation ever happens. There is no double `src` assignment for the browser to load, so this is not a plausible source of a same-document Y.Doc mismatch inside the iframe. No frontend change was made.
- **Net result:** the version pin is shipped (reproducibility, and it already carries the one upstream fix that exists for one of the three symptoms). The remaining console noise is assessed as upstream, not fixable at the application layer without patching JupyterLab's own bundled JS, and is recorded here rather than papered over with an unverified change.

**Depends on:** 9

**Touches:** `Execution Sandbox` (`backend/sandbox/`) — `docker/experiment-base.Dockerfile`'s Jupyter pins, `docker/jupyter_server_config.py`, `docker/jupyterlab_overrides.json`. `Experiments Board` (`frontend/src/experiments/`) — `LiveNotebookPanel.tsx`'s iframe mount, if a duplicate mount turns out to contribute.

## Phase 5: Polish

### Slice 11: The Notes save control explains itself

**What this delivers:** A note with body text but no title shows why it cannot be saved yet, and a saved note is visibly distinguished from one with unsaved edits — instead of a Save button that is disabled for an unstated reason and looks identical before and after a successful save.

Root cause: `disabled={saving || !title}` (`frontend/src/notes/`, the save button) encodes the title requirement with no accompanying copy, and no dirty-state is tracked at all, so the control's appearance is the same whether the note is unchanged, edited, or just written.

**Depends on:** none

**Touches:** `Notes Editor` (`frontend/src/notes/`) — the editor's save control, its dirty-state tracking and stylesheet.

### Slice 12: The tab overflow menu lists only the tabs that are hidden

**What this delivers:** The `»` menu lists exactly the tabs scrolled out of view, so it answers "what can't I see" instead of duplicating the whole strip.

Root cause: the overflow menu renders `tabs.map(...)` over every open tab (`frontend/src/app/AppShell.tsx`), while the `tabBarOverflowing` flag that shows the control already proves the strip is measuring its own scroll extent — the per-tab visibility that measurement implies is simply not used to filter the list.

**Depends on:** none

**Touches:** `App Shell` (`frontend/src/app/`) — the tab strip's overflow menu and `tabBarOverflowing` measurement in `AppShell.tsx`, `AppShell.css`.

### Slice 13: The Dashboard marks the tab you are actually on

**What this delivers:** "Continue where you left off" visually distinguishes the currently-active tab from the rest, so the list reads as resume state rather than an undifferentiated copy of the tab strip.

Root cause: every row renders with the same `dashboard__resume-bullet` / `dashboard__resume-title` treatment (`frontend/src/dashboard/Dashboard.tsx:106–111`) with no active variant, even though the active tab id is already known to the shell that renders this view.

**Depends on:** none

**Touches:** `Dashboard` (`frontend/src/dashboard/`) — `Dashboard.tsx` resume rows, `Dashboard.css`. `Client State` (`frontend/src/state/`) — the active-tab id passed into the Dashboard.

### Slice 14: The Feed's new-item count is proven not to double-count

**What this delivers:** A regression test that fails if a catch-up poll counts or stores the same arXiv item twice across overlapping windows, and whatever fix that test demands — settling whether the Dashboard's "226 new since Wed" reflects 226 distinct papers.

Root cause: not established, and possibly not a defect — a 7-category, 50-keyword interest profile plausibly yields that volume, and a Feed tab opened during QA rendered with no visible duplicates. What is missing is the evidence either way: `poll_feed_job` runs on catch-up-on-launch (D9), which means overlapping windows across restarts are its normal operating condition, and `tests/` covers feed *ranking* but not window overlap. This follows Rules.md's policy of automated coverage where correctness is invisible to the eye.

**Depends on:** none

**Touches:** `Research Feed` (`backend/feed/`) — `poll_feed_job`'s window bounds and dedup; `feed_items` table. `REST API` (`backend/api/`) — the dashboard counts endpoint. `tests/` — a new window-overlap test beside `test_feed_ranking.py`.
