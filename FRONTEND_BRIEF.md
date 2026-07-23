# Research Companion OS — Frontend Brief

Companion to `DECISIONS.md` (D1–D30) and `Research Companion Workspace OS.md` (vision). This is
the buildable spec: screens, flows, style, and the full data schema the frontend and backend are
built around. Cite decision IDs where relevant.

---

> ## ⚠️ Read before iterating on the design
>
> **Restyle freely; do NOT change the load-bearing structure.** The visual layer (colors,
> fonts, spacing, component look, layout polish) is open to iteration. But the following are
> **architecture, not decoration** — they encode the harness (D20) and provenance (Q18/Q33)
> decisions, and breaking them is a real spec change, not a design tweak:
>
> 1. **The persistent 3-pane shell + always-present Companion chat.** The chat is one
>    persistent per-project session on the *right*, visible alongside the center view at all
>    times. Do not turn it into a modal, a separate page, or something that replaces the content
>    pane. The simultaneity (see content + ask about it) is the whole point.
> 2. **Cross-pane quote-anchor linking.** Companion citation ↔ extractive-card field ↔ PDF span
>    must stay click-through-linked (Q33). A citation you can't click to its source is broken.
> 3. **Provenance is visual (Q18).** Quoted evidence must render *visually distinct* from AI
>    reasoning everywhere (reader answers, cards, matrix cells); "not stated" is a real state.
> 4. **One interaction path.** Anything the user can click, the Companion can also do via a tool
>    (and vice-versa) — both resolve to the same action + route transition (D19/D20). Don't add
>    UI-only capabilities the agent can't reach.
> 5. **Voice-ready shell.** The chat input is a transport into the persistent session; keep room
>    for a mic control (D23). Don't design a chat that assumes typing-only.
>
> **If an iteration makes you want to change behavior** (a new screen, different data on a
> screen, a changed flow, or any of the five above): stop and reconcile it into `DECISIONS.md`
> **and** this brief first, so the two never drift apart. Style ≠ spec — this file is the spec.

---

## Product summary

A voice-ready, AI-powered research workspace for solo researchers: one persistent project home
for finding, reading, organizing, and writing about papers — with an always-present **AI
Companion** that drives every capability and grounds every answer in cited evidence. The AI
assists (discovery, retrieval, organization); the researcher does all the thinking and writing.

## Target users

Solo researchers, grad students, and independent ML/CS researchers who read many papers, take
notes, run experiments, and write papers in LaTeX. Single-user per workspace (D1 amended); free;
BYO LLM API key (D9). v1 is a **fully web-based, desktop-browser** app (no mobile, no native
desktop app).

---

## The shell (persistent on every workspace screen) — D30

```
┌───────────────────────────────────────────────────────────────────────┐
│ TOP BAR: [Project ▼] · breadcrumb / active title      [search] [⚙ acct]│
├───────────────┬───────────────────────────────────┬───────────────────┤
│ LEFT NAV      │ CENTER: active view               │ RIGHT: COMPANION   │
│ (tree)        │ (reader / matrix / graph /        │ chat (persistent   │
│  Papers       │  experiments / feed / writing /   │ per-project        │
│  Notes        │  dashboard)                       │ WebSocket session) │
│  Experiments  │                                   │                    │
│  + views      │                                   │  transcript        │
│               │                                   │  [input] 🎙(future)│
└───────────────┴───────────────────────────────────┴───────────────────┘
```

- **Top bar** — project switcher, breadcrumb/active-paper title, global search entry, account &
  settings menu.
- **Left nav** — tree of the project's papers (relevant-papers library), notes, experiments; +
  entry points to Graph / Matrix / Feed / Writing views.
- **Center** — the active view; **its content is what the URL owns** (routing below).
- **Right — the Companion chat** — one persistent WebSocket session **per project** (D20/D30),
  always visible, survives center-pane navigation. This is the USP.

**Routing** (React Router, D30): URL owns project + center pane only.
`/p/:projectId` · `/p/:projectId/paper/:paperId` · `/p/:projectId/matrix/:matrixId` ·
`/p/:projectId/graph` · `/p/:projectId/experiments` · `/p/:projectId/feed` ·
`/p/:projectId/write/:docId`. Chat + nav are shell, not routes. Agent `ui_action` events drive
the same router (open_paper, scroll_to, open_view…).

**State** (D30): React Query (REST) + WebSocket event bus (harness stream) + Zustand (local UI
state = the `ui_state` payload sent up each turn).

---

## Screens

1. Auth / entry
2. Onboarding wizard
3. Project dashboard (center view)
4. Search / discovery (center view)
5. Reader (center view)
6. Literature matrix (center view)
7. Knowledge graph (center view)
8. Experiments (center view)
9. Research feed (center view)
10. Writing workspace / LaTeX (center view)
11. Companion chat (persistent right pane)
12. Settings (models, storage, interest profile, account)

---

### 1. Auth / entry — D27

- **Purpose:** get the user into a session. Real account or demo.
- **Shown:** app name/tagline; "Continue with Google", "Email + password", and a secondary
  "Explore demo (no account)" link.
- **Actions:** Google OAuth (also grants Drive scope), email/password sign-in/up, anonymous
  demo sign-in.
- **Navigation:** new real user → Onboarding wizard; returning user → last project dashboard;
  demo → straight into a scratch project (limited: needs a key for the agent, Supabase storage
  only, browser-bound).

### 2. Onboarding wizard — D29 (gated; completing it yields a working project)

- **Purpose:** collect everything a project needs to actually work, upfront.
- **Steps / fields:**
  1. **Account** — Google or email/password (skipped if already authed).
  2. **Models** — add ≥1 BYO API key (provider dropdown: Google/Groq/OpenAI/Anthropic/
     OpenRouter/DeepSeek/Custom-base-URL); **validate on save** (test call → show available
     models); select **primary model** + optional **auxiliary model**. Free-tier links
     (Groq / Google AI Studio) shown inline.
  3. **First project** — name; optional/skippable **focus seed** (a sentence + optional seed
     papers by search/link).
  4. **Storage** — "Connect Google Drive" (BYO blobs) or "Use default storage" — **skippable**.
- **Actions:** next/back, validate key, skip (steps 3-seed and 4), finish.
- **Navigation:** finish → Project dashboard for the new project.

### 3. Project dashboard (center view)

- **Purpose:** the project's home / at-a-glance state.
- **Shown:** project name + editable **interest profile** chips (categories/keywords, D25/Q32);
  recent papers, recent notes, experiment status summary (planned/in-progress/done counts), feed
  teaser (N new), quick stats (papers read, notes, highlights).
- **Actions:** new search, add paper (link/upload), new note, new experiment, open feed, edit
  interest profile, open writing doc.
- **Navigation:** cards link to Reader / Notes / Experiments / Feed / Matrix / Graph / Writing.

### 4. Search / discovery (center view) — D6/D25/D7

- **Purpose:** federated academic search.
- **Shown:** search box (natural language) + filter controls (year, venue, has_code, author);
  ranked results list. **Each result card (list stage, D7):** title, authors, venue, year,
  citation count, code-available badge, source badge(s) (arXiv/OpenAlex/S2), abstract summary,
  source link. Loading = streaming/skeleton while fan-out + rerank run.
- **Actions:** run search, apply/clear filters, open paper (→ Reader; triggers processing),
  add-to-project, mark relevance inline. Results are a cached `result_id` set (D22).
- **Navigation:** result → Reader (`/paper/:paperId`).
- **Note:** search is also invokable from the Companion ("find papers on X") → same results view.

### 5. Reader (center view) — D8/D7/Q18/Q33/D30 — *most complex screen*

- **Purpose:** read the real PDF with the AI as reading companion; provenance-linked.
- **Shown:**
  - **PDF.js** rendering the real PDF + text/annotation overlay (existing highlights drawn).
  - **Structure sidebar** (collapsible): docling sections (jump-to), references list, cited
    datasets, code/repo links (D22 `get_paper(include=[…])`).
  - **Toggleable extractive card** (side sheet): Problem / Method / Datasets / Results /
    Limitations — each field is a **verbatim quote** with a "not stated" state (Q18); each field
    is **clickable → scroll_to + highlight_span** the source span in the PDF.
  - Paper meta header: title, authors, venue/year, relevance selector, source/code links.
- **Actions:**
  - **Select text → inline popover:** "Ask about this" (sends selection as ambient `ui_state`,
    focuses chat), "Highlight/Save" (`create_highlight`), "Explain".
  - Mark relevance (relevant / somewhat / not); open reference (→ its Reader); open code repo;
    toggle card; add note from selection.
  - Cross-pane: a Companion citation (quote span) is clickable → drives `scroll_to` +
    `highlight_span` here.
- **Navigation:** references → other papers' Reader; "compare" → Matrix; back to Search.
- **Anchoring:** highlights + card fields share the `{quote, prefix, suffix}` + cached
  page/bbox anchor (Q33); survives re-parsing; degrades to text-only if the PDF blob is
  unavailable (D8).

### 6. Literature matrix (center view) — D28/Q46

- **Purpose:** side-by-side comparison table of selected papers.
- **Shown:** rows = selected papers; columns = **standard** (Problem/Method/Datasets/Results/
  Limitations — projected from extractive cards, no re-extraction) + **Personal notes** (user) +
  any **custom columns**. Each cell shows the value; extractive cells link to the source span;
  cells carry a `source: extracted | user` indicator.
- **Actions:** add/remove papers, add **custom column** (triggers per-paper scoped extraction,
  "not stated" fallback), **edit any cell** (→ becomes user-authored, flagged), reorder columns,
  export.
- **Navigation:** paper cell → Reader; "build matrix from these" from Search/Graph.
- **Persistence:** saved project artifact (`matrices` table).

### 7. Knowledge graph (center view) — D15/D28/Q45

- **Purpose:** visual exploration of relationships.
- **Shown:** project-scoped graph — nodes: papers, authors, datasets, methods/concepts, code,
  ideas/notes; edges: cites, authored-by, uses-dataset, has-code, method-of, idea→paper. Node
  color/shape by type. Provenance-tagged (LLM edges only from opened papers).
- **Actions:** click node → detail panel + open (paper→Reader, note→Notes); expand neighbors;
  filter by node/edge type; search within graph; "find related".
- **Navigation:** node → its screen; selection → "build matrix / compare".
- **Rendering note:** a force-directed graph lib (e.g. Cytoscape / react-force-graph) — pick at
  build; data comes from `get_graph(node_id, depth)` (D22).

### 8. Experiments (center view) — D19/Q19

- **Purpose:** the researcher's lab notebook.
- **Shown:** list/board of experiments grouped by status (planned / remaining / in-progress /
  done). Per experiment: hypothesis, setup, **metrics table** `[{name, value, unit}]`, notes
  (markdown), linked papers/datasets/notes.
- **Actions:** create/edit experiment, set status, add/edit metric rows, link to paper/dataset/
  note (graph edges), delete. (Outcomes are **user-authored** — AI never fills results.)
- **Navigation:** links → Reader / Notes / Graph; metrics can be pulled into a Matrix.

### 9. Research feed (center view) — D20-feed/Q20/Q32

- **Purpose:** personalized stream of new papers for the project.
- **Shown:** ranked new-paper cards (title/meta/abstract + **why-relevant**: matched keywords/
  categories + similarity); daily-refreshed.
- **Actions:** **save** (→ library, boosts profile), **dismiss** (→ never resurfaces, light
  down-weight), open (→ Reader), edit interest profile.
- **Navigation:** card → Reader; "edit interests" → Settings/interest-profile.

### 10. Writing workspace / LaTeX (center view) — D16/Q52/D30 — Slice 3

- **Purpose:** distraction-free LaTeX authoring with live preview; researcher authors, AI
  assists with syntax/organization only.
- **Shown:** split pane — **left: CodeMirror 6** LaTeX source (syntax highlight, **live inline
  KaTeX math**), **right: debounced SwiftLaTeX WASM PDF preview** (~1–2s) + compile-error panel.
  Toolbar above editor; document/file tree if multi-file.
- **Actions:** edit source; toolbar inserts (sections, equation, figure, table, `\cite`,
  `\ref`, bold/italic); autocomplete (commands, `\cite{}` from project references, `\ref/\label`);
  insert **image** (upload → VFS → `\includegraphics`), **Mermaid** diagram, **dataviz export**
  (PNG/SVG); recompile; ask Companion for syntax help / citation insertion / missing-citation &
  consistency checks. **AI never writes prose sections** (standing constraint).
- **Navigation:** citation search links to project papers; back to project.

### 11. Companion chat (persistent right pane) — D20/D30 — *the USP*

- **Purpose:** the always-on AI companion that drives every capability and grounds answers.
- **Shown:** streamed transcript — user turns, assistant `text_delta`, **status** steps
  ("searching…", "reading paper…"), **tool-call** chips, **tool-result** cards (rendered from
  `ui_view` by id), inline **citations** (clickable → drive reader/graph). Interrupt (stop)
  control. Input box (text now; **mic button for voice — future**, D23).
- **Data:** WebSocket event stream (down: status/text_delta/tool_call/tool_result/ui_action/
  turn_complete/error; up: user_message + `ui_state` snapshot + incremental pushes + interrupt).
- **Actions:** send message, interrupt turn, click a citation (→ scroll/highlight in reader or
  focus a graph node), click a result card action, (future) push-to-talk.
- **Navigation:** the chat itself doesn't navigate — its `ui_action` events move the **center
  pane** via the router. Persistent across the whole project session.

### 12. Settings — D26/D27/D29/Q37

- **Purpose:** manage keys, models, storage, profile, account.
- **Shown / sections:**
  - **Models & keys** — per-provider keys (masked `…last4`), validity status, primary +
    auxiliary model selectors.
  - **Storage** — connected drive (Google/OneDrive) or default; storage usage (soft quota).
  - **Interest profile** — editable categories/keywords/synonyms per project (D25/Q32).
  - **Account** — sign-in/upgrade (anonymous→real), sign out, delete data.
- **Actions:** add/remove/validate key, select models, connect/disconnect drive, edit profile,
  upgrade account, export (markdown — future), sign out.

---

## Key user flows

1. **First run (real):** Entry → Google sign-in (grants Drive) → wizard (key+validate → project
   +seed → storage) → Dashboard → search → open paper (Reader) → ask-about-highlight (Companion)
   → mark relevant → note.
2. **Demo:** Entry → "Explore demo" (anonymous) → scratch project → prompted for BYO key at
   first agent action → search/read/notes (Supabase storage) → "Sign in to keep your work"
   upgrade.
3. **Read & ground:** Reader → select equation → "Ask about this" → Companion answers with
   inline citations (visually distinct from reasoning, Q18) → click citation → PDF scrolls +
   highlights the span.
4. **Companion-driven navigation (voice-ready):** type/say "compare this with RAPTOR" →
   Companion runs `compare` tool → opens Matrix (`ui_action`) → cells cite spans in both papers.
5. **Feed → library:** daily feed → save item (→ library, profile shifts) or dismiss (→ gone).
6. **Write:** Writing view → draft LaTeX → live math preview → debounced PDF compile → insert
   figure + `\cite` (autocomplete from project refs) → ask Companion "check missing citations".
7. **Experiment log:** Experiments → new experiment (hypothesis/setup) → status in-progress →
   add metric rows on results → link to the paper that inspired it.

---

## Style prefs — D30

- **Aesthetic:** Academic & warm — scholarly, calm, readable. UI recedes; content is the star.
- **Theme:** **Light only** (single theme).
- **Palette:** warm off-white / sepia neutrals; soft contrast; one restrained accent; minimal
  chrome, subtle borders.
- **Typography:** **serif for reading** (Charter / Lora / Source Serif class) at comfortable
  measure and line length; clean sans for UI chrome/controls; mono for code/LaTeX (CodeMirror)
  and metrics.
- **Tone/mood:** quiet, focused, professional, long-reading-session friendly.

## Reference inspiration

- **Readwise Reader / iA Writer** — warm, reading-first calm (primary look-and-feel).
- **Linear / Obsidian** — restraint, keyboard-friendliness, "tool for thought" polish.
- **Cursor / Claude Code** — the persistent-companion-alongside-content shell (structure, not
  the dark IDE look).
- **Overleaf** — the split-source/PDF-preview writing surface.

## Constraints

- **Desktop web browser only** (no mobile layout, no native/desktop app) — v1. Chromium
  recommended (Web Speech API voice later, D23; File-System niceties).
- **Thin frontend (D19):** no business logic on the client; render server state, capture input,
  lazily fetch `ui_view` payloads by id. The model gets summaries; the client gets scoped
  referenceable payloads.
- **Single-user per workspace** (D1 amended); auth present but minimal surface (D27).
- **BYO key required** for any agent/LLM action (D9); reading/browsing works without one.
- **Provenance is visual (Q18):** quoted evidence must be rendered visually distinct from AI
  reasoning everywhere it appears (reader answers, cards, matrix cells).
- **LaTeX preview is debounced-compile, not per-keystroke** (Q52) — set the interaction
  expectation accordingly (~1–2s; live math is the exception).
- **Accessibility:** keyboard-navigable, sufficient contrast in the warm palette, semantic
  headings, focus states — standard WCAG-AA target.
- **Heavy client assets:** SwiftLaTeX WASM (~20–40 MB) loads lazily on first entering the
  Writing view; PDF.js for the reader.

---

## Schema (the contract both sides build against) — D21/D28 + related

Postgres (Supabase). `vector(768)` = gte-modernbert-base embeddings (D24). Global tables have no
`owner_id` (shared cache). Project-scoped tables carry `owner_id` + `project_id` (D27). Types are
indicative; refine at migration time.

### Account-level

```sql
-- Supabase auth.users is managed; app profile mirrors it.
profiles(
  id uuid pk references auth.users, display_name text, is_anonymous bool,
  created_at timestamptz )

api_keys(
  id uuid pk, owner_id uuid, provider text,          -- google|groq|openai|anthropic|openrouter|deepseek|custom
  encrypted_key bytea,                               -- AES-256-GCM (D26), master key in env
  base_url text null, last4 text, valid bool, created_at timestamptz )

model_config(
  owner_id uuid pk, primary_provider text, primary_model text,
  auxiliary_provider text null, auxiliary_model text null )

storage_connections(
  id uuid pk, owner_id uuid, provider text,          -- gdrive|onedrive|supabase
  encrypted_token bytea null, root_folder_id text null, created_at timestamptz )
```

### Global — keyed by canonical paper id (compute once, shared) — D21

```sql
papers(
  id text pk,                                        -- canonical id: DOI→arXiv→OpenAlex→S2 (D21)
  doi text, arxiv_id text, openalex_id text, s2_id text,
  title text, abstract text, authors jsonb, venue text, year int,
  citation_count int, metadata jsonb, created_at timestamptz )

paper_content(
  paper_id text pk references papers,
  sections jsonb,                                    -- [{heading, text, char_start, char_end}]
  full_text text, parser text, parsed_at timestamptz )

-- Unified extraction store: standard card fields AND matrix custom columns (Q46 reuse).
paper_extractions(
  id uuid pk, paper_id text references papers,
  field_key text,                                    -- problem|method|datasets|results|limitations|<custom-normalized>
  value text, quote text, char_start int, char_end int, section_heading text,
  validated bool,                                    -- deterministic substring check (Q18); false→"not stated"
  created_at timestamptz,
  unique(paper_id, field_key) )

paper_chunks(                                        -- global memory substrate (papers)
  id uuid pk, paper_id text references papers, chunk_index int,
  content text, embedding vector(768), tsv tsvector,
  section_heading text, char_start int, char_end int )

entities(                                            -- authors/datasets/models/methods/concepts/code
  id uuid pk, type text, canonical_id text null,     -- OpenAlex author id / PwC dataset id / repo url; null→LLM entity
  name text, aliases jsonb, embedding vector(768) null, metadata jsonb )

paper_edges(                                         -- global graph edges (metadata + LLM paper-intrinsic)
  id uuid pk, src_type text, src_id text, dst_type text, dst_id text,
  edge_type text,                                    -- cites|authored_by|uses_dataset|has_code|method_of|...
  source text,                                       -- openalex|s2|pwc|llm  (provenance)
  from_paper_id text null references papers, created_at timestamptz )

paper_blobs(                                         -- blob location, mostly cache/user-owned (Q5/Q37)
  id uuid pk, paper_id text references papers,
  class text,                                        -- oa | linked | upload
  storage_backend text,                              -- supabase | gdrive | onedrive
  storage_ref text, source_url text null,
  content_hash text null,                            -- SHA-256 (upload dedup)
  available bool, created_at timestamptz )
```

### Project-scoped — owner_id + project_id (D27)

```sql
projects(
  id uuid pk, owner_id uuid, name text,
  interest_profile jsonb,                            -- {categories:[], keywords:[], synonyms:{}} (D25/Q32)
  seed text null, created_at timestamptz )

project_papers(                                      -- library membership + relevance
  id uuid pk, owner_id uuid, project_id uuid references projects, paper_id text references papers,
  relevance text,                                    -- relevant | somewhat | not | unset
  added_via text, added_at timestamptz,
  unique(project_id, paper_id) )

notes(
  id uuid pk, owner_id uuid, project_id uuid, title text, content text,  -- markdown
  links jsonb, created_at timestamptz, updated_at timestamptz )

experiments(                                         -- D19/Q19
  id uuid pk, owner_id uuid, project_id uuid,
  hypothesis text, setup jsonb, metrics jsonb,       -- metrics: [{name, value, unit}] user-authored
  notes text, status text,                           -- planned | remaining | in_progress | done
  created_at timestamptz, updated_at timestamptz )

conversations(                                       -- persistent per project (D30)
  id uuid pk, owner_id uuid, project_id uuid, title text, created_at timestamptz )

messages(
  id uuid pk, conversation_id uuid references conversations,
  role text,                                         -- user | assistant | tool
  content text, tool_calls jsonb, tool_results jsonb,-- tool_results = refs to tool_results table
  ui_state_snapshot jsonb, created_at timestamptz )

conversation_summaries(                              -- summary-as-index (D20 node 4)
  id uuid pk, conversation_id uuid, summary text,
  covers_from uuid, covers_to uuid, embedding vector(768), created_at timestamptz )

project_chunks(                                      -- project memory substrate (notes/experiments/convos)
  id uuid pk, owner_id uuid, project_id uuid,
  source_type text, source_id uuid,                  -- note | experiment | conversation
  content text, embedding vector(768), tsv tsvector, created_at timestamptz )

highlights(                                          -- quote anchors (Q33) shared with provenance
  id uuid pk, owner_id uuid, project_id uuid, paper_id text,
  quote text, prefix text, suffix text,
  page int, bbox jsonb, section_heading text,
  color text, note text, created_at timestamptz )

matrices(                                            -- D28/Q46
  id uuid pk, owner_id uuid, project_id uuid, name text,
  selected_paper_ids jsonb, column_defs jsonb,       -- [{key, label, kind: standard|custom|user}]
  cell_overrides jsonb,                              -- {paper_id: {col_key: {value, source:user}}}
  created_at timestamptz, updated_at timestamptz )

idea_edges(                                          -- project graph edges involving user ideas/notes
  id uuid pk, owner_id uuid, project_id uuid,
  src_type text, src_id text, dst_type text, dst_id text, edge_type text, created_at timestamptz )

feed_items(                                          -- D20-feed/Q20
  id uuid pk, owner_id uuid, project_id uuid, paper_id text,
  score real, why_relevant jsonb, status text,       -- new | saved | dismissed
  surfaced_at timestamptz )

seen_papers(                                         -- dedup set (D20-feed)
  id uuid pk, project_id uuid, paper_id text,
  reason text,                                       -- read | library | surfaced | dismissed
  unique(project_id, paper_id) )

latex_docs(                                          -- Writing workspace (Slice 3)
  id uuid pk, owner_id uuid, project_id uuid, title text,
  source text, files jsonb null, updated_at timestamptz )
```

### Harness / runtime

```sql
search_results(                                      -- cached federated result set (D6/D22)
  id uuid pk, owner_id uuid, project_id uuid, query text,
  understood jsonb,                                  -- {keywords, filters} from the one query pass (D25)
  ranked_paper_ids jsonb, filters jsonb, created_at timestamptz )

tool_results(                                        -- reference-based result store (D20 node 3)
  id uuid pk, conversation_id uuid, tool_name text,
  ui_view jsonb, created_at timestamptz )            -- fetched lazily by id; never in LLM context

jobs(                                                -- Postgres queue (D14): parse/embed/extract/feed-poll
  id uuid pk, type text, payload jsonb, status text,
  project_id uuid null, created_at timestamptz, run_at timestamptz )
```

### Memory retrieval (query-time union — D21)

`query_memory(project P)` retrieves over `paper_chunks` (for papers in `project_papers` of P)
**∪** `project_chunks` (of P), via hybrid vector(768) + `tsv` BM25 → cross-encoder rerank →
cited source rows. Paper embeddings computed once globally and reused; retrieval stays
project-isolated by the membership filter. Not a physical table.

---

## Harness tool catalog (frontend triggers these; backend implements) — D22

`Q`=read, `A`=write/UI. Slice-1 set:
`search_papers` Q · `refine_results` Q · `add_paper` A · `get_paper(include=[card|sections|
references|datasets|code])` Q · `compare` Q · `open_reference` A · `query_memory` Q ·
`save_note`/`update_note` A · `mark_relevant` A · `create_highlight` A · `log_experiment`/
`update_experiment` A · nav: `open_paper`/`scroll_to`/`highlight_span`/`open_view` (A, emit
`ui_actions`). Later slices: `build_matrix`/`update_cell`; feed tools; `insert_citation`/
`check_citations`/`find_missing_citations`; `get_graph`/`find_related`. Reader Q&A is the core
loop, **not** a tool (D22 Fork A). MCP: adapter only, no bundled servers in v1.
