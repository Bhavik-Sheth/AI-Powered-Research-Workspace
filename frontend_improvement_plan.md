# Frontend Improvement Plan

**Methodology, matched to `PLANNER/ImplementationPlan.md`.** Build units are **Phase 1 … Phase
7**. The word "Slice" does not appear anywhere in this document, and neither do the old flat
severity buckets (`P0`–`P4`, `F0`–`F8`) from the first two passes — every finding from both
passes is preserved below, re-homed into a numbered sub-unit. Each phase is broken into numbered
sub-units (`Phase N.M`) so a coding agent can pick up one vertical, independently runnable piece
at a time; the **last sub-unit of every phase is that phase's sign-off checkpoint** — no work on
the next phase begins until it's visually/behaviourally verified in the running app. Sub-units
within a phase are listed in the order they should be built; later sub-units may assume earlier
ones in the same phase are done.

**Build order, fixed by severity:** a crash that white-screens the whole app outranks a wrong
font, which outranks a missing error message, which outranks an unreachable keyboard control,
which outranks a screen-level shape mismatch, which outranks polish. **Phase 1 → Phase 2 →
Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7.**

This plan does not restructure routes, panes, or add product features — every fix here closes a
gap against either `UI_DESIGN.md` (visual/theme spec) or observed runtime behaviour (loading,
error handling, races, accessibility). Baseline: `frontend/src/design/tokens.css` already
matches `UI_DESIGN.md` §1 exactly — colors, fonts, radii are all correctly *defined*; most of
this plan is about *consumption*, not definition. Findings are cross-checked against `Issues.md`
/ `PLANNER/IssueFixes.md` so nothing already tracked or already shipped there is repeated (all
14 of those slices are shipped per git log through `530d668`).

---

## Phase 1: The app cannot go permanently blank

### What this delivers
Today, **no `ErrorBoundary` exists anywhere** in the app (`grep -rl ErrorBoundary src` → empty),
so any uncaught render exception — a malformed WebSocket payload, a backend shape drift, a bad
project id — white-screens the entire app with no recovery short of a manual reload. This phase
makes every known trigger for that fail safely: a crash shows a recoverable fallback instead of
a blank page, a cold load or backend restart shows a spinner instead of `null`, a dead backend
shows an error instead of silently routing into onboarding, and a malformed WebSocket frame or
unexpected Companion event payload degrades instead of throwing. This is the single highest-
priority phase in the whole plan — it turns every other phase's worst-case outcome (a crash)
into a recoverable one, so build it first.

### Depends on
none

### Touches
`frontend/src/main.tsx`, `frontend/src/app/App.tsx` — new `ErrorBoundary` wrapping `<App/>`
(Phase 1.1); `App.tsx:17-19`'s `isPending → null` render (loading placeholder instead) and
`App.tsx:15,21`'s `isError`/`onboarding_completed_at` conflation (branch on `isError` explicitly
so a dead backend shows an error, not the onboarding wizard) (Phase 1.2); `frontend/src/notes/NotesView.tsx:97-99`
— same blank-`null` pattern (Phase 1.2); `frontend/src/state/bridge.ts:16-28` +
`main.tsx:9` — `configureApiClient()`'s unguarded module-load throw when the Electron bridge
isn't present yet (Phase 1.2); `frontend/src/app/AppShell.tsx:159-166` (`ProjectGate`) and
`frontend/src/state/useTabStack.ts:18-31` — unguarded project-list/project-get/tab-stack fetches
that render nothing forever on failure, with no error or retry (Phase 1.2); `frontend/src/state/useProjectSocket.ts:73`
— `JSON.parse(event.data)` with no try/catch around a malformed WS frame (Phase 1.3);
`frontend/src/companion/CompanionPane.tsx:12-19` (`isDownstreamEvent`) — validates only the
`event` discriminator, not per-variant payload fields, so a backend shape drift throws "Objects
are not valid as a React child" straight into the Companion tree (Phase 1.3).

### Phase 1.1: Global ErrorBoundary
**What this delivers:** any uncaught render-time exception, anywhere in the component tree,
shows a fallback screen with a reload affordance instead of a permanent blank page.
**Depends on:** none. **Touches:** new `ErrorBoundary` component (`frontend/src/app/` or
`frontend/src/design/`), wraps `<App/>` in `main.tsx`.

### Phase 1.2: Startup and fetch failures surface as errors, not blank screens or wrong routing
**What this delivers:** a missing Electron bridge, a cold load, a backend restart mid-session,
and a dead backend during the onboarding-vs-shell check all show a visible loading or error
state — never a blank `null` and never a silent misroute into the onboarding wizard when the
real problem is "backend unreachable." **Depends on:** 1.1. **Touches:** `state/bridge.ts`,
`app/App.tsx`, `app/AppShell.tsx` (`ProjectGate`), `state/useTabStack.ts`, `notes/NotesView.tsx`.

### Phase 1.3: WebSocket and Companion payloads fail safely — Phase 1 sign-off
**What this delivers:** a malformed WebSocket frame or an unexpected Companion event shape is
caught and logged/ignored instead of throwing uncaught inside `onmessage` or crashing the
Companion pane via a React child-type error. Verify by confirming (in the running app) that the
app survives a backend restart, a bad project id, and a forced-malformed WS payload without a
blank screen. **Depends on:** 1.1, 1.2. **Touches:** `state/useProjectSocket.ts`,
`companion/CompanionPane.tsx` (`isDownstreamEvent`).

---

## Phase 2: Core chrome renders the way it's designed

### What this delivers
Right now the app's chrome is visibly undressed: fonts silently fall back to system defaults,
the app frame has no blueprint grid or shadow, several buttons render as bare browser controls,
keyboard focus is invisible everywhere, and the nav never shows which view is open. This phase
closes all five — the app looks like the design after it, not a rough approximation.

### Depends on
Phase 1 (no point judging chrome fixes against a build that can still blank-screen)

### Touches
`frontend/package.json`/`node_modules` — `@fontsource/newsreader`, `@fontsource/space-grotesk`,
`@fontsource/jetbrains-mono` are declared but **not installed on disk**, so every `@import` in
`tokens.css` 404s and every `font-family` falls back to its generic keyword (Phase 2.1);
`frontend/src/app/AppShell.css` `.app-frame` — never paints the `--frame-grid`/
`--frame-grid-quiet` gridlines or the `0 20px 60px oklch(20% 0.08 225 / 0.35)` frame shadow,
despite both tokens being correctly defined in `tokens.css` and never consumed (§9.2 item H,
Phase 2.2); `frontend/src/writing/ManuscriptTab.tsx` (`+ Image`, `+ Mermaid`, `Check citations`,
`Export BibTeX`, `+ New draft`, ~lines 189–217) and `frontend/src/matrix/MatrixView.tsx`
(`+ New matrix`, `Edit rows & columns`, `+ Custom column`, `+ User column`, cell `Save`/`Cancel`,
matrix picker `<select>`, ~lines 152–330) — buttons/selects with no className, rendering as
browser-default controls (Phase 2.3); global `:focus-visible` rule (`2px solid var(--accent)`,
`2px` offset, `4px` radius — spec'd in UI_DESIGN.md §6/§7, absent app-wide) and
`app/AppShell.tsx` left-nav rows (never receive an active class — no `border-left: 3px accent`,
no `accent/0.12` background, no bullet fill, per §2's row-states rule) (Phase 2.4).

### Phase 2.1: Real fonts load
**What this delivers:** Newsreader, Space Grotesk, and JetBrains Mono actually render app-wide
instead of silently falling back to system-ui/Georgia/monospace. Re-verify every later "wrong
font" finding after this lands — several symptoms elsewhere may resolve or change shape once
real fonts are loading. **Depends on:** Phase 1. **Touches:** `frontend/package.json`/lockfile
(`npm install`, or explicit `@fontsource/*` reinstall if the lockfile is stale), verify via
devtools computed `font-family` on a heading.

### Phase 2.2: App frame shows its blueprint grid and shadow
**What this delivers:** the app frame paints the two-`linear-gradient` 24px blueprint grid and
the `0 20px 60px` frame shadow UI_DESIGN.md §1 specifies, switching to the dimmer
`--frame-grid-quiet` (0.07 opacity) on Reader/Writing/Notes per §7's "grid recedes behind
sustained reading" rule. **Depends on:** 2.1. **Touches:** `app/AppShell.css` `.app-frame`.

### Phase 2.3: Buttons and selects get real styling (Writing, Matrix)
**What this delivers:** every button/select on the Writing and Matrix screens matches the shared
button spec (`height 32px, radius 8px, 700 12px sans`, outline/primary/inline variants per §1)
instead of rendering as a bare OS control. Worth adding one shared `.btn`/`.btn--primary`/
`.btn--inline` set so this can't recur on a future screen. **Depends on:** 2.1. **Touches:**
`writing/ManuscriptTab.tsx`, `matrix/MatrixView.tsx`, optionally a new shared button stylesheet
in `design/`.

### Phase 2.4: Focus is visible and the active nav row is shown — Phase 2 sign-off
**What this delivers:** every interactive element shows the spec'd `2px accent` focus ring on
keyboard focus, and the left nav highlights whichever view is currently open (`border-left: 3px
accent`, `accent/0.12` background, filled bullet) instead of always rendering every row inactive.
Verify by tabbing through the app and confirming the active view is visually obvious in the nav.
**Depends on:** 2.1–2.3. **Touches:** `design/tokens.css` or `app/AppShell.css` (global
`:focus-visible` rule), `app/AppShell.tsx` (active-route-derived nav row class).

---

## Phase 3: Errors are shown, not swallowed, and mutations don't lose data

### What this delivers
Most screens today have **zero** error UI — no `ErrorCard`, no try/catch — so a failed request
just leaves the screen looking stuck, indistinguishable from "still loading." Separately, two
concrete data-loss races exist: editing Writing or Notes and switching away within ~1.5 seconds
silently drops the edit, and Matrix has a lost-update race on rapid clicks. This phase brings
every screen up to the standard `FeedView.tsx`/`NotesView.tsx`/`SettingsPanel.tsx` already meet
(they correctly import `ErrorCard` and catch their mutations — copy that pattern), and closes
the races.

### Depends on
Phase 1 (an `ErrorCard` shown inside a tree that can still crash on a bad payload doesn't help)

### Touches
`frontend/src/library/LibraryView.tsx`, `frontend/src/graph/GraphView.tsx:89-96`,
`frontend/src/matrix/MatrixView.tsx` (`refreshMatrices`, `createNewMatrix`, `putMatrix`,
`editCell`), `frontend/src/writing/ManuscriptTab.tsx` (`refresh`, `createDraft`,
`autosave:102-111`, `uploadAsset`, `insertMermaidDiagram`, `downloadBibtex`),
`frontend/src/experiments/ExperimentsBoard.tsx:154-174,205-219`,
`frontend/src/experiments/LiveNotebookPanel.tsx:84-110`, `frontend/src/dashboard/Dashboard.tsx:43-49,65-71`,
`frontend/src/voice/useVoice.ts:15-25,46-51`, `frontend/src/settings/InterestProfileForm.tsx:89-104`
— all unguarded, no `ErrorCard`, no rollback on failure (Phase 3.1); `writing/ManuscriptTab.tsx` +
`writing/useManuscriptPreview.ts` — two independent debounced saves (autosave 1200ms, compile+save
1500ms) neither flushed on document switch (Phase 3.2); `notes/NotesView.tsx:55-62`
(`selectNote`, switches notes while dirty with no confirm), `matrix/MatrixView.tsx:103-136`
(stale-closure lost-update race on rapid edits), `search/SearchResults.tsx:86-98`
(`handleSearch`, no cancellation for overlapping searches) (Phase 3.3).

### Phase 3.1: Shared error-surface pattern applied to every data-fetching screen
**What this delivers:** Library, Graph, Matrix, Writing, Experiments, Dashboard, Voice, and the
Settings interest-profile form all show an `ErrorCard` (with retry where the action is retryable)
instead of looking permanently stuck or silently no-opping on failure; a failed optimistic update
(`InterestProfileForm`) rolls back instead of showing a change that didn't actually save.
**Depends on:** Phase 1. **Touches:** the eight files listed above — apply the
`FeedView.tsx`/`NotesView.tsx`/`SettingsPanel.tsx` pattern to each.

### Phase 3.2: Writing tracks a dirty/saved indicator and flushes on switch
**What this delivers:** switching drafts (or closing the tab) no longer silently drops an edit
made in the last ~1.5 seconds — either the pending save is flushed before switching, or the user
sees a dirty-state warning, matching what Notes already partially does. **Depends on:** 3.1.
**Touches:** `writing/ManuscriptTab.tsx` (`openDraft`, `saveTimerRef`),
`writing/useManuscriptPreview.ts` (flush-on-`documentId`-change instead of pure cancel).

### Phase 3.3: Notes, Matrix, and Search stop losing edits to races — Phase 3 sign-off
**What this delivers:** switching notes while the current note is dirty asks for confirmation
first; Matrix's rapid checkbox/column edits no longer overwrite each other via a stale-closure
race; firing two searches in quick succession always shows the result for the query actually in
the box, not whichever response happened to land last. Verify by reproducing each original
failure scenario and confirming it no longer loses data. **Depends on:** 3.1, 3.2. **Touches:**
`notes/NotesView.tsx`, `matrix/MatrixView.tsx`, `search/SearchResults.tsx`.

---

## Phase 4: Every control is reachable by keyboard, and the approval gate is a real modal

### What this delivers
Several core controls aren't just missing a focus ring (Phase 2.4 fixed the ring) — they aren't
focusable at all, because they're `<div>`/`<span>` elements with an `onClick` and nothing else. A
keyboard-only user currently cannot switch sections, switch tabs, close a tab, open a paper from
the library, or expand an experiment card. Worse, `ApprovalPrompt` — the D31 human-approval gate
that is the only path to letting code execute — declares `role="dialog" aria-modal="true"` but
implements no real focus trap and no Escape handling, so the ARIA claims a modal that the
behaviour isn't. This phase fixes both classes of gap.

### Depends on
Phase 2 (fixing focus-visible styling first means these controls look right the moment they
become focusable)

### Touches
`frontend/src/app/AppShell.tsx:348-378` (left nav rows for Search/Settings/Readiness),
`:401-440` (tab strip + "×" close), `:454-466` (tab overflow menu) —
`<div>`/`<span>`/`<li>` + `onClick`, zero `tabIndex`/`role="button"`/`onKeyDown`;
`frontend/src/library/LibraryView.tsx:88` (paper title, `<span onClick>`);
`frontend/src/experiments/ExperimentsBoard.tsx:264-267` (card-head expand, `<div onClick>`)
(Phase 4.1); `frontend/src/experiments/ApprovalPrompt.tsx:59` — `role="dialog" aria-modal="true"`
with no focus trap, no Escape handling, Tab escapes to the board behind it;
`frontend/src/graph/GraphView.tsx:192-194` (detail-panel "×" close, no `aria-label`);
`frontend/src/companion/CompanionPane.tsx:314-319` (mic "hold to talk", mouse-only, no keyboard
or touch equivalent); `frontend/src/app/AppShell.tsx:40-83` (`ResizeHandle` — has the correct
`role="separator"` but no arrow-key resize; lower severity, has a click-based fallback)
(Phase 4.2).

### Phase 4.1: Nav, tabs, and list rows become real focusable controls
**What this delivers:** the left nav rows, tab strip, tab close button, tab overflow menu, a
library paper's open-affordance, and an experiment card's expand control are all real
`<button>`s (or have `tabIndex`/`role="button"`/`onKeyDown`) — a keyboard-only user can reach and
activate every one of them. **Depends on:** Phase 2. **Touches:** `app/AppShell.tsx`,
`library/LibraryView.tsx`, `experiments/ExperimentsBoard.tsx`.

### Phase 4.2: The code-execution approval dialog is a real modal — Phase 4 sign-off
**What this delivers:** `ApprovalPrompt` traps focus on open, returns it on close, and closes on
Escape — matching the `aria-modal` claim it already makes, for the single most safety-critical
control in the app (D31's consent gate). Also closes the two remaining icon-label/keyboard gaps:
the graph detail panel's close button gets an `aria-label`, and the mic control gets a keyboard
(and touch) equivalent to mouse hold-to-talk. Verify with a keyboard-only pass over the approval
flow and the graph/companion controls. **Depends on:** 4.1. **Touches:**
`experiments/ApprovalPrompt.tsx`, `graph/GraphView.tsx`, `companion/CompanionPane.tsx`.

---

## Phase 5: Screen-level visual conformance to UI_DESIGN.md

### What this delivers
With chrome, errors, and accessibility now sound, this phase works through the remaining
per-screen drift from UI_DESIGN.md's component specs: wrong title fonts, an incomplete
active-quote treatment (the mechanism that's supposed to make the reader and the extractive
card "light up together"), a reader missing its own header entirely, wrong relevance
presentation in the library, and shape/vocabulary drift in Feed, Graph, and the top bar.

### Depends on
Phase 2 (screen-level fixes should land on top of correct chrome/fonts, not before)

### Touches
`experiments/ExperimentsBoard.css` `.experiments__title` and `graph/GraphView.css`
`.graph__header h2` — both sans instead of the spec's `800 20px Newsreader` view-title treatment
(Phase 5.1); `reader/ReaderTab.css` `.reader__card-quote--active` — swaps background only, no
left border/radius, so passive vs. active quote states are ambiguous; `writing/ManuscriptTab.css`
`.citation-missing`/`.writing__citation-finding--missing` — conflates the active-quote token
(`--accent-tint-25`) with an unrelated error condition (Phase 5.2); `reader/ReaderTab.tsx` — no
`<header>` at all (no title, no authors/venue, no relevance segmented control on this screen);
`.reader__sidebar` (220px, no divider, vs. spec's 170px + `1px --border` right rule); selection
popover button order (`Highlight, Ask about this, Explain` vs. spec's `Ask about this ·
Highlight · Explain`) (Phase 5.3); `library/LibraryView.tsx` (segmented control instead of the
§3.2-mandated badge presentation on paper cards, no filter-chip row with counts, no
`+ Add paper`/`+ Import from arXiv` affordance); `feed/FeedView.css` `.feed__card` (panel fill +
no border instead of a raised bordered card) and `.feed__save` (outlined/tinted instead of solid
accent fill; button copy "Save to library" vs. spec's "Add to library");
`graph/GraphView.tsx` filter chips (raw `nodeType` strings — up to 9 variants — instead of the 6
canonical categories `nodeStyle.ts`'s legend already defines, worse than the original mock's
inconsistency per §9.2 item F); top bar (no global search field, no settings gear — both live as
nav rows instead; project switcher is a native `<select>`, not the spec'd chip) (Phase 5.4).

### Phase 5.1: View-title typography corrected
**What this delivers:** Experiments' and Graph's view titles switch from sans to the `800 20px
Newsreader` treatment every other screen (Dashboard, Notes, Writing, Papers) already uses.
**Depends on:** Phase 2. **Touches:** `experiments/ExperimentsBoard.css`, `graph/GraphView.css`.

### Phase 5.2: Evidence/quote treatment completed everywhere
**What this delivers:** the reader's active-quote state gets its missing left border + radius so
the reader highlight and the extractive card's field visibly light up together, matching the
passive/active distinction §3.3 defines; Writing's broken-citation error state switches from the
borrowed active-quote tint to a danger-only treatment (dashed `--danger-border`, no accent tint).
**Depends on:** 5.1. **Touches:** `reader/ReaderTab.css`, `writing/ManuscriptTab.css`.

### Phase 5.3: Reader gets its header, correct sidebar, and correct popover order
**What this delivers:** the reader shows the compact title/authors/venue header with the
relevance segmented control UI_DESIGN.md §4.2 specifies (currently relevance can only be set
from Library); the structure sidebar is 170px with its right-border rule instead of 220px with
none; the selection popover's three actions appear in spec order. **Depends on:** 5.2. **Touches:**
`reader/ReaderTab.tsx`, `reader/ReaderTab.css`.

### Phase 5.4: Library, Feed, Graph, and top-bar shape/vocabulary fixed — Phase 5 sign-off
**What this delivers:** Library shows relevance as badges (not a segmented control) with a
filter-chip row and an add-paper affordance; Feed's card is a proper raised bordered card with a
solid-accent primary save button reading "Add to library"; Graph's filter chips collapse to the
6 canonical categories the legend already documents; the top bar gains its search field and
settings gear, and the project switcher becomes the spec'd chip. Verify by walking each of the
four screens against its UI_DESIGN.md section. **Depends on:** 5.1–5.3. **Touches:**
`library/LibraryView.tsx`, `feed/FeedView.tsx`/`.css`, `graph/GraphView.tsx`, `app/AppShell.tsx`.

---

## Phase 6: Companion resilience, responsive layout, and stale UI

### What this delivers
The WebSocket reconnect-with-backoff and disconnected/reconnecting UI already work correctly
(`state/useProjectSocket.ts`, `companion/CompanionPane.tsx`) — this phase does not rebuild that.
It closes what's still missing around it: a mid-turn socket drop leaves the Stop button
permanently dead, a cited quote in the transcript doesn't scroll/highlight when clicked, there
are zero responsive breakpoints anywhere in the codebase, a Companion-created experiment never
appears on the board without a full reload, and the app's routing shape (single route, no
deep-linking) is a silent divergence from UI_DESIGN.md's route notation that deserves a
deliberate decision rather than staying an unnoticed gap.

### Depends on
Phase 3 (this phase's fixes are more valuable once the app's baseline error/race handling
is already sound)

### Touches
`companion/CompanionPane.tsx:257-260` — `turnInFlight` never clears on a mid-turn socket drop,
so the Stop button becomes permanently inert and the transcript looks stuck "in progress" even
after reconnect; `companion/parseCitations.tsx:70-77` / `reader/useAnchorSync.ts:11` — cited
spans render with no click handler at all (unshipped per the hook's own comment, not just
unstyled) (Phase 6.1); zero `@media` queries anywhere in `frontend/src`; `usePaneWidth`'s
min/max clamps (`AppShell.tsx:28-33`) are fixed-pixel drag limits unrelated to viewport width;
nav/companion only collapse via explicit click, never automatically; the center pane has no
minimum width of its own; `companion/CompanionPane.css:86-104` `.companion__bubble` has no
`overflow-wrap`/`word-break` (Phase 6.2); `app/AppShell.tsx:266-274`
(`handleCompanionUIAction` doesn't handle `log_experiment`/`update_experiment`, so an
agent-created experiment never appears without a full reload); `library/LibraryView.tsx:65-72`
(`setRelevance` has no optimistic update, looks dead during the round-trip) (Phase 6.3); no
routing library anywhere (`grep -rn "router|useNavigate|useParams|BrowserRouter"` empty in both
`src` and `package.json`) — single `/` route, tabs are server-persisted client state, no
back/forward, no deep-linking to `/p/:id/paper/:paperId` the way UI_DESIGN.md's route scheme
implies (Phase 6.4).

### Phase 6.1: Companion recovers cleanly from a mid-turn disconnect; citations become clickable
**What this delivers:** a socket drop mid-turn clears the stuck `turnInFlight` state (or surfaces
an explicit "connection lost mid-turn" error) instead of leaving Stop permanently inert; clicking
a cited-evidence span in the transcript drives `scroll_to` + `highlight_span` in the reader, the
behaviour UI_DESIGN §3.1 specifies and the code has never shipped. **Depends on:** Phase 3.
**Touches:** `companion/CompanionPane.tsx`, `companion/parseCitations.tsx`,
`reader/useAnchorSync.ts`.

### Phase 6.2: Responsive breakpoint behavior
**What this delivers:** below UI_DESIGN.md §7's ~1280px threshold, the nav collapses to icons
automatically before the companion is ever squeezed, the center pane keeps a sane minimum width
instead of being squeezable toward zero, and a long unbroken token (URL/DOI/path) in a chat
bubble wraps instead of overflowing. This needs a design call on the exact breakpoint/collapse
behavior before implementation, not just a CSS pass. **Depends on:** 6.1. **Touches:**
`app/AppShell.tsx`/`.css`, `state/usePaneWidth.ts`, `companion/CompanionPane.css`.

### Phase 6.3: Companion-created experiments and relevance clicks reflect immediately
**What this delivers:** an experiment the Companion creates or updates via `log_experiment`/
`update_experiment` appears on the Experiments board without a full app reload; clicking a
relevance segment in the Library gives immediate visual feedback instead of looking inert for
the whole PATCH round-trip. **Depends on:** 6.2. **Touches:** `app/AppShell.tsx`
(`handleCompanionUIAction`), `library/LibraryView.tsx`.

### Phase 6.4: A deliberate decision on routing/deep-linking — Phase 6 sign-off
**What this delivers:** not a code change by default — a recorded decision on whether the
single-route/no-deep-linking shape is accepted as-is (consistent with D2's local-single-user
scope and the already-accepted "tabs never unmount" call in `Issues.md` #5) or whether a router
should be introduced. Verify by confirming the decision is written down wherever this project
records such calls (e.g. alongside `Issues.md` #5), so it isn't rediscovered as a surprise gap
later. **Depends on:** 6.1–6.3.

---

## Phase 7: Hygiene and a regression safety net — final phase

### What this delivers
Everything user-visible is now fixed. This phase cleans up the token-literal drift that doesn't
look wrong yet but will silently miss a future palette change, fixes the one confirmed memory
leak, unifies the section-label letter-spacing value duplicated across ~11 files, and — since
every fix above was verified by hand — stands up a test runner and a lint config so the next
regression is caught automatically instead of requiring another manual QA pass.

### Depends on
Phase 1–6 (cleanup and tooling are lowest priority; do last so they don't need revisiting per
screen as earlier phases land)

### Touches
`companion/CompanionPane.css` (94, 180, 191, 206), `design/ErrorCard.css:28`,
`onboarding/wizard.css:79`, `search/SearchResults.css:32,116`, `notes/NotesView.css:150` —
`background:#fff`/`color:#fff` literals instead of `var(--surface-raised)`;
`experiments/ApprovalPrompt.css:4,17` — `rgba(0,0,0,…)` modal backdrop instead of an oklch
hue-210/225 value; `graph/GraphView.css:49`, `reader/ReaderTab.css:60,151` (popover shadow
opacity `0.24` vs. spec's `0.22`) — stray oklch literals duplicating token values; the ~11 files
defining their own copy of the `700 10px` uppercase section-label pattern
(`app/AppShell.css`, `dashboard/Dashboard.css`, `reader/ReaderTab.css`, `matrix/MatrixView.css`,
`graph/GraphView.css`, `experiments/ExperimentsBoard.css`, `writing/ManuscriptTab.css`,
`settings/SettingsPanel.css`, `onboarding/wizard.css`, `experiments/ApprovalPrompt.css`,
`settings/InterestProfileForm.css`), every one using `letter-spacing: 0.06em` against spec's
`0.08em` (Phase 7.1); `app/AppShell.tsx:61-72` (`ResizeHandle.handlePointerDown`) —
`window.addEventListener("pointermove"/"pointerup", …)` removed only in the `onUp` handler, no
`useEffect` cleanup, so an unmount mid-drag leaks the window-level listeners (Phase 7.1); no test
files anywhere in `frontend/src` (`*.test.*`/`*.spec.*` absent) and no test runner in
`package.json`; no ESLint config (`.eslintrc*`/`eslint.config.*` absent) despite a usefully
strict `tsconfig.json` (`strict`, `noUnusedLocals`, `noUnusedParameters`,
`noFallthroughCasesInSwitch` — `tsc --noEmit` passes clean today) (Phase 7.2).

### Phase 7.1: Token-literal cleanup and the one memory leak
**What this delivers:** every stray `#fff`/`rgba()`/duplicated-oklch literal routes through a
`tokens.css` variable so a future palette change can't silently miss it; the ~11 duplicated
section-label definitions collapse to one shared class (or are at least corrected to `0.08em`
everywhere); the `ResizeHandle` drag listeners get a proper `useEffect` cleanup so an unmount
mid-drag can't leak them. **Depends on:** Phase 1–6. **Touches:** the files listed above, plus a
possible new `design/typography.css` for the shared section-label/badge classes.

### Phase 7.2: Test runner and lint config stood up — Phase 7 sign-off, final phase
**What this delivers:** a test runner (e.g. Vitest, given the Vite toolchain already in place)
and an ESLint config exist in `frontend/`, with at least one regression test covering the
highest-value fixes from Phases 1–3 (the ErrorBoundary, the Writing dirty-flush race, the Matrix
lost-update race) so they can't silently regress, and a lint rule catching the `<div onClick>`
pattern Phase 4 just finished removing. This is the plan's final sign-off — once it's done, every
fix above ships with something other than another manual QA pass verifying it stays fixed.
**Depends on:** 7.1.

---

## What's already correct — no action needed

**Visual/theme:**
- `design/tokens.css` — full token set matches UI_DESIGN.md §1 exactly.
- `design/ErrorCard.tsx/css` — matches §3.4 error-card spec exactly; the reference
  implementation for Phase 3.1.
- `matrix/MatrixView.tsx/css` — correctly implements the extracted-vs-user provenance rule
  (§9.2/§8): quote cells use passive `accent-tint-14`, user cells are plain text, not-stated
  cells use the dashed absence pattern.
- `search/SearchResults.tsx/css` — closest full screen to spec: loading/empty/error states all
  match §4.10 and reuse `ErrorCard` correctly.
- `notes/NotesView.tsx/css`, `settings/*`, `onboarding/*` — conform to tokens and the typography
  ramp.
- Copy fix already done: §9.2 item D ("unmarked" not "unset") — confirmed correct in the
  current relevance labels.

**Functional/robustness:**
- WebSocket reconnect-with-backoff and a real disconnected/reconnecting/queued-message UI
  (`state/useProjectSocket.ts`, `companion/CompanionPane.tsx`) — genuinely works, not just
  designed. Phase 6.1 only closes the mid-turn-drop gap around it.
- `writing/useManuscriptPreview.ts`'s empty-draft compile skip, generation-counter race guard,
  and blob-URL cleanup — a solid reference implementation; contradicts the now-stale
  `Issues.md` #6 (that bug is already fixed in code).
- Onboarding + Settings forms (`ProviderForm`, `VaultStep`, `ProjectStep`, `SettingsPanel`,
  `ModelBudgetEditor`) — disabled-during-submit, client-side validation, retry, no double-submit,
  all correctly built.
- `reader/ReaderTab.tsx`'s handling of a bad/deleted paper id — degrades gracefully, no crash.
- ResizeObserver, CodeMirror view, and blob-URL cleanup elsewhere in the codebase — all correctly
  torn down in `useEffect` cleanups (the one exception is Phase 7.1's `ResizeHandle`).
- `tsc --noEmit` passes clean under a strict `tsconfig.json`; only one `any` in the whole
  codebase (`graph/GraphView.tsx:161`, a documented Cytoscape stylesheet-prop cast). No stray
  `TODO`/`FIXME`/`console.log` debris anywhere in `frontend/src`.
- `feed/FeedView.tsx`, `notes/NotesView.tsx`, `settings/SettingsPanel.tsx` — correctly import
  `ErrorCard` and catch their mutations; the reference pattern Phase 3.1 copies everywhere else.
