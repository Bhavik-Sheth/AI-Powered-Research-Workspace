# Frontend audit — Research Companion OS

Scope: `frontend/src`, checked against `PLANNER/UI_DESIGN.md` (the locked look-and-feel spec)
and general interface-design principles (distinctiveness, typography discipline, interaction
states, copy voice). Read-only audit, no code changed.

**Status: all four findings below implemented (2026-08-08).** Summary of what changed:
- #1 — `companion/CompanionPane.tsx`/`.css` now render `tool_call` as a chip (mono name +
  spinner) that rewrites in place into a `tool_result` card once the result arrives, plus an
  "Open results →" link when the backend returned a `result_id`.
- #2 — the dead `--accent-rule` fallback is gone (removed along with the old flat
  `.companion__tool-result` block it lived on).
- #3 — added `:hover` to every interactive element that was missing one (the shared `.btn`/
  `.select` first, then the rest of the "no hover" list from the table below), each guarded
  against `:disabled` and against being overridden by an element's own `--active` state.
- #4 — the five "Something went wrong" fallbacks are now `"This screen crashed"`
  (`ErrorBoundary`, genuinely unrecoverable/generic case) or `"Couldn't refresh"` (the other
  four, all bound to a refresh/refetch retry action).

`npx tsc --noEmit`, `npx vitest run` (5/5), and `npx vite build` all pass after the changes.

## TLDR

The design system itself (cool-blueprint palette, three disciplined typefaces, oklch tokens,
provenance vocabulary) is genuinely distinctive — not one of the generic AI-template looks — and
it's implemented faithfully almost everywhere I checked: relevance vocabulary, absence states,
matrix cell provenance, graph legend, responsive nav collapse. Contrast on the muted/accent text
pairs checks out at AA or better. The real gaps are three concrete implementation drifts, listed
worst-first below.

## Findings

### 1. Companion tool-call/tool-result rendering doesn't match the spec (high)

`UI_DESIGN.md` §3.1 defines **two** distinct pieces for tool activity: an inline mono **chip**
with a spinner while a tool call is running, and a structured **result card** it collapses into
(header line like `SEARCH_LIBRARY · 3 MATCHES`, up to 3 result rows, an `open all` link) —
"Rendered from `ui_view` by id... the frontend never re-derives it."

What's actually built (`companion/CompanionPane.tsx:26-160,359-364`) collapses both `tool_call`
and `tool_result` events into one `role: "tool"` entry and renders it as a single flat text block
(`.companion__tool-result`, `CompanionPane.css:182-189`). There's no spinner, no chip/card
distinction, no row list, no `open all` link — the transcript just prints whatever string the
backend sent as `model_view`.

This is the one part of the companion transcript (the app's core differentiator, per D32) that
doesn't carry the structure the spec calls out by name. Since it's driven by `ui_view`, this is
mostly a rendering gap, not a backend one — worth prioritizing because tool activity is probably
the most common transcript entry type after replies.

**Suggested fix:** split `role: "tool"` into `tool_call` (chip, spinner, mono tool name) and
`tool_result` (card, header + rows + link) as two transcript entry kinds, matching §3.1.

### 2. Dead CSS custom property in the companion (low, quick fix)

`companion/CompanionPane.css:183`:
```css
.companion__tool-result {
  border-left: 2px solid var(--accent-rule, var(--border));
```
`--accent-rule` is never defined anywhere (`design/tokens.css` or elsewhere) — grepped the whole
`frontend/src` tree, this is its only occurrence. It silently falls back to `--border` every time,
so the rule renders as a plain neutral left-border, not an accent one. Either the token was
renamed/dropped and this line wasn't updated, or it should be `var(--accent)` / `var(--border)`
outright. As written it reads like intentional accent styling but isn't — worth fixing alongside
finding #1 since that's the same component.

### 3. Most clickable elements have no `:hover` state (medium, broad)

`UI_DESIGN.md` §6 itself lists this as a standing constraint: *"Hover, active, disabled, and
`:focus-visible` on every interactive element."* Checked every CSS file for `:hover`:

| Has `:hover` | Has none |
|---|---|
| `app/AppShell.css` (4), `dashboard/Dashboard.css` (1), `companion/CompanionPane.css` (1), `reader/ReaderTab.css` (1), `matrix/MatrixView.css` (1), `feed/FeedView.css` (1) | `design/buttons.css`, `library/LibraryView.css`, `notes/NotesView.css`, `experiments/ExperimentsBoard.css`, `experiments/ApprovalPrompt.css`, `experiments/LiveNotebookPanel.css`, `graph/GraphView.css`, `writing/ManuscriptTab.css`, `search/SearchResults.css`, `settings/SettingsPanel.css`, `settings/InterestProfileForm.css`, `onboarding/wizard.css`, `design/ErrorCard.css`, `app/ErrorBoundary.css` |

The most notable case is `design/buttons.css` — the **shared `.btn`/`.btn--primary`/`.btn--outline`
component** used across Matrix, Library, and Writing. Every primary and secondary button built
from it has `cursor: pointer` but no hover feedback at all. Same pattern on library cards
(`library/LibraryView.css:94-102` — `.library__card-title`, clickable, no hover), filter chips,
the import placeholder, and notes rows.

`:focus-visible` is handled once, globally, in `design/tokens.css:90-94` — that part's solid and
correctly universal. Hover just never got the same treatment past the first few screens built.

**Suggested fix:** add a hover pass to `design/buttons.css` first (highest reuse), then
`library/LibraryView.css` and `notes/NotesView.css` (most-used browsing screens). A subtle
`accent-tint` background shift or `--border`→`--accent` outline shift is consistent with what
the app already does elsewhere (e.g. `AppShell.css:167-169` `.pane-toggle:hover`).

### 4. Inconsistent error-card copy — "Something went wrong" as a fallback title (low)

`ErrorCard`'s own doc comment (`design/ErrorCard.tsx:3`) says it "always names what went wrong,"
and most call sites do — `Could not load the library`, `Could not start the measured run`,
`Could not load this matrix`. But five call sites fall back to the generic
**"Something went wrong"**: `app/ErrorBoundary.tsx:67`, `library/LibraryView.tsx:144`,
`experiments/ExperimentsBoard.tsx:265`, `dashboard/Dashboard.tsx:82`, `matrix/MatrixView.tsx:245`.

These are all specifically the *background refresh failed, but stale data is still showing* case
— a real, nameable situation ("couldn't refresh — showing what was loaded before"), not an
unknown one. Right now the title tells the user nothing the body message doesn't already have to
carry alone, and it breaks the voice the rest of the app commits to (never vague about what
happened — same standard `UI_DESIGN.md`'s own error-copy pattern sets: name the source, say what
happened, say what still worked).

**Suggested fix:** replace the shared fallback with something specific to *that* failure mode,
e.g. `"Couldn't refresh"` (paired with the existing message body), so the title and body aren't
saying the same generic thing twice.

## What's already solid (don't touch)

- Relevance vocabulary (`unmarked`, `not relevant`) is centralized in one file
  (`design/labels.ts`) and never re-declared — exactly one source of truth, as the spec requires.
- Absence states (`not stated in this paper`, `Unlinked`, `unsupported claim`, matrix `not
  stated`) use the same dashed/italic/muted treatment everywhere, consistently.
- Matrix cell provenance (`matrix/MatrixView.tsx:383-448`) and the graph legend/shape encoding
  (`graph/GraphView.tsx` + `nodeStyle.ts`) — both flagged as "not designed yet" in
  `UI_DESIGN.md` §8 — are in fact built and match the spec's intent.
- Responsive nav collapse below 1280px (`app/AppShell.tsx:42-43,422-426`) is real and
  auto-triggering, not just a manual toggle — addresses §7's "collapse the nav before ever
  dropping the companion" requirement correctly.
- Color tokens are 1:1 with `UI_DESIGN.md` §1 (verified `design/tokens.css` against the spec
  table directly); no stray hardcoded hex outside the seven `#fff` button/badge text colors,
  which the spec doesn't tokenize either.
- Contrast checked (oklch → sRGB, WCAG relative luminance) on the risk pairs §7 calls out —
  muted text on surface, accent-text on white, danger-text on white — all land between 5.3:1 and
  8:1, comfortably AA. No action needed here.
- `jsx-a11y` is configured in `eslint.config.js` with `no-static-element-interactions` and
  `click-events-have-key-events` as errors, not warnings — keyboard reachability is enforced at
  lint time, not left to review.

## Priority order

1. Fix companion tool-call/result rendering (#1) — the app's signature surface, currently the
   weakest link to the spec.
2. Add hover states starting with `design/buttons.css` (#3) — cheap, high-reuse, and it's a
   constraint the spec states explicitly.
3. Remove the dead `--accent-rule` fallback (#2) — one line, do it while in that file for #1.
4. Tighten the five "Something went wrong" fallbacks (#4) — copy-only, no layout risk.
