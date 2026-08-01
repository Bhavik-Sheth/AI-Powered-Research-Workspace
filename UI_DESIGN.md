# UI Design Reference — Research Companion OS

Visual source: Claude Design project `3ae53c3f-fac9-402c-8508-ca96be0992ca`, file
`Research Companion OS.dc.html` — **10 static screens** (01–09 plus the 05b variant).
Re-imported and reconciled **2026-08-01**. Supersedes the 2026-07-31 import (5 screens).

**Status: look-and-feel reference, not spec.** This file records *how the UI should look* —
colors, type, spacing, layout, component shapes. It does **not** outrank `DECISIONS.md`,
`Research Companion Workspace OS.md`, or any instruction given in a prompt. Where it disagrees
with them on *behaviour, data, screens, or flows*, the decisions win and the screens below have
been **corrected** — §9 logs every correction, so nothing is lost silently. Build what §1–§8 say.

> **The palette lives here.** The earlier warm off-white / sepia direction is retired; the
> palette is the **cool blueprint** look below — light-blue gridded frame, near-white panels,
> cyan accent, warm sand only behind the frame. D32 defers to this file on colour and type, and
> its remaining style rules (light-only, serif for reading, sans for chrome, recessive chrome)
> are honoured by this design.

> **What changed in this revision.** The redesign absorbed almost all of the previous
> reconciliation log: the reader is now a real PDF surface, experiments are a lab notebook,
> there is a real top bar, the companion is on every screen, relevance uses the real enum, and
> the graph is typed and provenance-coded. Five screens are **new** — Dashboard, Writing, Feed,
> the Experiment detail sheet, and a loading/empty/error gallery. Two things changed in the
> foundations: **Inter is gone** (three families now, chat is sans chrome), and the **nav is
> 200px with a `DISCOVER` group**. §9 lists what is still outstanding — the short version is
> Matrix is missing from the nav, tabs are still undecided, and Writing has no LaTeX layer.

---

## 1. Design tokens

All colors are authored in `oklch()`. Keep them in oklch — the hue-consistent ramps (hue 210 for
UI, 225 for the frame, 25 for danger) are the whole reason the palette hangs together.

### Color

| Token | Value | Use |
|---|---|---|
| `--page-bg` | `#e4e0d8` | Warm sand behind the app frame (`body`) |
| `--frame-bg` | `oklch(91% 0.079 225)` | App frame / desktop surface |
| `--frame-grid` | `oklch(52% 0.42 225 / 0.16)` | Blueprint grid lines on the frame |
| `--frame-grid-quiet` | `oklch(52% 0.42 225 / 0.07)` | **Reader only** — the grid is dimmed behind reading |
| `--surface` | `oklch(98% 0.024 210)` | Every panel: nav, content, companion, lists, sheets |
| `--surface-raised` | `#fff` | Cards, inputs, PDF pages, popovers sitting *on* a panel |
| `--surface-muted` | `oklch(93% 0.024 210)` | Assistant bubbles, tool-call chips, **PDF viewport backdrop** |
| `--surface-inactive` | `oklch(84% 0.073 210)` | Inactive tabs |
| `--accent` | `oklch(52% 0.385 210)` | Cyan. Active nav, primary buttons, sent bubbles, papers in the graph |
| `--accent-text` | `oklch(38% 0.33 210)` | Accent text on an accent tint (filled badges) |
| `--accent-text-outline` | `oklch(42% 0.2 210)` | Accent text on white (outlined badges, Stop control) |
| `--accent-tint-10/12/14/15/25` | `oklch(52% 0.385 210 / 0.10 · 0.12 · 0.14 · 0.15 · 0.25)` | companion quote (.10) · active nav row, metrics chip (.12) · passive highlight, quote block, inline citation (.14) · filled badge, active relevance segment (.15) · **active** provenance highlight (.25) |
| `--link` / `--link-hover` | `oklch(45% 0.32 210)` / `oklch(38% 0.36 210)` | Inline links, "Resume →", "open all" |
| `--text-strong` | `oklch(14–16% 0.055 210)` | Titles, headings, quote text |
| `--text` | `oklch(26–30% 0.05 210)` | Body prose |
| `--text-muted` | `oklch(46–52% 0.024–0.055 210)` | Meta, breadcrumbs, section labels, placeholders, offsets |
| `--border` | `oklch(88–89% 0.055 210)` | Panel headers, card outlines, inputs, column rules |
| `--border-faint` | `oklch(91–93% 0.02–0.03 210)` | Row dividers, segmented-control dividers, legend rules |
| `--border-dashed` | `oklch(80% 0.05 210)` | Empty / "add" / **"not stated"** / **unmarked** affordances |
| `--graph-edge` | `oklch(70% 0.05 210)` | Graph edges, both solid and dashed |

**Danger family** — errors only, never a status. Hue 25 throughout:

| Token | Value | Use |
|---|---|---|
| `--danger-rule` | `oklch(55% 0.19 25)` | `3px` left rule on error cards, `2px` on unverified quotes |
| `--danger-border` | `oklch(80% 0.09 25)` | Error card outline |
| `--danger-badge-border` | `oklch(70% 0.12 25)` | `⚠ unverified` badge outline |
| `--danger-title` | `oklch(40% 0.16 25)` | Error headline |
| `--danger-body` | `oklch(35% 0.04 25)` | Error explanation |
| `--danger-text` | `oklch(45% 0.15 25)` | `⚠ unverified` badge text |
| `--danger-fill` | `oklch(52% 0.19 25)` | Destructive / recovery button fill (`Retry source`) |

**Skeleton shimmer** — `linear-gradient(90deg, oklch(92% 0.02 210) 0%, oklch(96% 0.012 210) 50%,
oklch(92% 0.02 210) 100%)`, on bars of `13px` (title) / `9px` (lines), radius `4px`.

**Categorical palette** — the one sanctioned exception to "one accent". Used *only* for
knowledge-graph node types (§4.7) and dataviz. Equal lightness and chroma so no type reads as more
important, and each is paired with a shape so it survives color-blindness:

| Node type | Color | Shape |
|---|---|---|
| Paper | `--accent` `oklch(52% 0.385 210)` | circle |
| Author | `oklch(58% 0.14 285)` | circle with a `2px` ring (surface gap + hue ring) |
| Dataset | `oklch(58% 0.14 150)` | rounded square |
| Method / concept | `oklch(62% 0.13 75)` | hexagon |
| Code / repo | `oklch(58% 0.15 330)` | rounded square, `1.5px` **dashed** stroke, no fill |
| Idea / note | `oklch(58% 0.14 25)` | hexagon / diamond |

Outside the graph the accent is used **sparingly and always to mean "current / mine / primary"**:
the active nav row, the active tab underline, the current card's inset underline, the primary
button, the user's own chat bubble, quoted evidence. Nothing else is colored.

### Typography

**Three families. Inter has been dropped** — the chat body is now sans chrome like everything else.

| Family | Weights | Use |
|---|---|---|
| **Space Grotesk** | 500/600/700/800 | All UI chrome: nav, tabs, labels, buttons, badges, meta, graph labels, **chat bubbles**, big stat numbers |
| **Newsreader** (serif, incl. italic) | 400/600/800 | All *reading* surfaces: paper titles, note & draft body, card titles, quotes, hypotheses, detail prose |
| **JetBrains Mono** | 400/500 | Metric values + units, char offsets, tool names, LaTeX source (D34) |

Ramp:

| Role | Spec |
|---|---|
| View title (every center pane) | `800 20px Newsreader` |
| Reader paper title | `800 17px Newsreader` — the header is compact, not a hero |
| Dashboard stat value | `800 24px Space Grotesk` (a number, so sans) |
| Card title (paper, feed, experiment, graph detail) | `700 14px Newsreader`; experiments add `/1.3` + 2-line clamp |
| Note / draft body prose | `15px/1.7 Newsreader`, `max-width 640px` |
| Detail-sheet prose (hypothesis, setup, notes, graph summary) | `11px/1.6 Newsreader` |
| Quote — companion | `italic 13px Newsreader` |
| Quote — extractive card | `italic 12px/1.5 Newsreader` |
| Quote — notes | `italic 14px/1.5 Newsreader`, `max-width 600px` |
| Chat bubble | `12px` (user) / `12px/1.5` (assistant) Space Grotesk |
| Nav item | `700 12px` active / `600 12px` inactive; children `11px` |
| Section label | `700 10px`, `letter-spacing .08em`, uppercase, muted |
| Stat-tile label | `700 10px`, `letter-spacing .06em`, uppercase, muted |
| Meta / summary line | `11px` sans (`/1.5` when it wraps) |
| Badge / pill / small button | `700 10px` sans |
| Mono values | `11px` value, `10px` unit + offsets |

Serif is for *content*, sans is for *controls and conversation*, mono is for *values you might
copy*. Do not mix.

### Shape, elevation, spacing

- App frame: `border-radius: 22px`, `box-shadow: 0 20px 60px oklch(20% 0.08 225 / 0.35)`,
  grid via two `linear-gradient` 1px lines at `background-size: 24px 24px`.
  Canvas sizing in the mock: `min-width 1380px`, `max-width 1440px`, `height 82vh`
  (`560–860px`) — that is mock framing, not a product constraint (§7).
- Panels: `border-radius: 14px`, `box-shadow: 0 1px 4px oklch(26% 0.079 210 / 0.16)`, no border.
- Cards / inputs / legend: `border-radius: 10px`, `1px solid var(--border)`, no shadow
  (PDF pages add a faint `0 1px 3px`; the legend re-uses the panel shadow).
- Popover: `radius 10px`, `1px --border`, `0 4px 16px oklch(26% 0.079 210 / 0.22)`.
- Overlay sheet: no radius, `border-left: 1px --border`,
  `box-shadow: -8px 0 24px oklch(26% 0.079 210 / 0.14)`.
- Badges / pills: `radius 9px`, `padding 2–3px 8–9px`, `700 10px` sans.
- Small buttons (feed, error actions): `radius 7px`, `padding 4–5px 10–11px`, `700 10px`.
- Buttons: `height 32px`, `radius 8px`, `padding 0 14px`, `700 12px` sans. Inline variants
  (`Find related`, `Expand neighbours`) `radius 8px`, `padding 6–7px 12px`, `700 11px`.
- Chat input: `height 36px`, `radius 10px`; mic and send buttons are `36px` circles.
- Gaps: `12px` between shell columns, `8–14px` inside panels, `24px` grid unit on the frame,
  `24px` between PDF pages.
- Panel padding: `14px` (nav rail / companion / detail panels), `16px 22px` (content headers),
  `18–24px` (content bodies), `12–14px` (sheet + sidebar).

---

## 2. Shell layout — D32

```
┌─ frame: light-blue blueprint grid, radius 22, big soft shadow ─────────────────┐
│ TOP BAR  [Attention Sinks ▾] · Papers / Scaling Sparse   [Search…] [⚙] [RK]    │
│ ┌──────────┐ ┌─────────────────────────────────┐ ┌───────────────────────────┐ │
│ │ LEFT NAV │ │ CENTER: active view (routed)    │ │ COMPANION — every screen  │ │
│ │  200px   │ │            flex:1               │ │          280px            │ │
│ └──────────┘ └─────────────────────────────────┘ └───────────────────────────┘ │
└─ padding: 0 12px 12px, column gap 12px ───────────────────────────────────────┘
```

Three columns **on every screen** — all ten mocks now hold this. The companion is never dropped,
never a modal, never replaced by the center pane — it is one WebSocket session per project that
survives center-pane navigation (D32; this is the USP). Views that want a secondary list, detail
column, or overlay sheet (Notes, Writing, Experiments, Graph, Reader) take it out of the center
pane's width, not the companion's.

### Top bar (40px, sits directly on the frame — not a panel)

Left → right: **project switcher** chip (`--surface` @ 35% fill, `1px accent` @ 40% border,
radius 8, `700 13px`, trailing `▾`) · **breadcrumb / active title** (`12px` muted sans,
ellipsized — e.g. `Papers / Scaling Sparse Retrieval`, `Notes / Why the scoring head works`,
`Search · "retrieval heads"`) · then right-aligned: **global search** field (`height 32px`,
`width 220px`, radius 8, `--surface-raised`, `1px --border`, placeholder "Search everything…";
**active state = `2px solid --accent`** and it holds the query text) · **settings** `⚙`
(`15px`, muted). **There is no account avatar or account menu** — the app is
single-user and has no auth (D1); the mock's avatar is dropped.

No in-app window chrome — the Electron shell owns the native window (D2). The top bar is the
app's own, not a title bar.

### Left nav (`200px`, `min-width 170px`, `padding 14px 0`, row gap `2px`)

Starts directly with `Dashboard` (the project switcher lives in the top bar). Groups:

```
            Dashboard
LIBRARY     Papers  ▾ (expandable tree of the project's papers)
            Notes
WORK        Experiments
            Writing
DISCOVER    Graph
            Feed
            Matrix          ← required by D27/D32; missing from the mock (§9.2 A)
```

Every routed center view must be reachable from here; search is reachable from the top bar.

**Row states.** *Active:* `border-left: 3px solid var(--accent)`, `background: accent/0.12`,
`margin: 0 8px`, `border-radius: 0 6px 6px 0`, `padding: 8px 14px 8px 11px`, 9px accent square
bullet (radius 2), label `700 12px` at `oklch(20% 0.055 210)`. *Inactive:*
`border-left: 3px solid transparent`, bullet `oklch(50% 0.055 210 / 0.35)`, label `600 12px` at
`oklch(35% 0.055 210)`. *Expandable row* prefixes a `10px ▾` glyph. *Expanded children:*
`padding-left 34px`, `11px`, `padding-top 7px`, connected by a `1px solid oklch(85% 0.055 210)`
left rule at `margin-left 20px` (the last child drops the rule and closes the group); the current
child is `700` at `oklch(25%)`, siblings `400` at `oklch(45%)`.

**Group labels:** `700 10px`, `letter-spacing .08em`, muted, `padding: 12px 14px 4px 22px`.

### Tabs (center pane, reader only — still provisional)

Bottom-attached to the content panel, `padding: 8px 6px 0`, `gap 2px`: `flex:1 1 0;
max-width:200px`, radius `10px 10px 0 0`, `700 12px`. Active = `--surface` +
`box-shadow: 0 -2px 0 var(--accent) inset` + trailing `✕`. Inactive = `--surface-inactive`,
centered, ellipsized, `oklch(38%)` text. A `+` glyph (26px circle, no chrome) at the end.
The panel below uses `border-radius: 0 12px 14px 14px` so it welds to the active tab.

> **Still undecided.** D32's router owns *one* center pane per URL; multi-tab open papers remains
> an invention of the mock and a real interaction-model change (routing, persistence, whether the
> companion follows the active tab). Default to single-pane-per-route until it is decided; the tab
> styling above is ready if it goes the other way. See §8.

---

## 3. Cross-screen components

These recur on most screens; defined once here, referenced by §4.

### 3.1 Companion pane — D18 node 5 / D36 / D24

`280px`, `min-width 220px`, `padding 14px`, `gap 10px`. Top to bottom: status line → transcript
→ composer.

- **Status line** — `7px` accent dot + italic `11px` muted (`Companion · <project>`, or a live
  status like "reading paper…"), driven by `status` events. While a turn runs, a **stop control**
  sits right-aligned: `✕ Stop`, `700 10px` upright sans, `1px accent/0.5` outline, radius 9,
  `padding 3px 8px`, text `--accent-text-outline`. Interrupt is first-class (D18 node 5) so it is
  visible, not hidden. **Idle turns hide it** (screen 09).
- **Transcript**, five visually distinct kinds — this split *is* the D24 provenance rule made
  visible, so never collapse it:
  - *User:* accent fill, white, `radius 10px 10px 2px 10px`, right-aligned, `max-width 85%`,
    `12px` sans, `padding 8px 12px`.
  - *Assistant reasoning:* `--surface-muted`, radius 10, `12px/1.5` sans, `padding 10px 12px`.
  - *Cited evidence:* its own block — `border-left: 2px solid accent`, `accent/0.10`,
    `radius 0 8px 8px 0`, `italic 13px Newsreader`, trailing superscript `[n]` in `700 10px`
    accent sans, underlined at `2px` offset. Clickable → drives `scroll_to` + `highlight_span`
    in the center pane.
  - *Tool-call chip:* inline, `--surface-muted`, radius 9, `padding 5px 9px`, `700 10px` **mono**
    tool name + a `9px` ring spinner (`1.5px` accent, transparent top).
  - *Tool-result card:* what the chip collapses into — `--surface-raised`, radius 10,
    `1px --border`, `padding 9px 11px`: a `700 10px` `.06em` header (`SEARCH_LIBRARY · 3 MATCHES`),
    up to 3 result rows at `11px` sans, then an `open all` link in accent. Rendered from `ui_view`
    by id (D17/D18 — the frontend never re-derives it).
  - *Unverified claim* (span failed the substring validator): the evidence block with
    `border-left: 2px solid var(--danger-rule)`, **no tint**, plus a `⚠ unverified` badge —
    `700 10px`, `1px --danger-badge-border`, radius 9, text `--danger-text`.
- **Empty state** — `34px` dashed circle, then `12px/1.5` "No conversation yet in this project."
  and `11px/1.5` "Ask about a paper, a claim, or your notes.", centered. Composer still present.
- **Composer** — `36px` input, radius 10, `1px --border`, white, placeholder "Ask the companion…";
  then a **mic button** (36px circle, outlined, white, drawn as a capsule + base glyph) and the
  accent **send** circle (`36px`, `800 14px ↑`, white). Reserve the mic now even while voice is
  post-Slice-1 (D36); a typing-only composer is the thing D36 warns against.

### 3.2 Relevance — the four-value enum (D22 / D25)

One enum, `project_papers.relevance`: `relevant | somewhat | not | unset`. Three presentations:

| Presentation | Where | Style |
|---|---|---|
| **Segmented control** | Reader header | 4 segments in one `1px --border` white box, radius 8, `padding 5px 9px`, `1px` divider between segments. Selected = `accent/0.15` fill + `700` `--accent-text`; unselected = `600` muted. Label `RELEVANCE` in `700 9px .06em` sits to its left. |
| **Badge** | Paper card, search result | `radius 9px`, `padding 2px 8px`, `700 10px` — `relevant` = accent/0.15 fill + `--accent-text`; `somewhat` = `1px accent/0.5` outline + `--accent-text-outline`; `not relevant` = `1px oklch(85% 0.03 210)` outline + muted; `unmarked` = `1px dashed --border-dashed` + muted. |
| **Filter chip** | Papers header | The same four badge styles with a trailing count (`relevant 6`). Toggling filters the grid. |

**Copy:** show `not` as "not relevant" and `unset` as "unmarked" everywhere. The mock's reader
segment says "unset" — that leaks the enum value into the UI; use "unmarked" (§9.2 D).

### 3.3 Quote / evidence treatment (D24)

Two intensities, used consistently across reader, extractive card, companion, notes, writing, and
matrix:

- **Passive** — `accent/0.14`, radius 3 (inline in prose) or 6–8 (block), no rule. The default
  for any verbatim span.
- **Active** — `accent/0.25` + `border-left: 2px solid accent`, `radius 0 3px 3px 0` (inline) or
  `0 6px 6px 0` (block). Exactly one active span at a time; it is what `highlight_span` sets, and
  the reader highlight and the extractive-card field light up **together**.

Never render a quote as plain body text.

### 3.4 Absence and error blocks

- **"not stated" / unsupported / add-affordance** — `1px dashed --border-dashed`, no tint,
  radius 6–10, `italic` muted sans copy. Used for `not stated in this paper` (extractive card),
  `unsupported claim — no linked source yet` (writing), `+ add metric`, `+ Import from arXiv`,
  the empty-state panel, and the `unmarked` badge. **A real state you draw, never an omission.**
- **Error card** — `--surface-raised`, radius 10, `1px --danger-border`,
  `border-left: 3px solid --danger-rule`, `padding 12–14px 14–16px`: `700 12px --danger-title`
  headline, `11px/1.6 --danger-body` explanation, then recovery actions — primary in
  `--danger-fill`, secondary outlined neutral. The danger family is for **errors only**; it is
  never a status value.

---

## 4. Screens

Nine views are drawn, plus one variant. The remaining four (§8) have no mock — derive them
from §1–§3.

### 4.1 Dashboard — `/p/:id` (screen 01) **new**

The project's landing view. Header is the project name (`800 20px Newsreader`). Body, `gap 18px`:

1. **Stat row** — `grid-template-columns: repeat(4, minmax(0,1fr))`, `gap 12px`. Each tile is a
   card: `700 10px .06em` label (`PAPERS` / `NOTES` / `EXPERIMENTS` / `FEED`), `800 24px` sans
   value, then a `10px` muted qualifier that is always the *actionable* subset — `4 unmarked`,
   `3 unlinked`, `1 in progress`, `new since Tue`. The qualifier is the point; a bare count is not.
2. **`CONTINUE WHERE YOU LEFT OFF`** — section label, then rows (card, `padding 12px 14px`,
   `gap 12px`): a `9px` square bullet (accent for the live item, muted for the rest), a
   `600 13px Newsreader` title, `11px` muted context (`page 4 of 11`, `note · edited 2h ago`), and
   a right-aligned link action (`Resume →` / `Open →`) in `--link`. Resume position is real state,
   not a guess.
3. **`NEEDS ATTENTION`** — a stack that mixes two severities deliberately:
   - *Soft nudges* — dashed card, `12px` sans, with the operative word emphasised inline
     (`4 papers are still *unmarked* for relevance`; `2 experiments sit in **remaining** with no
     next step`). These are prompts, not failures, so they get the dashed treatment (§3.4).
   - *Real errors* — the §3.4 error card (`Semantic Scholar API key failed validation`, D13).

### 4.2 Reader — `/p/:id/paper/:paperId` (screen 02; D32 / D33 / D23 / D24 / D33)

The center pane is **not an article view**. It renders the real PDF; the parsed structure is a
navigation and provenance overlay on top of it. **The frame grid drops to `--frame-grid-quiet`
behind this screen** — reading wins over decoration.

```
┌ tab strip ────────────────────────────────────────────────────┐
├ content panel (radius 0 12 14 14) ────────────────────────────┤
│ header: title · authors · venue          [RELEVANCE ▮▮▮▮]     │
├──────────┬──────────────────────────────────┬─────────────────┤
│STRUCTURE │  PDF pages on --surface-muted    │ EXTRACTIVE CARD │
│  170px   │  real layout, highlight overlay  │      270px      │
│ ‹ collap.│  on the text layer               │   › collapsible │
└──────────┴──────────────────────────────────┴─────────────────┘
```

- **Header** (`padding 12px 18px`, bottom border) — title `800 17px Newsreader` ellipsized,
  authors + venue `11px` muted beneath, and the relevance segmented control (§3.2) right-aligned.
  Deliberately compact: the PDF is the hero, not the chrome.
- **Structure sidebar** (`170px`, `min 150px`, `1px --border` right rule) — collapsible via a
  `‹` glyph beside the first label. Uppercase `700 10px` groups:
  `SECTIONS` (docling headings, jump-to — reuses the nav-row active state at `border-left 3px` +
  `accent/0.12`, `700 11px`), `REFERENCES` (a count, `42 cited works`, opening the full list),
  `DATASETS` (inline names), `CODE` (repo URL in `--link`).
- **PDF canvas** — the viewport is `--surface-muted` so the pages read as paper. Pages are
  `--surface-raised` cards, `max-width 420px`, radius 10, `1px --border`, `0 1px 3px` shadow,
  `padding 22px 24px`, `24px` apart, internal `gap 9px`. Figures and tables are boxed placeholders
  (`oklch(96% 0.01 210)`, `1px oklch(90% 0.02 210)`, radius 4) with a small caption. Highlights
  paint on the **text layer** using the §3.3 passive / active pair.
- **Extractive card** (`270px`, `min 240`, `1px --border` left rule) — a pane inside the panel,
  not an overlay; collapsible via `›` in its `EXTRACTIVE CARD` header. One block per field
  (`PROBLEM / METHOD / DATASETS / RESULTS / LIMITATIONS`), `gap 14px`: uppercase `700 10px` muted
  label → the **verbatim quote** (§3.3, `italic 12px/1.5 Newsreader`, radius 6, `padding 7px 10px`)
  → `10px` mono `§N Section · start–end`. The whole block is clickable → `scroll_to` +
  `highlight_span`; the field carrying the currently-highlighted span uses the **active** quote
  variant, so card and page are lit in sync. **"not stated"** renders as the §3.4 dashed block
  with italic muted sans copy — `not stated in this paper`.
- **Selection popover** — appears on text selection, anchored below the selection:
  `--surface-raised`, radius 10, `1px --border`, `0 4px 16px` shadow, three `700 11px` sans
  actions split by `1px` rules — `Ask about this` · `Highlight` · `Explain`.

### 4.3 Papers library — `/p/:id/papers` (screen 03)

Header: `Papers` title, then a right-aligned **relevance filter-chip row** (§3.2 —
`relevant 6 · somewhat 3 · not 1 · unmarked 4`) and the primary `+ Add paper` button.

Body: `grid-template-columns: repeat(3, minmax(0,1fr))`, `gap 14px`, `padding 18px 22px`. Card =
`--surface-raised`, radius 10, `1px --border`, `14px` pad, `gap 8px`: `700 14px Newsreader` title
/ `11px` muted `Authors et al. · venue year` / `11px/1.5 Newsreader` one-line summary / relevance
badge. The current card carries the `box-shadow: 0 -2px 0 accent inset` underline. The last cell
is the dashed `+ Import from arXiv` placeholder.

### 4.4 Notes — `/p/:id/notes` (screen 04)

`nav │ note list 220px │ editor │ companion`.

- **List** (`220px`, `1px --border` right rule, `14px` pad): `All Notes` header (`700 12px`),
  then rows of `700 12px` title + `10px` muted link line — `Linked to <paper>` or **`Unlinked`**,
  which is a first-class state, not a blank. Selected row = `accent/0.12` + `3px` accent left
  border, `radius 0 8px 8px 0`.
- **Editor** (`min-width 320px`): header (title `800 20px Newsreader`, right-aligned
  `Linked to <link>` in `11px` muted with the paper as an actual link, bottom border), then a
  **markdown editor** — serif `15px/1.7` at `max-width 640px`, quote blocks in the §3.3 passive
  treatment at `italic 14px/1.5`, radius 8, `max-width 600px`, mono for code spans.

Content is user-authored ground truth (D18 node 4): always editable, never AI-overwritten.

### 4.5 Experiments — `/p/:id/experiments` (screen 05; lab notebook, not a run tracker — D17 / D29)

Header: `Experiments` + a `11px` muted subtitle `lab notebook` + primary `+ New experiment`.

Board of four columns by `status`, `flex:1 1 0` each with `min-width 190px` and `12px` gutters,
scrolling independently: `PLANNED · REMAINING · IN PROGRESS · DONE`. Each column header is an
uppercase `700 10px` label with the count beside it in `700 10px` muted.

Card (`--surface-raised`, radius 10, `1px --border`, `12px` pad, `gap 7px`): the **hypothesis is
the title** (`700 14px/1.3 Newsreader`, 2-line clamp), a `11px/1.5` muted setup summary, then a
chip row — a metrics-count chip (`3 metrics`, accent/0.12 fill + `--accent-text`) and one
outlined-neutral chip per linked paper. The selected card gets the inset accent underline and a
`›` open affordance beside its title.

There is no "failed" experiment status — the danger family is reserved for genuine errors.

### 4.6 Experiment detail — overlay sheet (screen 05b)

Opening an experiment slides a **`300px` right-hand sheet over the board**, inside the center
pane: `--surface`, `border-left 1px --border`,
`box-shadow: -8px 0 24px oklch(26% 0.079 210 / 0.14)`, no radius. **The companion is not
covered** — the board loses width, the companion never does.

- **Header** (`12px 14px`, bottom border): `700 11px` experiment name (ellipsized), a
  right-aligned **status badge** (outlined accent = `in progress`), and a `✕` close.
- **`HYPOTHESIS` / `SETUP`** — `11px/1.6 Newsreader`.
- **`METRICS`** — a 3-column grid `1fr auto auto` (`gap 0 10px`): name in `11px` sans, value in
  `11px` **mono** right-aligned, unit in `10px` **mono** muted (`—` when unitless). Rows separated
  by `1px --border-faint`; the last row drops its divider. Below it a dashed `+ add metric` button
  (`700 10px`, accent text, radius 8). Variable-length and **user-authored** — the AI never fills
  these.
- **`NOTES`** — markdown, same serif treatment.
- **`LINKS`** — **typed** relationship chips, outlined neutral, `radius 9px`, `padding 3px 8px`:
  `inspired by · Scaling Sparse Retrieval`, `uses dataset · HotpotQA`, `references note · Why the
  scoring head works`. The relation name is part of the chip, because these are graph edges (D26).

Status badges (§3.2 vocabulary reused): `done` = accent-tint filled · `in_progress` = outlined
accent · `planned` / `remaining` = outlined neutral.

### 4.7 Knowledge graph — `/p/:id/graph` (screen 07; D26 / D26)

Header (`14px 20px`): `Knowledge Graph` title, then **type filter chips** — one per node type,
each a `700 10px` pill with its categorical color dot. *On* = `1px` solid border **in the type's
own hue** + `--surface` fill + `oklch(25%)` label. *Off* = `1px --border`, transparent fill,
muted label — the dot keeps its hue so you can still see what you turned off. Right-aligned
primary `Find related`.

- **Canvas** — SVG, force-directed at build (Cytoscape / react-force-graph). **Nodes are colored
  *and* shaped** per the §1 categorical table; each carries a two-line label — `700 10–11px` name
  over a smaller muted type word (`paper`, `author`, `dataset`, …). Dark labels on light fills,
  white on the accent. The selected node gets a `3px` ring in its own hue.
- **Edges** — `1.5px --graph-edge`. **Solid = metadata-derived** (cites / authored_by /
  uses_dataset / has_code, from OpenAlex / S2 / PwC). **Dashed (`stroke-dasharray: 5 4`) =
  LLM-derived** (method_of, idea→paper). That dash *is* the provenance tag (D26) — a trust graph
  must show which edges were inferred. Edge labels appear on hover only.
- **Legend** — bottom-left `--surface-raised` card, radius 10, `1px --border`, panel shadow: a
  `LEGEND` label, one `11px` swatch row per node type **rendered in that type's real shape**, then
  a `1px` divider and two line samples — solid `from metadata`, dashed `LLM-inferred`. The legend
  documents both encodings, not just color.
- **Detail panel** (`250px`, `min 200`, `1px --border` left rule, `14px` pad): the node's type
  chip, `700 14px Newsreader` name, `11px/1.6 Newsreader` summary that states the connection
  counts, then an `EDGES` group listing relations **grouped by provenance** —
  `cites · authored-by · uses-dataset · has-code (metadata)` / `method-of · idea→paper (inferred)`,
  with the parenthetical in muted. Bottom actions: `Expand neighbours` (accent fill) and `Open`
  (outlined) → paper opens the reader, note opens Notes.

### 4.8 Writing — `/p/:id/write/:docId` (screen 06) **new**

`nav │ drafts list 200px │ editor │ companion`.

- **Drafts list** (`200px`, right rule, `14px` pad): `Drafts` header, then rows of `700 12px`
  title + `10px` muted `1,240 words · 6 cites`. Selected row uses the same accent/0.12 +
  left-border treatment as Notes.
- **Editor**: header with the draft title (`800 20px Newsreader`) and a right-aligned
  `Export BibTeX` action (`11px` muted). Body is serif `15px/1.7` at `max-width 640px`, and
  **inline citations are quote-tinted**: `(Sarthi et al., 2024)` on `accent/0.14`, radius 3,
  `padding 0 3px`. A citation is evidence, so it gets the evidence treatment (D24).
- **Unsupported claim** — the §3.4 dashed block, `max-width 600px`, italic muted sans:
  `unsupported claim — no linked source yet`. This is the writing-side twin of "not stated": the
  UI shows you where your prose has outrun your sources.

> The mock draws only the prose-and-citations layer. D34 requires an in-browser WASM LaTeX
> pipeline — CodeMirror source in **JetBrains Mono**, a compiled preview, and a compile-error
> state. Those are not drawn; see §9.2 C.

### 4.9 Feed — `/p/:id/feed` (screen 08) **new**

Header: `Feed` + right-aligned `11px` muted `matched to this project's topics`. Body is a single
column of cards, `gap 10px`, `padding 16px 22px` — deliberately not a grid: this is a queue you
work through, not a library you browse.

Card (`--surface-raised`, radius 10, `1px --border`, `14px` pad, `gap 6px`): `700 14px Newsreader`
title, then a `11px` muted meta line that **always states why the item is here** —
`arXiv · 2 days ago · matches "scoring head", "reranking"` or
`arXiv · 1 week ago · cites 2 papers in your library`. Then two small buttons (`radius 7px`,
`700 10px`): `Add to library` (accent fill, white) and `Dismiss` (outlined neutral).

Never show a feed item without its match reason — an unexplained recommendation is not a
trust-graph affordance.

### 4.10 Search results + state gallery — `/p/:id/search` (screen 09) **new**

Reached from the top-bar search field, which switches to its `2px` accent active state; the
breadcrumb reads `Search · "retrieval heads"`. Header: `Results for "<query>"` +
`11px` muted `federated across 4 sources`.

Screen 09 doubles as the state gallery (its `LOADING` / `EMPTY` / `ERROR` section labels are mock
scaffolding, not product UI). The three states are real and belong on every list surface:

- **Loading (streaming).** Search is a federated fan-out + rerank (D20/D21) and is slow, so it
  **streams**: an `11px` ring spinner (`1.5px` accent, transparent top) beside a per-source
  progress line — `arXiv answered · Semantic Scholar, OpenAlex, local library pending`. Below it
  the normal 3-up result grid, with **real cards and shimmer skeletons side by side** as sources
  land. A skeleton is a card with four shimmer bars (`13px` @72%, `9px` @42%, `9px` @100%,
  `9px` @86%). Never a single blocking spinner.
- **Empty.** Dashed panel, radius 10, `oklch(96% 0.02 210 / 0.5)` fill, `28px` pad, centered:
  `600 13px` headline (`No results in your library`), `11px/1.6` explanation capped at `340px`
  that names the query and offers the way out, then a primary CTA (`+ Import from arXiv`).
- **Error.** The §3.4 error card. Note the copy pattern: name the source, say what happened, and
  **say what still worked** — "Other sources returned normally — these results are incomplete."
  Actions: `Retry source` (danger fill) + `Dismiss`. A partial federation failure degrades the
  page; it never blanks it (D23 graceful degradation).

The companion shows its §3.1 empty state here, with **no Stop control** — nothing is running.

---

## 5. Rules to carry into the build

1. **Three panes, always.** Nav + center + companion on every screen. Panels float on the grid
   with 12px gutters; never a flat full-bleed page. Secondary columns and overlay sheets come out
   of the center pane's width.
2. **Serif = content, sans = chrome and chat, mono = values.** Three families, no Inter, no serif
   buttons, no sans body prose.
3. **Accent means current / primary / mine** — roughly six uses per screen. The graph's
   categorical palette is the only exception, and it never leaks into chrome.
4. **Quoted evidence always gets the accent tint** — reader page, extractive card, companion,
   notes, writing citations, matrix cells. Passive `.14`, active `.25` + left rule, one active at
   a time (§3.3). Never render a quote as plain body text (D24).
5. **Absence is a state you draw** (§3.4): dashed border, no tint, italic muted. `not stated`,
   `unmarked`, `Unlinked`, `unsupported claim`, `+ add metric`, empty lists — all the same
   treatment, so absence reads as deliberate rather than broken.
6. **Danger is for errors only.** Never a status value, never a "failed" experiment.
7. **Anything the user can click, the Companion can do** — and both resolve to the same tool call
   + route transition (D17/D18). Don't add UI-only capabilities the agent can't reach.
8. **Always show provenance.** Solid vs dashed graph edges, metadata vs inferred edge groups,
   `⚠ unverified` on failed span validation, feed match reasons, mono `§section · offsets` on every
   extracted quote. If the system inferred it, the UI says so.
9. **Measure caps at 600–640px** for prose regardless of pane width.
10. **Status = badge**, bound to a real enum: tinted-fill = terminal-good, outlined-accent =
    active, outlined-neutral = pending, dashed = unset. Never a bare colored dot.
11. **Depth is one soft shadow** — `0 1px 4px` on panels, `0 4px 16px` on popovers,
    `-8px 0 24px` on sheets, `0 20px 60px` on the frame. Panels have no border; borders belong to
    cards, inputs, and dividers.
12. **The grid recedes behind content.** `0.07` opacity on the reader; consider it everywhere (§7).

---

## 6. States the design still doesn't draw

Screen 09 covers loading / empty / error for **search**. These remain:

- **Per-paper processing.** PDF fetch, parse, embed, and extraction are queued jobs (D9). The
  library card and the reader header need a visible processing state; the extractive card needs
  a "still extracting" state distinct from "not stated".
- **Degraded full text (D23).** Paywalled or unfetchable source → "abstract only, plus a link".
  Never a fabricated extractive card.
- **Dropped WebSocket.** The companion's session is the USP; its disconnected and reconnecting
  states must exist, and the composer must say whether a queued message will send.
- **LaTeX compile errors (D34).** See §4.8.
- **Empty variants** for notes, experiments, graph, feed, drafts — reuse the §4.10 empty panel.
- **Interaction states.** Hover, active, disabled, and `:focus-visible` on every interactive
  element. Keyboard navigability and WCAG-AA are standing constraints; the mock already sets a
  global `:focus-visible` of `2px solid var(--accent)` at `2px` offset with `radius 4px` — keep it.

---

## 7. Accessibility and contrast

- Verify AA on muted text (`oklch(46–52%)` on `oklch(98%)`) and on tinted badges before shipping.
  The `700 10px` badge/label size is the riskiest combination in the system, and it is now used in
  more places (filter chips, link chips, legend, stat labels) than in the previous revision.
- The `8.5px` PDF body type and small figure captions in the mock are **mock scale**, not a spec —
  real PDF.js pages render at their own size.
- Panels separate from the frame by shadow alone. Confirm the panel edge is discernible at low
  brightness; add a `1px oklch(94% 0.02 210)` hairline if not.
- The reader dims the blueprint grid to `0.07`. Do the same on Writing and Notes — any screen
  whose job is sustained reading — and consider `0.07` globally.
- Graph encodes type by **color and shape**, and edge provenance by **dash**; the legend shows
  both. Never color alone. Filter chips keep the hue dot when toggled off, so the off state is not
  signalled by color removal alone.
- The mock's `min-width: 1380px` is a canvas convenience. The real app needs a responsive story
  for ~1280px and below: the nav collapses to icons before the companion is ever dropped, since
  dropping the companion breaks the product's premise (D32).

---

## 8. Not designed yet

- **Three screens have no mock:** onboarding wizard (D35), settings / BYO-key + local-model
  config (D13), and the **literature matrix** (D27, route `/matrix/:id`). Derive from §1–§3.
  *(An auth screen was previously listed here and is now **dropped** — there is no auth, D1.)*
- **Matrix cell provenance treatment** — cells carry `source: extracted | user` (D27). Extracted
  cells get the §3.3 quote treatment and click through to the span; user cells get plain body
  type. Needs drawing.
- **Center-pane tabs** — still open, see §2. Default to one center pane per route.
- **Writing's LaTeX layer** — source / preview split, compile errors (D34). See §4.8.
- **Force-graph library** — Cytoscape vs react-force-graph, pick at build (D26 leaves it open).
- **Reader references list** — the sidebar shows `42 cited works` as a count; the expanded list
  is undrawn.

---

## 9. Reconciliation log

### 9.1 Resolved in this revision

The 2026-07-31 log listed 11 corrections against the old 5-screen mock. The new design **adopts
9 of them**; these are no longer corrections, they are what the design shows:

| # | Was corrected from | Now drawn as | Authority |
|---|---|---|---|
| 1 | Reader as reflowed article prose | PDF pages + structure sidebar + extractive side pane + selection popover | D32, D33 |
| 2 | Experiments as an ML run-tracker | Lab-notebook board + hypothesis / setup / user metrics / notes / typed links | D17, D29 |
| 3 | Fake macOS title bar, switcher in the nav | Real top bar: switcher · breadcrumb · search · settings (no account — no auth) | D32, D1, D2 |
| 4 | Companion missing on several screens | Companion on all ten screens | D32 |
| 5 | Relevance badges `Relevant / Baseline / Related` | The real enum, in three presentations (§3.2) | D22, D25 |
| 6 | Graph: papers only, no controls | Six typed node shapes, solid vs dashed edges, filters, legend, provenance-grouped edges | D26, D26 |
| 7 | Extractive card as a row of chips | Collapsible side pane, verbatim quote + mono offsets per field, real "not stated" | D32, D24, D33 |
| 8 | Companion = bubbles only | Stop control, tool chips, tool-result cards, mic, `⚠ unverified` | D18 node 5, D36, D24 |
| 10 | No mono family | JetBrains Mono for metric values, units, offsets, tool names | D32, D29 |
| 11 | Notes read-only | Markdown editor, user-authored | D25, D18 node 4 |

Item 9 (nav reaching every routed view) is **partly** resolved — see §9.2 A.

### 9.2 Still outstanding — the design is wrong or silent; §1–§8 are right

| # | Design shows | Build instead | Authority |
|---|---|---|---|
| A | Nav reaches 7 views; **Matrix is absent** | Add `Matrix` under `DISCOVER`. Every routed view must be reachable from the nav | D27, D32 (`/matrix/:id`, `open_view(matrix\|…)`) |
| B | Three open-paper tabs + `+` in the reader | Unresolved. Default to one center pane per route; the tab styling in §2 is ready if it flips | D32 router |
| C | Writing is prose + citation chips only | Add the LaTeX source / preview split (CodeMirror, mono) and the compile-error state | D34 |
| D | Reader relevance segment labelled `unset` | Show `unmarked`; `unset` is the enum value, not UI copy. Keep it consistent with the library's chips | D25 |
| E | Reader header has no source / code links | Acceptable — the repo moved into the structure sidebar's `CODE` group. Add a source `↗` link there too, for D23's "abstract only, plus a link" degradation | D23 |
| F | Filter chip reads `Method`; legend reads `Method / concept` | Pick one vocabulary and say it once. Chips may abbreviate for space, but document that they do | D26 |
| G | No onboarding, settings, or matrix screens | Derive from §1–§3. No auth screen is needed (D1) | D13, D27, D35 |
| H | Frame grid at `0.16` on 9 of 10 screens | Dim to `0.07` on all reading-heavy screens; consider globally | §7, D32 "chrome recedes" |
| I | Fixed `min-width: 1380px` canvas | Real responsive behaviour; collapse the nav before ever dropping the companion | D32 |

### 9.3 Foundation changes to be aware of

If you built against the 2026-07-31 revision, these touch existing markup:

- **Inter dropped.** Chat body is now Space Grotesk `12px/1.5`. Three families, not four.
- **Nav `220px → 200px`** (`min 170px`); **companion `min-width 200px → 220px`**.
- **`VIEWS` group renamed `DISCOVER`**; Writing sits under `WORK`.
- **Reader paper title `800 30px → 800 17px`** — the header is compact chrome, not a hero.
- **Relevance moved from a dropdown badge to a 4-segment control** in the reader header.
- **Experiment detail `260px` column → `300px` overlay sheet** over the board (companion untouched).
- **Notes list `260px → 220px`.**
- **`accent/0.25` added** as the active-highlight tint, pairing the reader highlight with the
  extractive-card field.
- **Danger expanded** from two tokens to seven (§1), because errors now have cards and buttons,
  not just text.
