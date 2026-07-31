# UI Design Reference — Research Companion OS

Visual source: Claude Design project `3ae53c3f-fac9-402c-8508-ca96be0992ca`, file
`Research Companion OS.dc.html` (5 static screens). Imported and reconciled 2026-07-31.

**Status: look-and-feel reference, not spec.** This file records *how the UI should look* —
colors, type, spacing, layout, component shapes. It does **not** outrank `DECISIONS.md`,
`Research Companion Workspace OS.md`, or any instruction given in a prompt. Where it disagreed
with them on *behaviour, data, screens, or flows*, the decisions won and the screens below have
been **corrected** — §8 logs every correction, so nothing is lost silently. Build what §1–§7 say.

> **One deliberate override — the palette.** D30 §Style says *academic & warm — warm off-white /
> sepia neutrals*. The design is a **cool blueprint** look: light-blue gridded frame, near-white
> panels, cyan accent, warm sand only behind the frame. The design is newer and is what the user
> pointed at, so **build the cool palette**. Every other part of D30 (light-only, serif for
> reading, sans for chrome, recessive chrome) stands and the design already honours it.

---

## 1. Design tokens

All colors are authored in `oklch()`. Keep them in oklch — the hue-consistent ramps (hue 210 for
UI, 225 for the frame) are the whole reason the palette hangs together.

### Color

| Token | Value | Use |
|---|---|---|
| `--page-bg` | `#e4e0d8` | Warm sand behind the app frame (`body`) |
| `--frame-bg` | `oklch(91% 0.079 225)` | App frame / desktop surface |
| `--frame-grid` | `oklch(52% 0.42 225 / 0.16)` | Blueprint grid lines on the frame |
| `--surface` | `oklch(98% 0.024 210)` | Every panel: nav, content, companion, lists |
| `--surface-raised` | `#fff` | Cards, inputs, PDF pages sitting *on* a panel |
| `--surface-muted` | `oklch(93% 0.024 210)` | Assistant chat bubbles, tool-call chips |
| `--surface-inactive` | `oklch(84% 0.073 210)` | Inactive tabs |
| `--accent` | `oklch(52% 0.385 210)` | Cyan. Active nav, primary buttons, sent bubbles, papers in the graph |
| `--accent-text` | `oklch(38% 0.33 210)` | Accent text on an accent tint (badges) |
| `--accent-tint-10/12/14/15` | `oklch(52% 0.385 210 / 0.10 · 0.12 · 0.14 · 0.15)` | Quote blocks (.14), active nav row (.12), badges (.15), companion quote (.10) |
| `--link` / `--link-hover` | `oklch(45% 0.32 210)` / `oklch(38% 0.36 210)` | Inline links |
| `--text-strong` | `oklch(14–16% 0.055 210)` | Titles, headings |
| `--text` | `oklch(26–30% 0.05 210)` | Body prose |
| `--text-muted` | `oklch(46–50% 0.024–0.055 210)` | Meta, breadcrumbs, section labels, placeholders |
| `--border` | `oklch(88–89% 0.055 210)` | Panel headers, card outlines, inputs |
| `--border-faint` | `oklch(92% 0.03 210)` | Row dividers |
| `--border-dashed` | `oklch(80% 0.05 210)` | Empty / "add" / **"not stated"** affordances |
| `--danger-border` / `--danger-text` | `oklch(88% 0.03 25)` / `oklch(45% 0.15 25)` | Errors only — failed key validation, LaTeX compile errors, unverified citations |
| `--graph-edge` / `--graph-edge-faint` | `oklch(70% 0.05 210)` / `oklch(80% 0.03 210)` | Graph edges |

**Categorical palette** — the one sanctioned exception to "one accent". Used *only* for
knowledge-graph node types (§3.6) and dataviz. Equal lightness and chroma so no type reads as more
important, and each is paired with a shape so it survives color-blindness:

| Node type | Color | Shape |
|---|---|---|
| Paper | `--accent` `oklch(52% 0.385 210)` | circle |
| Author | `oklch(58% 0.14 285)` | circle, thin ring |
| Dataset | `oklch(58% 0.14 150)` | rounded square |
| Method / concept | `oklch(62% 0.13 75)` | hexagon |
| Code / repo | `oklch(58% 0.15 330)` | rounded square, dashed stroke |
| Idea / note | `oklch(58% 0.14 25)` | diamond |

Outside the graph the accent is used **sparingly and always to mean "current / mine / primary"**:
the active nav row, the active tab underline, the primary button, the user's own chat bubble,
quoted evidence. Nothing else is colored.

### Typography

| Family | Weights | Use |
|---|---|---|
| **Space Grotesk** | 500/600/700/800 | All UI chrome: nav, tabs, labels, buttons, badges, meta, graph labels |
| **Newsreader** (serif, incl. italic) | 400/600/800 | All *reading* surfaces: paper titles, note body, card titles, quotes, detail-panel prose |
| **Inter** | 400 | Assistant message body in the companion pane (`12px/1.5`) |
| **JetBrains Mono** | 400/600 | CodeMirror LaTeX source, metric values + units, char offsets, API-key `…last4` |

Ramp: page title `800 30px Newsreader`; view title `800 20px Newsreader`; body prose `16px/1.65`
(reader) and `15px/1.7` (notes) Newsreader; card title `700 14px Newsreader`; nav item
`600–700 12px`; section label `700 10px` with `letter-spacing:.08em`, uppercase, muted; meta
`11px`; chat `12px`.

Serif is for *content*, sans is for *controls*, mono is for *values you might copy*. Do not mix.

### Shape, elevation, spacing

- App frame: `border-radius: 22px`, `box-shadow: 0 20px 60px oklch(20% 0.08 225 / 0.35)`,
  grid via two `linear-gradient` 1px lines at `background-size: 24px 24px`.
- Panels: `border-radius: 14px`, `box-shadow: 0 1px 4px oklch(26% 0.079 210 / 0.16)`, no border.
- Cards / inputs: `border-radius: 10px`, `1px solid var(--border)`, no shadow.
- Badges/pills: `border-radius: 9–11px`, `padding: 2–3px 8–10px`, `700 10–11px` sans.
- Buttons: `height 32px`, `radius 8px`, `padding 0 14px`, `700 12px` sans.
- Chat input: `height 36px`, `radius 10px`; send button `36px` circle, accent fill, `↑`.
- Gaps: `12px` between the shell columns, `10–16px` inside panels, `24px` grid unit on the frame.
- Panel padding: `14px` (nav/companion), `16–22px` (content headers), `18–26px` (content body).

---

## 2. Shell layout — D30

```
┌─ frame: light-blue blueprint grid, radius 22, big soft shadow ─────────────────┐
│ TOP BAR  [Attention Sinks ▾] · Papers / Scaling Sparse   [Search…] [⚙] [avatar] │
│ ┌──────────┐ ┌─────────────────────────────────┐ ┌───────────────────────────┐ │
│ │ LEFT NAV │ │ CENTER: active view (routed)    │ │ COMPANION — every screen  │ │
│ │  220px   │ │            flex:1               │ │          280px            │ │
│ └──────────┘ └─────────────────────────────────┘ └───────────────────────────┘ │
└─ padding: 0 12px 12px, column gap 12px ───────────────────────────────────────┘
```

Three columns **on every screen**. The companion is never dropped, never a modal, never replaced
by the center pane — it is one WebSocket session per project that survives center-pane navigation
(D30; this is the USP). Views that want a secondary list or detail column (Notes, Experiments,
Graph) take it out of the center pane's width, not the companion's.

### Top bar (40px, sits directly on the frame — not a panel)

Left → right: **project switcher** chip (`--surface` @ 35% fill, `1px accent` @ 40% border,
radius 8, `700 13px`, trailing `▾`) · **breadcrumb / active title** (`12px` muted sans, ellipsized)
· then right-aligned: **global search** field (`height 32px`, radius 8, `--surface-raised`,
`1px --border`, `max-width 220px`, placeholder "Search everything…") · **settings** `⚙` ·
**account** avatar (22px circle, `1.5px` muted border, opens the account menu).

No window chrome. This is a browser app (D1) — the mock's macOS traffic lights are gone.

### Left nav (`220px`, `min-width:170px`)

Starts directly with its first group label (the project switcher lives in the top bar). Groups:

```
            Dashboard
LIBRARY     Papers  ▾ (expandable tree of the project's papers)
            Notes
WORK        Experiments
            Writing
VIEWS       Matrix
            Graph
            Feed
```

Every routed center view is reachable from here; search is reachable from the top bar.

**Row states.** *Active:* `border-left: 3px solid var(--accent)`, `background: accent/0.12`,
`margin: 0 8px`, `border-radius: 0 6px 6px 0`, 9px accent square bullet, label `700 12px` at
`oklch(20%)`. *Inactive:* `border-left: 3px solid transparent`, bullet
`oklch(50% 0.055 210 / 0.35)`, label `600 12px` at `oklch(35%)`. *Expanded children:* indented
`36px`, `11px`, connected by a `1px solid oklch(85% 0.055 210)` left rule at `margin-left:20px`;
current child is `700`.

### Tabs (center pane, reader only — provisional)

Bottom-attached to the content panel: `flex:1 1 0; max-width:200px`, radius `10px 10px 0 0`,
`700 12px`. Active = `--surface` + `box-shadow: 0 -2px 0 var(--accent) inset` + `✕` close.
Inactive = `--surface-inactive`, centered, ellipsized. A `+` glyph (26px, no chrome) at the end.
The panel below uses `border-radius: 0 12px 14px 14px` so it welds to the active tab.

> **Undecided.** D30's router owns *one* center pane per URL; multi-tab open papers is an
> invention of the mock and a real interaction-model change (routing, persistence, whether the
> companion follows the active tab). Default to single-pane-per-route until it's decided; the tab
> styling above is ready if it goes the other way. See §7.

---

## 3. Screens

Five are drawn in the mock and corrected here. The remaining seven (§7) have no mock — derive them
from §1–§2.

### 3.1 Reader — `/p/:id/paper/:paperId` (D30 / Q7 / D8 / Q18 / Q33)

The center pane is **not an article view**. It renders the real PDF; the parsed structure is a
navigation and provenance overlay on top of it.

```
┌ content panel ────────────────────────────────────────────────┐
│ breadcrumb strip                                              │
│ paper header: title · authors · venue/year · [relevance ▾]    │
│               [source ↗] [code ↗]              [card ⧉ toggle]│
├──────────┬──────────────────────────────────┬─────────────────┤
│STRUCTURE │  PDF.js canvas (scrollable)      │ EXTRACTIVE CARD │
│ ~200px   │  real pages, layout intact,      │ side sheet 300px│
│collapsib.│  highlight overlay on the        │ toggleable      │
│          │  text layer                      │                 │
└──────────┴──────────────────────────────────┴─────────────────┘
```

- **Paper header** — title `800 30px Newsreader`; meta row `13px` muted sans with the
  **relevance selector** (a badge that is also a dropdown, §4.8) and outlined `code available` /
  source-link badges.
- **Structure sidebar** — collapsible, `--surface` on `--surface`, separated by `1px --border`.
  Uppercase `700 10px` group labels: `SECTIONS` (docling headings, jump-to; nav-row active state
  reused) / `REFERENCES` / `DATASETS` / `CODE`.
- **PDF canvas** — pages are `--surface-raised` cards (radius 10, `1px --border`) on the panel,
  `24px` apart. Highlights paint on the text layer at `accent/0.14`; the *active* provenance
  highlight is `accent/0.25` with a `2px` accent left rule at the span's start.
- **Extractive card side sheet** — toggleable, pushes rather than overlays. One block per field
  (`PROBLEM / METHOD / DATASETS / RESULTS / LIMITATIONS`): uppercase `700 10px` muted label, then
  the **verbatim quote** in the quote-block treatment (§4.4), then `10px` mono `section · offsets`.
  Whole block is clickable → `scroll_to` + `highlight_span`; hover raises the block to
  `accent/0.18`. **"not stated"** renders as the same block with `1px dashed --border-dashed`, no
  tint, and italic muted `12px` sans "not stated" — a real state, never omitted (Q18).
- **Selection popover** — appears on text selection, anchored below: `--surface-raised`, radius 10,
  `1px --border`, `0 4px 16px` shadow, three `700 11px` sans actions —
  `Ask about this` · `Highlight` · `Explain`.

### 3.2 Companion pane (every screen) — D20 node 5 / D23 / Q18

`280px`, `min-width 200px`. Top to bottom: status line → transcript → composer.

- **Status line** — `7px` accent dot + italic `11px` muted ("reading paper…"), driven by `status`
  events. Add a **stop control** to its right while a turn runs: `✕ Stop`, `700 10px`, outlined,
  radius 9 — interrupt is first-class (D20 node 5), so it must be visible, not hidden.
- **Transcript**, three visually distinct kinds — this split *is* the Q18 provenance rule made
  visible, so never collapse it:
  - *User:* accent fill, white, `radius 10px 10px 2px 10px`, right-aligned, `max-width 85%`.
  - *Assistant reasoning:* `--surface-muted`, radius 10, Inter `12px/1.5`.
  - *Cited evidence:* its own block — `border-left: 2px solid accent`, `accent/0.10`,
    `radius 0 8px 8px 0`, italic Newsreader, superscript `[n]` in accent sans, clickable → drives
    `scroll_to` + `highlight_span` in the center pane. Cursor pointer + underline on hover so the
    click affordance is legible.
  - *Unverified claim* (span failed the substring validator): same block but
    `border-left: 2px solid var(--danger-border)`, no tint, with a `⚠ unverified` badge in
    `--danger-text`.
- **Tool-call chip** — inline, `--surface-muted`, radius 9, `700 10px` mono tool name + spinner;
  collapses into a **tool-result card** on completion (`--surface-raised`, radius 10,
  `1px --border`, title + up to 3 result rows + "open" action, rendered from `ui_view` by id).
- **Composer** — `36px` input, radius 10, plus a **mic button** (36px circle, outlined, `🎙`) to
  the left of the accent send button. Reserve the space now even while voice is post-Slice-1
  (D23); a typing-only composer is the thing D23 warns against.

### 3.3 Papers library — `/p/:id`

Header: `Papers` title, right-aligned filter/search field (`max-width 180px`) + primary
`+ Add paper`. Body: `grid-template-columns: repeat(3, minmax(0,1fr))`, `gap:14px`. Card =
`--surface-raised`, radius 10, `1px --border`, `14px` pad — serif title / sans meta / serif
one-line summary / **relevance badge**. The current card carries the `0 -2px 0 accent inset`
underline. Last cell is a dashed `+ Import from arXiv` placeholder.

Badges bind to the real `project_papers.relevance` enum (§4.8) — `relevant | somewhat | not |
unset`. The mock's `Baseline` / `Related` are not values of anything.

### 3.4 Notes — nav │ note list 260px │ editor │ companion

List: `All Notes` label, then rows of `700 12px` title + `10px` muted "Linked to X"; selected row
= `accent/0.12` + 3px accent left border, radius `0 8px 8px 0`. Editor: header (title
`800 20px Newsreader`, "Linked to *link*" right, bottom border), then a **markdown editor** —
serif `15px/1.7` at `max-width 640px`, mono for code spans, quote blocks in the §4.4 treatment.
Content is user-authored ground truth (D20 node 4): it is always editable, never AI-overwritten.

### 3.5 Experiments — lab notebook, not a run tracker (D19 / Q19)

Board of four columns by `status`: `PLANNED · REMAINING · IN PROGRESS · DONE` — uppercase
`700 10px` muted headers with a count, `12px` gutters, each column scrolling independently.
Card per experiment (`--surface-raised`, radius 10, `1px --border`, `14px` pad): hypothesis as the
title (serif `700 14px`, 2-line clamp), `11px` muted setup summary, a metrics count chip, and
linked-paper chips. Primary action is `+ New experiment`.

Detail panel (`260px`) for the selected experiment:

- `HYPOTHESIS` / `SETUP` — serif `11px/1.6`.
- `METRICS` — a 3-column mini table `name | value | unit`, `--border-faint` row dividers, values
  and units in **mono**. Variable-length and **user-authored** — the AI never fills these. Add-row
  affordance at the bottom.
- `NOTES` — markdown.
- `LINKS` — inspired-by paper / uses-dataset / references-note chips (graph edges).

Status badges (§4.8): `done` = accent tint filled · `in_progress` = outlined accent ·
`planned` / `remaining` = outlined neutral. There is no "failed" experiment status — the danger
pair is reserved for genuine errors.

### 3.6 Knowledge graph — `/p/:id/graph` (D15 / D28)

Header: `Knowledge Graph` title, then **type filter chips** (one per node type, each carrying its
categorical color dot; toggling filters the view) and a `Find related` action. Canvas fills the
rest; force-directed at build (Cytoscape / react-force-graph).

- **Nodes** — colored *and shaped* by type per the §1 categorical table. Selected node gets a
  `3px` ring in its own hue; label `700 10–11px` sans, dark on light fills, white on the accent.
- **Edges** — `1.5px --graph-edge`. **Solid = metadata-derived** (cites / authored_by /
  uses_dataset / has_code, from OpenAlex / S2 / PwC). **Dashed = LLM-derived** (method_of,
  idea→paper). That dash *is* the provenance tag (D15) — a trust graph must show which edges were
  inferred. Edge labels appear on hover only.
- **Legend** — bottom-left of the canvas, `--surface-raised` card, one row per visible type.
- **Detail panel** (`240px`): `Selected: <node>`, type badge, serif summary, `Expand neighbours` +
  `Open` actions (paper → reader, note → notes).

---

## 4. Rules to carry into the build

1. **Three panes, always.** Nav + center + companion on every screen. Panels float on the grid with
   12px gutters; never a flat full-bleed page.
2. **Serif = content, sans = chrome, mono = values.** No sans body prose, no serif buttons.
3. **Accent means current/primary/mine** — roughly six uses per screen. The graph's categorical
   palette is the only exception, and it never leaks into chrome.
4. **Quoted evidence always gets the accent-tint block or the accent left-rule** — reader card,
   notes, chat, matrix cells. Never render a quote as plain body text (Q18).
5. **"not stated" is a state you draw**, not an empty cell: dashed border, no tint, italic muted.
   Same for `unset` relevance and unverified citations.
6. **Anything the user can click, the Companion can do** — and both resolve to the same action +
   route transition (D19/D20). Don't add UI-only capabilities the agent can't reach.
7. **Measure caps at 600–640px** for prose regardless of pane width.
8. **Status = badge**, bound to a real enum: tinted-filled = terminal-good, outlined-accent =
   active, outlined-neutral = pending, danger pair = **error only**. Never a bare colored dot.
9. **Depth is one soft shadow** — `0 1px 4px` on panels, `0 20px 60px` on the frame. Panels have no
   border; borders belong to cards, inputs, and dividers.

---

## 5. States the mock never drew — build these

- **Loading:** search is a federated fan-out + rerank (D6/D25) and is slow — needs a streaming
  skeleton, not a spinner. PDF fetch/parse, embedding, and extraction are queued jobs (D14): show
  per-paper processing state in the library card and reader header.
- **Empty:** every list (no papers, no notes, no experiments, empty feed, empty graph) needs the
  dashed-placeholder treatment already used by `+ Import from arXiv`.
- **Error:** failed key validation (D26), failed PDF fetch / paywalled-source degradation (D8 —
  "abstract only, plus a link", never a fabricated card), LaTeX compile errors (D16), dropped
  WebSocket.
- **Interaction:** hover, active, disabled, and `:focus-visible` on every interactive element.
  Keyboard navigability and WCAG-AA are standing constraints; a visible focus ring in
  `--accent` at `2px` offset is the default.

---

## 6. Accessibility and contrast

- Verify AA on muted text (`oklch(46–50%)` on `oklch(98%)`) and on accent-tint badges before
  shipping. The `700 10px` badge/label size is the riskiest combination in the system.
- Panels separate from the frame by shadow alone. Confirm the panel edge is discernible at low
  brightness; add a `1px oklch(94% 0.02 210)` hairline if not.
- The blueprint grid is loud for a product whose goal is "UI recedes; content is the star" — drop
  its opacity (or the grid entirely) behind the reader.
- Graph encodes type by color **and** shape, and edge provenance by dash — never color alone.

---

## 7. Not designed yet

- **Seven screens have no mock:** auth (D27), onboarding wizard (D29), project dashboard,
  search/discovery (D25), literature matrix (D28), research feed, writing/LaTeX (D16/D30),
  settings (D26/D27). Derive from §1–§2.
- **Center-pane tabs** — open decision, see §2. Default to one center pane per route.
- **Force-graph library** — Cytoscape vs react-force-graph, pick at build (D28 leaves it open).
- **Matrix cell provenance treatment** — cells carry `source: extracted | user` (D28). Extracted
  cells get the quote-block treatment and click through to the span; user cells get plain body
  type. Needs drawing.

---

## 8. Reconciliation log — what was changed from the mock, and why

Kept for traceability. The mock is *wrong* on these; §1–§7 are right.

| # | Mock showed | Corrected to | Authority |
|---|---|---|---|
| 1 | Reader as reflowed article prose | PDF.js canvas + structure sidebar + extractive-card side sheet + selection popover | D30, Q7 — explicitly "not a reflowed text reader" |
| 2 | Experiments as an ML run-tracker: `RUN/DATASET/RECALL@10/LATENCY`, `Complete/Running…/Failed`, "+ New run" | Lab-notebook board: hypothesis / setup / user-authored `metrics[{name,value,unit}]` / notes / `planned·remaining·in_progress·done` / graph links | D19, Q19 — live run tracking is explicit future scope |
| 3 | Fake macOS title bar (traffic lights), project switcher in the left nav, no global search or account menu | Real top bar: project switcher · breadcrumb · global search · settings · account | D30 ("top bar + 3-pane shell"), D1 (browser-only) |
| 4 | Companion pane absent on Notes / Experiments / Graph | Companion on every screen | D30 — persistent per-project session, "the USP" |
| 5 | Relevance badges `Relevant / Baseline / Related` | `relevant / somewhat / not / unset` | D7, D21 schema |
| 6 | Graph: papers only, all identical, no controls | Six node types, color + shape coded; solid vs dashed edges for metadata vs LLM provenance; type filters; legend | D15, D28 |
| 7 | Extractive card as a row of five chips | Toggleable side sheet, one verbatim quote per field, click-through to span, real "not stated" state | D30, Q18, Q33 |
| 8 | Companion = status line + bubbles only | Added stop control, tool-call chips, tool-result cards, mic button, unverified-claim state | D20 node 5, D23, Q18 |
| 9 | Left nav reached 4 destinations | All routed views reachable; search in the top bar | D30 routing |
| 10 | No mono family | JetBrains Mono for LaTeX source, metric values, offsets | D30 (CodeMirror/LaTeX), Q19 (metrics) |
| 11 | Notes shown read-only | Markdown editor; user-authored ground truth | D21 schema, D20 node 4 |
