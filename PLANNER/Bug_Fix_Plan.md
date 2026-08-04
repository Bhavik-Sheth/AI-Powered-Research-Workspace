# Bug Fix Plan

## Phase 1: Restore the evidence pipeline

### Slice 1: A long extraction request survives the model's token limit

**What this delivers:** A single completion request larger than the configured provider's per-request token allowance is split or throttled instead of failing, and a transient `429` is retried with backoff rather than surfacing as a terminal error — verifiable by issuing the 54,139-character extraction call that currently raises `litellm.RateLimitError: Limit 8000, Requested 12510` and seeing it return a parsed result.

Root cause: `complete_structured` passes whatever it is handed straight to `litellm.acompletion`, with no knowledge of the active model's context or tokens-per-minute ceiling and no retry on `RateLimitError`. Every caller that sends a document-sized payload therefore fails on the free tier of the configured provider (`groq/openai/gpt-oss-20b`, TPM 8000). The gateway is the only place that knows which model is in play, so the budget belongs here, not in each caller.

**Depends on:** none

**Touches:** `LLM Gateway` (`backend/llm/`) — `complete`, `complete_structured`, `_resolve_tier`; adds a per-tier token budget and a rate-limit retry policy. `Settings Store` (`backend/settings/`) — the per-provider request-budget value read by `_resolve_tier`; `api_keys` table.

### Slice 2: Opening a paper shows real extracted quotes instead of "not stated"

**What this delivers:** `extract_card_job` completes for a full-length paper and writes `paper_cards` rows, so the reader's extractive card renders verbatim Problem / Method / Datasets / Results / Limitations spans with their `§section · start–end` mono line, instead of five `not stated in this paper` fields.

Root cause: `extract_card_job` sends `content.full_text[:60_000]` as one user message (`backend/papers/__init__.py:313`). That single call is what Slice 1 makes survivable, but the deeper fix is that extraction should run over bounded, section-aware windows rather than one whole-paper blob — the five fields are stated in identifiable sections, and a windowed pass both fits the budget and improves quote fidelity. Because `write_llm_edges` and the `paper_cards` writes both sit inside the same `try` block, one failed call currently loses the card *and* every LLM-derived graph edge for that paper.

**Depends on:** 1

**Touches:** `Paper Pipeline` (`backend/papers/`) — `extract_card_job`, `_EXTRACTION_PROMPT`, `_MAX_EXTRACTION_CHARS`; `papers.extract_state`, `paper_cards`, `quote_anchors` tables. `Provenance` (`backend/provenance/`) — `validate_and_anchor` per window. `Knowledge Graph` (`backend/graph/`) — `write_llm_edges`, `idea_edges` table.

### Slice 3: A failed or stalled paper can be re-driven from the Library

**What this delivers:** A paper whose pipeline failed or stalled shows its true per-stage state in the Library with a `Retry` action that re-enqueues only the incomplete stages, so the four papers currently at `extract_state = failed` and the one stuck at `fetch_state = done` / `parse_state = queued` can be recovered without deleting and re-adding them.

Root cause: stage state is terminal. `_set_extract_state(pid, "failed")` writes `failed` and nothing in the API or UI can move it back to `queued`, so a paper that lost one LLM call is permanently degraded. The stall (`fetch done` → `parse queued` with no running job) shows the same gap from the other side: a job that never ran leaves the row indistinguishable from one that was never started. This slice also removes the tracer fixtures that a build-phase live test left in `papers` (`arxiv:tracer-bc607d`, `arxiv:tracer2-2e4111` — no `pdf_origin`, every stage `queued`), which currently render as real, permanently-unprocessable library entries and as duplicate titles beside the genuine rows.

**Depends on:** 2

**Touches:** `Paper Pipeline` (`backend/papers/`) — a re-drive entry point over `fetch_state` / `parse_state` / `embed_state` / `extract_state`. `REST API` (`backend/api/`) — `POST /api/projects/{project_id}/papers/{paper_id}/reprocess`. `Job Queue` (`backend/jobs/`) — re-enqueue of the incomplete stages. `Library View` (`frontend/src/library/`) — per-stage status and the retry control.

## Phase 2: Trustworthy Companion answers

### Slice 4: The Companion answers from the open paper's actual text

**What this delivers:** Asking "quote the core idea from each open paper" returns real verbatim quotes from those papers, rather than the current "I don't have the exact text of the abstracts for those papers available right now."

Root cause: the turn's context is assembled at `backend/harness/__init__.py:255–265` from the system prompt, the highlighted selection, open-paper **titles only** (`_format_open_papers` emits titles and ids, never content), optional memory rows, and history. The paper body is never injected. The one path that could supply content — `_maybe_retrieve` — is gated behind an auxiliary-tier LLM call that returns `[]` on any exception (`:111–112`), which the same rate limit as Slice 1 triggers routinely. So the model is instructed to cite verbatim while holding nothing to cite from, and either declines or invents. Evidence for the selected and open papers must be assembled deterministically, not decided by a model call that fails open to silence.

**Depends on:** 2

**Touches:** `Agent Harness` (`backend/harness/`) — `run_turn` context assembly, `_maybe_retrieve`, `_format_open_papers`. `Memory Index` (`backend/memory/`) — `query_memory` over `paper_chunks` for the read set. `Provenance` (`backend/provenance/`) — anchors for the injected spans.

### Slice 5: An unsupported claim renders as `⚠ unverified` instead of as prose

**What this delivers:** A model answer that asserts a fact about a paper without a validated span is shown with the `⚠ unverified` badge, and an answer with no evidence available says so rather than answering from training knowledge — closing the gap where the Companion produced fabricated quotation-marked text (e.g. "The dominant paradigm for modeling sequences is to use recurrent or convolutional neural networks.") that appears nowhere in the paper and carried no badge.

Root cause: `_validate_citations` (`backend/harness/__init__.py:167–198`) iterates `_CITE_PATTERN`, which matches only `<cite>…</cite>`. Text the model never wrapped is copied through untouched. Citation discipline is therefore opt-in by the model, and `_SYSTEM_PROMPT`'s "Make no factual claim from a source without a supporting quote" is advisory — a 20B model routinely ignores it. D24's guarantee holds only for spans the model volunteered, which is precisely the case where fabrication does not occur. The validator needs to police untagged assertions, not just tagged ones.

**Depends on:** 4

**Touches:** `Agent Harness` (`backend/harness/`) — `_validate_citations`, `_CITE_PATTERN`, `_SYSTEM_PROMPT`; `messages.citations`. `Provenance` (`backend/provenance/`) — `validate_and_anchor`. `Companion Pane` (`frontend/src/companion/`) — `parseCitations.tsx` rendering of the `<unverified>` branch.

### Slice 6: The Companion can act, not only answer

**What this delivers:** Asking the Companion to open a paper, run a search, or add a note performs that action through a tool call and produces the same route transition the user's own click would, with tool results reaching the UI by id and the loop terminating at its iteration cap.

Root cause: no agent loop exists. `run_turn` makes exactly one `complete()` call and emits `iterations=1` as a literal at every exit (`:279`, `:283`, `:310`). `backend/llm/__init__.py:149` accepts a `tools=` argument that the harness never passes, and the `messages.role` check constraint already admits `tool_call` and `tool_result` values that nothing ever writes. D17–D19's 7-node runtime, the tool catalog, and dual-channel tool results are unbuilt, which makes US7's "anything the user can click, the Companion can do" unreachable and is the structural reason the Companion cannot fetch its own evidence.

**Depends on:** 5

**Touches:** `Agent Harness` (`backend/harness/`) — the iteration loop, tool dispatch, the tool catalog, `messages` rows of role `tool_call` / `tool_result`. `LLM Gateway` (`backend/llm/`) — the existing `tools=` pass-through in `complete`. `Session Transport` (`backend/ws/`) — tool-result events by id. `Companion Pane` (`frontend/src/companion/`) — tool-result rendering. `Client State` (`frontend/src/state/`) — route transitions driven by tool results.

## Phase 3: Workspace ergonomics

### Slice 7: Panes resize by dragging their edges

**What this delivers:** The left nav, center pane and Companion are resized by dragging the divider between them, the widths persist across a restart, and each pane has a sensible minimum — replacing the current all-or-nothing collapse.

Root cause: no resize implementation exists. `frontend/src/app/AppShell.tsx` and `AppShell.css` contain no resizer, splitter, drag handler or pointer-event code; the nav is a fixed `width: 200px` and the Companion a fixed `width: 280px`, and the only affordance is a collapse toggle that animates the width to `0`. A user who wants a wider reader has no option but to hide a whole pane.

**Depends on:** none

**Touches:** `App Shell` (`frontend/src/app/`) — `AppShell.tsx`, `AppShell.css` column sizing and the new drag handles. `Client State` (`frontend/src/state/`) — persisted pane widths.

### Slice 8: Tabs reorder by dragging and overflow into a menu

**What this delivers:** Dragging a tab moves it within the strip, and once the strip overflows an overflow control lists the hidden tabs — currently dragging a tab only activates the tab it is dropped on, because the drag degrades to a click.

Root cause: the tab strip renders tabs as plain click targets with no `draggable` attribute and no drag handlers anywhere in `frontend/src/app/`, and overflow is handled solely by horizontal scrolling of the strip.

**Depends on:** 7

**Touches:** `App Shell` (`frontend/src/app/`) — the tab strip component and its CSS. `Client State` (`frontend/src/state/`) — `useTabStack` tab ordering.

### Slice 9: Companion replies render formatted text

**What this delivers:** An assistant reply containing `**International Conference on Learning Representations**` renders bold rather than showing literal asterisks, with the `<cite>` and `<unverified>` treatments intact.

Root cause: no markdown renderer is present — the frontend has no markdown dependency, and `parseCitations.tsx` splits the reply on the citation tags and emits the remaining pieces as raw text nodes. Markdown rendering has to compose with that tag splitting rather than replace it, so that formatting never swallows or re-orders an evidence span.

**Depends on:** none

**Touches:** `Companion Pane` (`frontend/src/companion/`) — `parseCitations.tsx`, `CompanionPane.tsx`, `CompanionPane.css`.

### Slice 10: One project session survives a reconnect without duplicating a turn

**What this delivers:** Loading the app opens exactly one live session, the console no longer logs `WebSocket is closed before the connection is established`, and a message is never recorded twice in the transcript — the current transcript shows the same "Compare Attention Is All You Need and Efficient Streaming Language Models…" user message stored back to back.

Root cause: `backend/ws/` keeps one connection per `project_id` in `_sessions`, "unconditionally overwritten by whoever connects last". `useProjectSocket` opens its socket in an effect, and React `StrictMode` (`frontend/src/main.tsx`) mounts that effect twice in development, so the second socket evicts the first, the evicted socket's `onclose` fires, and the hook's backoff reconnects into the same race — visible as the repeated `connection open` lines in the sidecar log. Any send that straddles the eviction can be delivered twice.

**Depends on:** none

**Touches:** `Session Transport` (`backend/ws/`) — `_sessions` registration and eviction. `Client State` (`frontend/src/state/`) — `useProjectSocket.ts` connect/reconnect lifecycle. `Agent Harness` (`backend/harness/`) — turn de-duplication against `messages.turn_id`.

## Phase 4: Completing specified surfaces

### Slice 11: The manuscript preview compiles

**What this delivers:** Editing a draft produces a rendered PDF preview, replacing the permanent `I can't find the format file 'swiftlatexpdftex.fmt'!` error that every document currently shows.

Root cause: the preview can never have worked as provisioned. `frontend/scripts/fetch-swiftlatex.mjs` downloads the upstream release zip and extracts `PdfTeXEngine.js`, `swiftlatexpdftex.js` and `swiftlatexpdftex.wasm` — and that archive contains **no `.fmt` file at all** (its nine entries are the pdftex, xetex and dvipdfm engines only). The engine expects to resolve `swiftlatexpdftex.fmt` and every `\usepackage` target at runtime from SwiftLaTeX's hosted texlive endpoint, so even a working preview would be a network dependency on a third-party service.

**Decision (user, 2026-08-04): the preview must work offline.** The compile resolves entirely against local assets — no request to `texlive2.swiftlatex.com` or any other host at compile time. This is a stricter requirement than PRD.md US12 states and supersedes it; US12's Tectonic-in-Docker keeps its specified role as the escape hatch for final compiles needing full package coverage.

Two provisioning routes exist and the slice must establish which one holds before building on it: vendor the format file and a bounded texlive resource set as static assets under `frontend/public/`, or dump the format from the pdftex WASM engine itself as a build step. Tectonic-in-Docker with a **local** bundle is the fallback that is known to be offline-capable — take it if neither route yields a working `.fmt`, since an offline preview via the escape hatch honours the decision above where a networked SwiftLaTeX preview would not.

**Depends on:** none

**Touches:** `Manuscript Editor` (`frontend/src/writing/`) — `useSwiftLatexPreview.ts`, the compile-error panel. `frontend/scripts/fetch-swiftlatex.mjs` and `frontend/public/`. `Execution Sandbox` (`backend/sandbox/`) — the Tectonic path in `docker/tectonic.Dockerfile`, if taken.

### Slice 12: A citation check reports its result even when nothing is wrong

**What this delivers:** `Check citations` always states an outcome — the findings list, or an explicit "no issues found" — instead of appearing to do nothing on a document with no problems.

Root cause: the feature works end to end. `backend/writing/check_citations` runs the checks and persists to `documents.citation_findings`, and `ManuscriptTab.checkCitations` calls it and sets both `findings` and the editor decorations. On a draft with no `\cite` commands the check correctly returns zero findings, and with no empty state the UI renders nothing at all, which reads as a dead button. The defect is the missing result state, not the check.

**Depends on:** none

**Touches:** `Manuscript Editor` (`frontend/src/writing/`) — `ManuscriptTab.tsx` findings panel, `citationDecorations.ts`. `Manuscript` (`backend/writing/`) — `check_citations`; `documents.citation_findings`.

### Slice 13: The Dashboard reports what needs attention

**What this delivers:** The Dashboard shows all four stat tiles with their actionable qualifier and a `NEEDS ATTENTION` section surfacing failed and stalled work — which is where the four `extract_state = failed` papers should have been visible without a database query.

Root cause: unbuilt. `frontend/src/dashboard/Dashboard.tsx` fetches only papers and notes and renders two bare-count tiles; there is no experiments tile, no feed tile, no qualifier text, and no needs-attention region.

**Depends on:** 3

**Touches:** `Dashboard` (`frontend/src/dashboard/`) — `Dashboard.tsx`, `Dashboard.css`. `REST API` (`backend/api/`) — the per-project counts and degraded-item summary.

### Slice 14: Model and key settings are reachable after onboarding

**What this delivers:** The Settings panel exposes the provider key, primary model and auxiliary model alongside the existing interest profile, so the configuration made during onboarding can be changed without re-running the wizard.

Root cause: `frontend/src/settings/` renders only `InterestProfileForm`. The onboarding wizard is the sole writer of provider settings, which is why `api_keys.auxiliary_model` is currently `NULL` with no in-app way to set it — the auxiliary tier silently falls back to the primary model for every extraction and memory-decision call.

**Depends on:** 1

**Touches:** `Settings Panel` (`frontend/src/settings/`) — the panel and its sections. `Settings Store` (`backend/settings/`) — `get_settings`, the model-string writers; `api_keys` table. `REST API` (`backend/api/`) — the settings endpoints.

### Slice 15: The interface renders in its own typefaces

**What this delivers:** The app renders in Space Grotesk, Newsreader and JetBrains Mono, replacing the system-font fallback that currently makes every screen read as generic.

Root cause: the three families are declared in `frontend/src/design/tokens.css` with system fallbacks and a note that "self-hosted font files are not yet vendored"; no font file is served and no `@font-face` rule exists, so every stack resolves to its fallback. The rest of the design system — tokens, panels, the cyan accent — is applied and working.

**Depends on:** none

**Touches:** `Design Tokens` (`frontend/src/design/`) — `tokens.css` `@font-face` declarations and the vendored font files under `frontend/public/`.
