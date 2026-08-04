# Research Companion OS — QA Testing Report

Tested live via Playwright/Chrome against `http://localhost:5173/` (frontend) and
`http://127.0.0.1:34121` (backend sidecar), project "Attention Sinks: A Study", 2026-08-04.

> ## Corrections after root-cause verification (2026-08-04)
>
> Three findings below were checked against the running app and the source, and did not hold.
> Read them with these corrections; `Bug_Fix_Plan.md` is built on the corrected set.
>
> - **"The app is visually unstyled" is wrong.** The design system *is* applied: 20 stylesheets and
>   702 CSS rules load, `--accent` resolves to `oklch(52% 0.385 210)`, `body` background is the
>   token value `#e4e0d8`, and the 3-pane shell renders with floating rounded panels and the cyan
>   accent. The real and much smaller gap is that the three font families are **not vendored** — no
>   `@font-face` rule or font file exists, so Space Grotesk / Newsreader / JetBrains Mono all fall
>   back to system fonts. That is Slice 15, not a re-skin of the app.
> - **"Feed items never state why they surfaced" is wrong.** `backend/feed/` computes
>   `why_relevant` and drops any candidate with no match (`feed/__init__.py:220`), and
>   `FeedView.tsx:12–17` renders it. The observed `categories: cs.CL` line *is* the match reason.
>   It is thin (category-only, no similarity shown), which is a polish item, not a spec violation.
> - **"`Check citations` is a silent no-op" is wrong.** The check runs end to end and persists to
>   `documents.citation_findings`. On a draft with no `\cite` commands it correctly returns zero
>   findings, and the UI has no empty state — so it *looks* dead. The defect is the missing result
>   state (Slice 12), not the check.
>
> One finding was **understated**: the duplicate paper rows are not a dedup failure. They are
> leftover build-phase test fixtures (`arxiv:tracer-bc607d`, `arxiv:tracer2-2e4111`, no
> `pdf_origin`, every stage `queued`) sitting in the `papers` table. Opening one of those rows
> explains part of the empty-card and `References (0)` symptoms independently of the extraction
> failure.

## 1. Summary

The app is functionally broad — nearly every US1–US13 surface exists, routes, and returns real
data from a live backend (search federation, PDF.js reader, notes CRUD, experiments with a *real*
embedded Jupyter kernel, matrix, graph, LaTeX editor, feed, settings, readiness). That's a lot of
genuine plumbing working end to end.

But the build is **visually unstyled** — none of `UI_DESIGN.md`'s blueprint-grid frame, cyan
accent, oklch palette, three-font system, card shadows, or quote/evidence tinting is present. The
whole app renders in default black-on-white browser chrome with plain HTML buttons and links; it
reads as an unstyled prototype, not the mock. That's the single biggest gap between what was
designed and what is built.

Functionally, the most serious problem is that **paper processing is failing** for 3 of the 4
papers in the project (Papers library shows a `Failed` status badge on "Attention Is All You
Need", "AskChem", and "Efficient Streaming Language Models with Attention Sinks"). This cascades
into empty extractive cards ("not stated" on every field, including for the famous, easy
"Attention Is All You Need" paper), a `References (0)` / `Code (0)` structure sidebar, an empty
knowledge graph despite both papers having been opened, and a Companion that fabricates quotes
instead of citing real spans. The citation/evidence system — the app's core trust mechanism per
D24 — does not actually verify anything in the flows tested: the Companion answered questions with
invented quotation marks and no citation markers, and explicitly said "I don't have the exact
text" while still answering.

Pane/tab ergonomics are partially VS-Code-like: tabs open, switch, and close correctly, and the
left nav and Companion both collapse. But **tab drag-to-reorder does not work** (it just switches
focus to the drop-target tab) and **no pane is drag-resizable** — nav/center/companion widths are
fixed, only collapsible.

## 2. Critical bugs

1. **Paper processing pipeline is failing for most papers.** Papers → status column shows
   `Failed` for "Attention Is All You Need", "AskChem", and "Efficient Streaming Language Models
   with Attention Sinks" (only one paper, "Search Strategies for Optimal Classification and
   Regression Trees", shows `Queued`; none show a healthy/done state). Reproduce: nav → Papers.
   Downstream effects observed directly:
   - Reader extractive card shows `not stated in this paper` for all five fields (Problem/
     Method/Datasets/Results/Limitations) on "Attention Is All You Need" — implausible for a
     paper this well-structured.
   - Structure sidebar shows `REFERENCES (0)` and `Code (0)` for the same paper, which has ~40
     references in reality.
   - Knowledge Graph is completely empty ("No graph yet") even after both papers were opened in
     the reader during this session — per US11 this should populate LLM-derived edges for opened
     papers.

2. **Companion fabricates quotes instead of citing verified spans (D24/US4 failure).** Using the
   selection popover's "Ask about this" on the phrase "dominant sequence transduction" in
   Attention Is All You Need, the Companion replied: *"While I don't have the exact wording of the
   sentence in front of me..."* and then gave an invented, factually wrong gloss ("refers to a
   sequence-to-sequence transformation... dominant processing path") with no citation, no quote
   block styling, and no `⚠ unverified` badge. In a follow-up ("Summarize... with citations"), it
   invented three quotation-marked "quotes" (e.g. *"The dominant paradigm for modeling sequences is
   to use recurrent or convolutional neural networks."*) that do not appear verbatim in the actual
   paper text (the real sentence is "The dominant sequence transduction models are based on
   complex recurrent or convolutional neural networks that include an encoder and a decoder").
   These fabricated quotes are rendered as plain text, not the required cited-evidence block, and
   the D24 substring validator never flags them. This is the core trust mechanism of the product
   and it does not work in the flows tested.

3. **LaTeX live preview (SwiftLaTeX WASM) is completely broken.** Writing → any draft → the
   preview pane permanently shows: `This is pdfTeX... I can't find the format file
   'swiftlatexpdftex.fmt'!` with a console error `Compilation failed, with status code 1 @
   swiftlatexpdftex.js`. No preview ever renders, for the default empty document. This fails
   US12's "SwiftLaTeX WASM preview is the default and updates within ~1–2s" outright.

4. **`Check citations` button in Writing is a silent no-op.** Clicking it (Writing → toolbar)
   produces no visible result — no panel, no toast, no highlight, no console call observed. Looks
   implemented, does nothing.

5. **Feed items never state why they surfaced.** Every card in Feed shows only a bare
   `categories: cs.CL`-style line — no matched keywords, no similarity score, no "why this is
   here" copy. Per US13's explicit acceptance criterion, "an item without a match reason never
   renders" — here, every item renders without one.

6. **Jupyter notebook widget throws a console TypeError on open.** Experiments → open any card →
   `TypeError: Cannot read properties of undefined (reading 'schema')` at
   `notebook_core...js:501`, plus repeated `[yjs#509] Not same Y.Doc` warnings and duplicated
   Jupyter menu-command warnings. The notebook still rendered and a cell had already run
   (`kernelspec fix works 42`), so this didn't visibly block the demo, but it's a real error on a
   core interaction path.

## 3. PRD compliance table

| US | Area | Verdict | Key gaps observed |
|---|---|---|---|
| US1 | Onboarding | Not re-tested (already completed per task setup) | Not exercised live; can't confirm wizard step-locking, Docker fail-closed messaging, or invalid-key retry card in this session. |
| US2 | Federated search | **Partial** | Query streamed, real result cards rendered, and the partial-failure state matched the design almost verbatim ("Semantic Scholar did not respond / Other sources returned normally — these results are incomplete." + Retry). Did not confirm the "one LLM pass, not one rewrite per source" internal behavior, dedup, or cross-encoder rerank (not observable from the browser). |
| US3 | Reader / extractive card | **Fail** (for this project's papers) | Real PDF.js render with intact layout — pass. Structure sidebar present with jump-to sections — pass. But extractive card is "not stated" on every field and `REFERENCES (0)` / `Code (0)` because paper processing is `Failed` (see Critical Bug #1). Can't confirm this is a "not stated" vs. "still extracting" distinction — no processing-state UI was visible, it just silently reads as done-and-empty. |
| US4 | Ask about a highlight | **Fail** | Selection popover with the exact three actions (Highlight / Ask about this / Explain) — pass, matches spec. But answers are not citation-grounded (Critical Bug #2): no inline citation, no evidence-block styling, no `⚠ unverified` badge, fabricated quotes presented as if genuine. |
| US5 | Notes | **Pass** | Created a note; it saved, appeared in the list, showed the `Unlinked` state correctly. Save button UX gap: stays disabled with no explanation until both title and body have content (minor). |
| US6 | Project memory | Not testable in browser (no direct UI surfaces `query_memory` internals, chunking, or cross-project isolation) | Companion's poor grounding (Critical Bug #2) suggests retrieval may not be wired to real paper text, but this can't be confirmed from the browser alone. |
| US7 | Companion and tabs | **Partial** | Companion present on every screen tested, survived navigation across ~11 tabs without resetting the transcript — pass. Tabs open/switch/close correctly — pass. Did not observe a Stop control during any turn (turns completed too fast to catch it, or it never appeared — inconclusive). One console warning: `WebSocket... failed: WebSocket is closed before the connection is established` on initial load — the socket appears to reconnect since the transcript still worked, but this is a real warning worth investigating. Also observed a duplicated user message in the transcript (same "Compare Attention Is All You Need..." text appearing twice back-to-back) — possible double-send bug. |
| US8 | Voice push-to-talk | Not testable in browser (requires real mic input) | UI affordance present ("Hold to talk" button in composer); did not exercise actual audio capture. |
| US9 | Experiments / sandboxed notebook | **Partial** | Board with the correct 4 status columns (Planned/Remaining/In Progress/Done) — pass. Opening a card embeds a live, real Jupyter notebook (kernel connected, a cell had already run with real output) — genuinely impressive and functional. But the UI is the raw JupyterLab widget dropped inline, not the designed 300px overlay sheet with Hypothesis/Setup/Metrics/Notes/Links — a real design deviation. Console TypeError on load (Critical Bug #6). Did not verify Docker mount isolation, network-off-by-default, or the `run_all` human-confirmation gate from the browser (not fully observable). |
| US10 | Literature matrix | **Pass** (mostly) | Standard columns present (Problem/Method/Datasets/Results/Limitations); an Experiment ("Testing") appears as a comparable row alongside papers, matching the spec. Real extracted text shown for the one successfully-processed paper. Couldn't confirm the quote-click-through-to-span behavior for extracted cells. |
| US11 | Knowledge graph | **Fail** (in this project's current state) | Graph is empty ("No graph yet") despite two papers having been opened this session. Legend/filter chips/node shapes could not be evaluated because there's no data to render. |
| US12 | LaTeX writing | **Fail** | CodeMirror source editor works, line numbers and syntax visible. Live preview is completely broken (Critical Bug #3). `Check citations` is a no-op (Critical Bug #4). Could not test `\cite` autocomplete, BibTeX export, or the unsupported-claim dashed treatment because no citations exist in an empty draft. AI-writes-no-prose constraint held (no prose-generation affordance was found in Writing). |
| US13 | Research feed | **Fail** | Interest profile is inspectable/editable (found under the "Settings" nav item, seeded with real categories/keywords) — pass for that sub-criterion. But feed items never show a match reason (Critical Bug #5), which the PRD explicitly says must never happen. Save/Dismiss buttons are present and clickable; did not confirm centroid-shift-on-save or dedup-against-dismissed behavior. |

## 4. UI/UX issues

- **No visual design applied.** The entire app renders in default browser styling — black
  Helvetica-ish text, blue underlined links, unstyled `<button>` elements, white background, no
  panel shadows, no rounded corners, no blueprint grid frame, no cyan accent, no serif/sans/mono
  type split. This is a near-total gap against `UI_DESIGN.md` §1–§5. Every screen tested (Dashboard,
  Reader, Papers, Notes, Experiments, Matrix, Graph, Writing, Feed, Settings, Readiness, Search)
  shows this same lack of styling — it isn't isolated to one view.
- **Tabs cannot be drag-reordered.** Dragging the "Papers" tab onto "Attention Is All You Need"
  did not reorder the tab strip — it just activated the drop-target tab, i.e. the drag degraded to
  a click. VS Code-style tab reordering is not implemented.
- **No pane is resizable by dragging.** Inspected the DOM for resizer/splitter elements — none
  exist. Nav, center pane, and Companion all have fixed widths; the only adjustment available is
  full collapse (nav collapses to nothing, not to icons as `UI_DESIGN.md` §7 specifies; Companion
  collapses similarly). A user who wants the reader or writing pane wider than default has no way
  to get it short of collapsing a whole other pane.
- **Dashboard is missing content relative to spec.** Only 2 stat tiles (Papers, Notes) render
  instead of the spec's 4 (Papers/Notes/Experiments/Feed), and stat tiles show bare counts with no
  actionable qualifier ("4 unmarked", etc. — `UI_DESIGN.md` §4.1 calls this "the point"). The
  entire "NEEDS ATTENTION" section (soft nudges + error cards) is absent. "Continue where you left
  off" appears to just mirror the open-tab list rather than tracking real per-item resume state —
  all bullets are the same color instead of distinguishing the live item from the rest.
- **Settings page is Feed-only.** The "Settings" nav item shows only the Feed interest profile
  (categories/keywords) — there's no way to revisit the LLM/API-key or Docker configuration set up
  during onboarding from inside a running project.
- **Companion chat renders raw markdown.** Assistant replies contain literal `**bold**` asterisks
  and are never rendered as bold/italic — markdown isn't parsed in the transcript.
- **Possible duplicate-send bug.** The Companion transcript shows the exact same user message
  ("Compare Attention Is All You Need and Efficient Streaming Language Models with Attention
  Sinks: what problem does each paper's abstract say it solves? Cite a quote from each.") appearing
  twice, back to back, as if sent twice.
- **Notes Save button gives no feedback about why it's disabled.** Typing only in the body leaves
  Save disabled with no visible reason; it only enables once the title field also has real (not
  placeholder) text.
- **Readiness is a full page, not a strip.** Functions as intended (per-capability status: Vault,
  Database, Docker, LLM, Search, Embeddings, Reranker, Voice, all shown as badges), but the design
  calls for a readiness strip visible during cold start, not a page you have to navigate to.

## 5. Console/network errors observed

```
[ERROR] Failed to load resource: 404 Not Found — http://localhost:5173/favicon.ico
[WARNING] WebSocket connection to 'ws://127.0.0.1:34121/ws/session/<id>?token=devtoken' failed:
          WebSocket is closed before the connection is established.
          (src/state/useProjectSocket.ts:41) — fired twice on initial load
[WARNING] Menu entry for command 'filemenu:close-and-cleanup' is duplicated.
[WARNING] Menu entry for command 'debugger:show-panel' is duplicated.
[WARNING] Menu entry for command 'toc:show-panel' is duplicated.
[WARNING] [yjs#509] Not same Y.Doc  (×3, on opening an Experiment's notebook)
[ERROR] TypeError: Cannot read properties of undefined (reading 'schema')
        at notebook_core.<hash>.js:501:207 (on opening an Experiment's notebook)
[ERROR] Compilation failed, with status code 1 @ swiftlatexpdftex.js
        ("I can't find the format file `swiftlatexpdftex.fmt'!" in the LaTeX preview pane)
```
All ~180 REST calls captured in `browser_network_requests` during this session returned `200 OK` —
no failed API calls observed outside the WebSocket handshake warning and the two errors above.

## 6. Minor polish items

- Feed and search result cards use plain unstyled `<h3>` links — no card chrome, no relevance
  badge visuals beyond default browser link-blue.
- The "Testing" experiment card and several feed cards have no metrics-count / paper-link chips
  visible, consistent with the general lack of styling rather than a functional gap.
- Notes editor's "Save" button stays enabled/interactive-looking after a successful save with no
  changed content (no dirty-state indication).
- Matrix's "not stated" cells render as a plain bordered box rather than the spec's dashed-italic
  treatment (consistent with the general styling gap, not a separate bug).
- Tab bar needs horizontal scrolling once ~7+ tabs are open (confirmed scrollbar present and
  functional) — acceptable, but there's no overflow menu / "..." affordance as VS Code offers.
- Relevance badge copy is correctly "Unmarked" (not the enum value "unset") in the Papers list —
  this one specific §9.2 D correction from `UI_DESIGN.md` was implemented correctly.
