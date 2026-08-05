# Research Companion OS — QA Issues

Tested live via Playwright against `http://localhost:5173/` (frontend) and a locally-run backend
sidecar, project "Attention Sinks: A Study", 2026-08-05, after Bug_Fix_Plan.md's 15 slices
(Phases 1–4 in the git log). Goal: find bugs, fix what's small and safe to fix directly, and log
everything bigger here.

**Four minor issues were fixed directly in this pass** (see bottom of this file). Everything below
is unfixed and needs a real decision or a non-trivial change.

## 1. Critical: a hung tool call wedges the Companion with no way out

**Reproduce:** open a paper, ask the Companion a question that makes it call a tool (e.g. "Quote
one sentence from the training section, with a citation" triggered a `search_papers` call even
though the answer was sitting in the already-open paper). Watch the status pill.

**What happens:** the status pill reads `search_papers…` and just stays there — confirmed stuck for
4+ minutes with **zero** further backend log activity (no LiteLLM call, no HTTP call, nothing but
`/api/health` polling). Clicking **✕ Stop** does nothing: the pill doesn't change, and the backend
log shows no `interrupt` event was ever acted on. Typing a new message afterwards is silently
swallowed — no user bubble appears, no error, nothing. The only recovery is restarting the backend
process, which loses the turn.

**Root cause:** `harness/__init__.py`'s turn loop only checks `cancel_flag.is_set()` *inside* the
streaming-completion loop (between chunks, around line 411). Once the model requests a tool call,
the code moves on to `result = await dispatch_tool(...)` (line 456) with **no cancel check and no
timeout** around it. If that awaited call never returns, the turn — and the whole session, since a
new turn can't begin until `begin_turn`'s lock is released — is stuck forever. `search_papers`
itself has per-source `httpx` timeouts (15s, `search/sources.py`), so the hang is most likely inside
`search/reranker.py`'s cross-encoder scoring or `query_understanding.py`, neither of which is wrapped
in a timeout.

**Why it matters:** this is the single most user-visible thing wrong with the app right now — it
silently and permanently breaks the core feature (the Companion) mid-conversation, with no error,
no way to cancel, and no way to recover short of a backend restart. It also means Slice 6/10's
"Stop is real" and "one session, no duplicate turns" guarantees don't hold once a tool call is
involved.

**Suggested fix direction:** wrap `dispatch_tool` (and whatever `search_papers` calls into,
particularly the reranker) in `asyncio.wait_for` with a real timeout, and race it against
`cancel_flag` the same way the streaming loop does — a tool call needs the identical two-sided
"can be cancelled, can time out" guarantee `_COMPLETION_TIMEOUT_S` already gives the primary
completion.

## 2. Paper extraction still fails routinely for the model actually configured

**Reproduce:** Papers → any paper showing `Extract: Failed` → Retry.

**What happens:** 3 of 4 papers in this project are permanently stuck at `Extract: Failed`. Retry
re-enqueues the job (confirmed working) but it fails again, every time, for two different reasons
observed in the same run:

- `litellm.RateLimitError`: `Limit 6000, Requested 14327` — a single extraction window request is
  more than double the auxiliary model's actual per-minute token budget.
- `litellm.BadRequestError`: `tool call validation failed... expected object, but got string` — the
  model returned strings for fields the schema requires as an object or `null`.

**Root cause:** the auxiliary model configured in Settings (visible on the Settings page) is
`groq/llama-3.1-8b-instant`, a much smaller/cheaper model than the `groq/openai/gpt-oss-20b`
primary — with a **6000 TPM** limit, not the 8000 TPM the Bug Fix Plan's Slice 1 budgeting was
written against. Phase 1.2's 60,000-char section windows (`_MAX_EXTRACTION_CHARS`,
`backend/papers/__init__.py`) are far too large for this model's real budget, and being a smaller
model it also doesn't reliably follow the structured-output schema `_ExtractedCard` requires.
Neither failure mode was visible before this session — `extract_card_job` swallowed the exception
completely silently (fixed below, but only to add logging, not to fix the underlying failure).

**Suggested fix direction:** either size `_MAX_EXTRACTION_CHARS` (and the retry/backoff policy) per
the *actual* configured model's real TPM rather than a single constant, or don't let `auxiliary_model`
be set to a model this small for a task this token-heavy — surface a warning in Settings when the
chosen auxiliary model's budget can't fit a single extraction window.

## 3. Malformed `<cite>` tags leak into the Companion transcript as raw text

**Reproduce:** ask the Companion to summarize a paper with citations a few times — reproduced live
in this session's transcript.

**What happens:** an `⚠ unverified` block sometimes contains literal, unstripped markup:
`<cite>The dominant sequence transduction models are based on...</cite>` shown as plain text to the
user, badge and all.

**Root cause:** `_CITE_OR_QUOTE_PATTERN` in `backend/harness/__init__.py` matches `<cite>(?P<cite>.*?)</cite>`
non-greedily. When the model emits its own quote-wrapped tag (`"<cite>...</cite>"`) or a doubled
tag, the non-greedy capture can grab a fragment that itself starts with a literal `<cite>` prefix.
That fragment then fails the substring-anchor check (it obviously isn't verbatim paper text) and
gets wrapped in `<unverified>...</unverified>` — so the final string has a real tag nested inside
another real tag, and the frontend's `parseCitations.tsx` (by design) never recurses into an
already-matched span, so the inner tag prints as literal characters.

**Why it matters:** this is exactly the citation-trust UI (D24's core mechanism) rendering broken
markup instead of a clean warning — the opposite of readable/trustworthy.

**Suggested fix direction:** strip any literal `<cite>`/`</cite>`/`<unverified>`/`</unverified>`
substrings out of a quote before re-wrapping it as unverified, or reject/re-prompt on a model
response containing nested tags rather than passing the fragment through.

## 4. Jupyter notebook widget: real console errors, unusable at default pane width

**Reproduce:** Experiments → open any card with a notebook.

**What happens (still true today, same as the pre-existing QA report):**
```
TypeError: Cannot read properties of undefined (reading 'schema')
    at notebook_core.<hash>.js:501:207
[yjs#509] Not same Y.Doc  (×2)
Menu entry for command 'filemenu:close-and-cleanup' is duplicated.
Menu entry for command 'debugger:show-panel' is duplicated.
Menu entry for command 'toc:show-panel' is duplicated.
```
These come from JupyterLab's own bundled JS, not this codebase's code, so they need a real
upstream/config fix (likely a duplicate init — the widget appears to be getting mounted/initialized
more than once), not a one-line patch.

Separately: the notebook is dropped into the center pane at its default width (~180–220px once
other panes are open) with no minimum-width handling — the kernel picker, toolbar, and cell gutter
all require horizontal scrolling to reach, which makes running so much as one cell a multi-scroll
operation. This is the same "raw JupyterLab widget, not the designed overlay sheet" gap the prior
QA report flagged; still true.

## 5. Every tab ever opened stays mounted for the whole session

> **Decision (user, 2026-08-05): accepted, not fixed.** Deliberately out of scope in
> `PLANNER/IssueFixes.md` — this is a student-scope, single-user app with short sessions, and every
> available fix trades a real regression for the saving: unmounting an inactive tab drops a live
> Jupyter kernel connection, a PDF's render and scroll position, or unsaved editor state. Revisit
> only if session-length jank is actually observed.

**Reproduce:** open ~10 tabs across different sections (Search, Feed, a couple of papers,
Experiments), then look at the DOM.

**What happens:** confirmed by accident — a Playwright selector for a "Save" button came back
ambiguous against **227 elements**, because every `feed__save` button from a Feed tab opened
earlier in the session was still in the DOM, alongside a fully-mounted Writing editor,
Experiments board, and notebook widget, none of them visible. Inactive tabs are never unmounted,
only hidden.

**Why it matters:** this is a real, compounding memory/perf cost — a long session that opens many
papers, searches, and a large Feed will keep growing the DOM for as long as the app stays open,
which will eventually show up as jank, especially with a live Jupyter widget or PDF.js canvas kept
alive per open paper tab.

**Suggested fix direction:** unmount (not just hide) a tab's content when it's not the active tab,
keeping only its route/state — the tab stack already persists state to the backend
(`PUT /tab-stack`), so remounting on reactivation should be cheap.

## 6. A brand-new LaTeX draft opens in a compile-error state

**Reproduce:** Writing → "+ New draft" (don't type anything) → look at the preview pane.

**What happens:** the preview immediately shows a real compiler error stack —
`error: the xdvipdfmx engine had an unrecoverable error`, `No pages of output.` — before the user
has typed a single character. Typing real body text between `\begin{document}`/`\end{document}`
makes the preview render correctly (confirmed — Slice 11's offline preview genuinely works for a
non-empty document). The bug is specifically the empty-document edge case: a LaTeX document with no
content produces zero pages, and the compiler treats that as a fatal, not benign, condition — so
this is what *every* new draft looks like for the first several keystrokes.

**Suggested fix direction:** detect an empty/whitespace-only body and show a quiet "nothing to
preview yet" state instead of running the compiler at all.

## 7. Minor / polish

- **Notes "Save" gives no reason when disabled**, and doesn't reflect a dirty/clean state once a
  note has content — it just always looks clickable. A disabled-state tooltip or a "saved"/"unsaved
  changes" label would close this.
- **Tab overflow menu ("»") lists every open tab**, not just the ones hidden by the strip's
  horizontal scroll — a small redundancy with the visible tab strip.
- **Dashboard's "Continue where you left off" list** renders every row in identical weight/color;
  there's no visual distinction for the tab that's actually active right now.
- **Feed volume**: the Dashboard's Feed tile read "226 new since Wed" for one project — plausible
  given a broad interest profile (7 categories, 50+ keywords), but worth a sanity check that
  `poll_feed_job` isn't fetching/counting the same catch-up window more than once; a Feed tab
  opened during this session did render cleanly with no visible duplicates, so this is a "worth a
  second look," not a confirmed bug.

## Fixed directly in this pass (small, low-risk, applied to the code)

- **Reader PDF opened center-cropped, not at the true page start.** `.reader__pages` used
  `align-items: center` with only `overflow-y: auto`; a PDF page rendered wider than the pane (the
  common case once the Companion pane is open) had its label — sorry, its *content* — silently
  clipped equally from both edges, so the reader opened mid-line with the left margin already
  scrolled off, and there was no way to tell from the UI. Changed to `align-items: safe center` +
  full `overflow: auto` (`frontend/src/reader/ReaderTab.css`) — centers when the page fits, opens at
  the true top-left and scrolls when it doesn't.
- **Knowledge Graph node labels could render as unreadable clipped fragments** (e.g. "ion Is All
  Y…" for "Attention Is All You Need") when the force layout placed a node near the canvas edge —
  the label's ~90px width wasn't included in the layout's edge padding, so long labels spilled
  past the container and got clipped before Cytoscape's own ellipsis logic ever applied. Increased
  `padding` in the `cose` layout config from 40 to 80 (`frontend/src/graph/GraphView.tsx`).
- **Extraction failures were completely unlogged** — `extract_card_job`'s per-window `except
  (*LLMError, RuntimeError): continue` swallowed the real exception with no trace at all, so "every
  extraction window failed" (issue #2 above) was undiagnosable without instrumenting the code by
  hand. Added a `logger.warning` with the real exception per failed window
  (`backend/papers/__init__.py`).
- **Missing favicon 404'd on every single page load**, in every dev session. Added an inline SVG
  favicon so the browser's implicit `/favicon.ico` request has something to find
  (`frontend/index.html`).
