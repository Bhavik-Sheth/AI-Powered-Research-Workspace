# PRD — Research Companion OS (v1)

**Vocabulary rule (binding, from the Grill Session).** The five build units are **Phases**, not
"slices". `DECISIONS.md` D5 and every "Slice N" reference in D19, D29 and D36 are to be read and
restated as **Phase N**. The word "Slice" is retired from this document and everything downstream
of it.

**Authority.** `DECISIONS.md` (D1–D37) decides scope. Where this PRD restates a decision, the
decision ID is cited. Where this PRD adds something, it is because the Grill Session established
it (R1–R5) or because it is a placement/structuring choice this document owns.

---

## 1. Overview

**Feature / Project Name:** Research Companion OS

**Problem Statement:**
A solo researcher's work is scattered across a browser full of arXiv tabs, a PDF reader with no
memory, a notes app that does not know what a paper said, and a terminal where the experiments
actually run. Nothing connects, so the same paper gets re-found and the same derivation gets
re-done. General AI tools make this worse rather than better: they answer confidently about papers
they have not read, so nothing they say can be trusted without re-checking the source by hand.

**Proposed Solution:**
A local, single-user desktop workspace where an AI Companion sits permanently beside the work —
searching the literature, reading the real PDF with you, remembering everything in the project,
and writing experiment code that you approve before it runs in a Docker container. Every claim
attributed to a paper is backed by a verbatim span that a non-LLM validator has confirmed exists.

**AI Build Summary:**
> Build a Linux-only, single-user desktop research workspace in five phases plus a cross-cutting
> voice layer. Ship all five phases in v1 (Grill R1). The stack, architecture and data model are
> already locked in `DECISIONS.md` D1–D37 and restated in `TRD.md`/`Schema.md` — do not re-derive
> them, and never resurrect anything in `DECISIONS.md` Appendix A. Hardest constraints, in order:
> (1) no field or claim is displayed as coming from a paper unless a deterministic substring
> validator confirms its quote resolves in the parsed source; (2) all code execution runs in a
> Docker container, always, with no opt-out; (3) the agent never executes code without an explicit
> human approval; (4) the embedding model is fixed forever; (5) never fetch paywalled PDFs. Files
> in the vault folder are truth; Postgres is a rebuildable index. Build and stop for sign-off at
> each phase boundary.

---

## 2. Goals & Success Metrics

**Primary Goal:** One researcher can run a complete research project — discover, read, remember,
experiment, compare, write, keep current — inside one local workspace, without any claim about a
paper being shown that its source does not verifiably contain.

**Success Metrics:**
- **Phase gates pass cleanly.** Each of the five per-phase manual acceptance checklists in
  Section 13 passes at 100% at its sign-off gate, and all four automated pytest suites (D24
  provenance substring validator, D25 canonical-id dedup, D33 fuzzy quote locator, D29 `measured`
  gate) pass. No coverage-percentage target, no CI service (Grill R2).
- **Zero unverified evidence rendered as verified.** Every displayed span has passed the substring
  validator; spans that fail are dropped to `not stated` (extractive card, matrix cell) or shown
  with the `⚠ unverified` badge (reader answers, companion citations). Target: 0 exceptions,
  checked by the D24 suite plus a manual pass per phase.
- **Zero unearned `measured` metrics.** No `metrics` row carries `source: measured` without a
  linked `run_id`, image digest, `requirements.txt` hash and notebook hash from a clean
  restart-and-run-all that exited 0. Target: 0 exceptions, enforced by the D29 gate suite.

**Anti-goals:**
- Not trying to write the researcher's prose, paper sections, or metric values — the AI verifies,
  organises, finds and navigates; the researcher authors (D24, D29, D34).
- Not trying to be an autonomous agent — no auto-run, no trusted-experiment mode, no blanket
  approval (D31).
- Not trying to serve more than one user on more than one machine — no auth, no sync, no
  multi-tenancy, and no schema concession to any of them (D1, D3).
- Not trying to be fast at LLM-call latency — an ~8–10 iteration cap with a graceful stop is the
  budget (D18 node 1); a routing model is future scope (D16).

---

## 3. Scope & Constraints

### In scope — v1 is all five phases plus voice (Grill R1)

| Phase | Scope |
|---|---|
| **Phase 1** | Project workspace (vault, dashboard, papers library, notes) + AI research search (federated, deduped, reranked) + reader with ask-about-highlight + notes + retrieval over everything read + the persistent Companion + onboarding wizard |
| **Voice** | Cross-cutting layer, built **right after Phase 1** (D36) — push-to-talk capture, transport, playback, and the D37 module boundary |
| **Phase 2** | Experiments — notebook UI, Docker sandbox, in-container kernel, consent gate, structured experiment record (D29–D31) |
| **Phase 3** | Reader depth (references list, datasets, code, cross-paper compare) + literature matrix (D27) |
| **Phase 4** | Writing workspace — LaTeX source/preview, citation insertion, citation checks (D34) |
| **Phase 5** | Research feed — interest profile, category fetch, deterministic rank, catch-up-on-launch poll (D28) |
| **Cross-phase** | The knowledge graph accretes across phases rather than owning one (D26) |

Three contracts are designed once in v1 and consumed by every phase (Grill R4):
- **The tool catalog** (D19) — one typed-tool surface; anything the user can click, the Companion
  can call.
- **The memory index** (D18 node 4 / D25) — the query-time union
  `paper_chunks(papers in P) ∪ project_chunks(P)`.
- **The shared quote-anchor object** (D33) — the same `{quote, prefix, suffix}` object serves an
  extractive card's `char_offsets`, a reader highlight, a matrix cell's provenance and a companion
  citation.

**Voice scope floor.** Voice ships at minimum as the **D37 module boundary plus the stub engine**,
wired end to end: `backend/voice/` with its engine registry and the
`transcribe()` / `synthesize()` interface, `frontend/src/voice/` with push-to-talk capture and
playback. The real `faster-whisper` (STT) and Piper (TTS) engines are **the single droppable piece
of v1** — if the D37 spike shows the local models are too heavy, the engines slip post-v1 and
nothing else changes (D36, Grill R1).

**Center-pane tabs are in scope** (Grill R5, a direct user instruction, which outranks
`DECISIONS.md` and `UI_DESIGN.md`). This overrides `UI_DESIGN.md` §2's single-pane-per-route
default and closes its §9.2 B. See Section 6 and Section 10.

### Out of scope

- Multi-user, auth, accounts, `owner_id`, RLS, billing (D1).
- Hosted deployment, any server, any clickable demo URL — demos are screen recordings (Appendix A).
- Windows/macOS, code signing, notarization, installers, AppImage, auto-update. Distribution is
  `git clone` + `make dev`, updates are `git pull` + rebuild (D2).
- Multi-machine sync (point Syncthing or git at the vault if ever wanted) (D3).
- File watching, hash-diffing, conflict detection, startup reconciliation — the app is the sole
  writer of the vault (D4).
- Scraping paywalled PDFs; if no OA copy and no user upload exist, show the abstract plus a
  source link and **no** fabricated card (D23).
- The AI drafting prose, paper sections, or metric values (D24, D29, D34).
- GPU arbitration between a local LLM server and experiment containers — the user stops one by
  hand (D11).
- Bundled MCP servers — the adapter is built as the extension seam, zero servers ship (D19).
- A hardwired intent classifier / regex fast path (D16).
- A second datastore (Qdrant, Neo4j, Redis) (D7, D9).
- Rate limiting (dissolved by single-user).
- **Deferred to post-v1 explicitly:** embedding notebook `.ipynb` content in the memory index (v1
  indexes the structured experiment record only, D29); two-layer extractive→paraphrase card
  display (v1 is extractive-only, D24); full negative-example learning for feed dismissals (v1 is
  a light down-weight, D28); always-on VAD voice (v1 is push-to-talk, D36); W&B/MLflow ingestion
  (D29).

### Technical constraints

- **Platform:** Linux desktop only, one user, one machine. The OS login is the auth boundary
  (D1).
- **Docker is a hard dependency**, verified at onboarding before anything else (D30, D35).
- **All code execution happens inside a Docker container — always, no opt-out** (invariant #4).
- **The agent never executes code without explicit user approval**; sandbox and consent gate are
  independent controls and both are required (invariant #5).
- **The embedding model is fixed forever** — pinned local `gte-modernbert-base`, 768-dim.
  Embeddings are never routed through Ollama/vLLM even when a local server is running
  (invariant #1, D14).
- **Claude Pro/Max and ChatGPT/Codex subscriptions cannot be used as LLM access** — no API
  surface, ToS forbids it, including via a local CLI shim (invariant #2).
- **Never fetch paywalled PDFs** — open-access fetch and user upload only (invariant #3).
- **Files are truth.** Everything outside `.research-os/` is durable user data; everything inside
  is derived and deletable. Deleting the index costs time (minutes to hours of re-parse and
  re-embed), never data (D3).
- **The app writes the file and updates the index in one operation** — disk and DB cannot drift
  (D4). Notes are keyed in the DB by a stable frontmatter id, never by file path (D4).
- **Network:** no dependency beyond the literature APIs and, optionally, an LLM endpoint. The
  experiment kernel has **network off by default**; dependencies install at image-build time
  (D30).
- **Loopback only:** the sidecar binds `127.0.0.1` on an ephemeral port and a per-launch bearer
  token is mandatory on every request and on the WebSocket (D2).
- **Cold start:** the window paints immediately with a readiness strip; ML models load lazily
  (5–15 s to import torch + the embedding model) and must **never** block first paint. Search,
  notes and the vault tree are usable before embeddings are (D2).
- **Secrets:** LLM keys live in the OS keyring (`libsecret`), never in the vault, never in the DB,
  never in the repo, never logged; the UI shows `…last4` only (D13).
- **Accessibility:** WCAG **AA** contrast, full keyboard navigability, and a global
  `:focus-visible` of `2px solid var(--accent)` at `2px` offset on every interactive element
  (`UI_DESIGN.md` §6, §7).
- **Offline:** the app runs without a network for everything except literature search/fetch and a
  remote LLM endpoint. A local model endpoint makes it fully offline (D11).
- **Responsive floor:** usable at ~1280 px wide; the nav collapses to icons before the Companion
  pane is ever dropped (`UI_DESIGN.md` §7, §9.2 I).

---

## 4. Jobs to Be Done

**Persona — the only one.** *Bhavik, a solo student researcher on a single Linux machine.* No lab,
no cloud budget, no collaborators in the tool. He brings his own LLM key or runs a local model,
and he is both the author and the only user of this app (D1, "the author is the user").

| Priority | Job Statement |
|---|---|
| 1 | When I open a new research direction, I want to search the literature and pull the relevant papers into one project, so I can stop maintaining a pile of browser tabs I will never re-find. |
| 2 | When I am stuck on a dense passage, I want to ask about the exact text I have highlighted and get an answer whose every factual claim cites a span I can click through to, so I can trust it without re-reading the section myself. |
| 3 | When I half-remember something from three weeks ago — a note, a paper, a conversation — I want to ask the project and get the actual row back with a citation, so I stop re-deriving what I already worked out. |
| 4 | When I have a hypothesis, I want to write and run the code beside the papers that inspired it and have the resulting number carry its own proof of how it was produced, so it can sit as a comparable row next to published results. |
| 5 | When I write the paper up, I want citations pulled from the library I actually read and unsupported claims flagged as I go, so I stay the author while the tooling does the checking. |

---

## 5. User Stories

| ID | Phase | Role | Action | Benefit | JTBD |
|---|---|---|---|---|---|
| US1 | 1 | Solo researcher | complete a gated setup wizard that checks Docker, picks a vault folder, validates an LLM endpoint and creates the first project | so that I land in a working project instead of a half-configured app | J1 |
| US2 | 1 | Solo researcher | search academic sources in natural language and get one deduped, reranked result list | so that I find papers without leaving the workspace | J1 |
| US3 | 1 | Solo researcher | open a paper as the real PDF with a structure sidebar and an extractive card whose fields click through to the source span | so that I can see what the paper actually says, verbatim | J2 |
| US4 | 1 | Solo researcher | highlight a passage and ask the Companion about it, with citations visually distinct from its reasoning | so that I can trust the explanation | J2 |
| US5 | 1 | Solo researcher | write markdown notes stored as plain files in my vault | so that my writing is mine and readable with the app closed | J3 |
| US6 | 1 | Solo researcher | ask the project a question and get cited rows from papers, notes, experiments and past conversations | so that I stop re-deriving what I already know | J3 |
| US7 | 1 | Solo researcher | keep one Companion conversation open across every view, with several papers open in tabs at once | so that the assistant never loses the thread when I navigate | J1, J2 |
| US8 | Voice | Solo researcher | hold a key, speak to the Companion, and hear the reply | so that I can drive the workspace without typing | J1, J2 |
| US9 | 2 | Solo researcher | run a notebook in a per-experiment Docker container, approving every run, and capture measured metrics from a clean restart-and-run-all | so that my numbers carry provenance strong enough to compare against papers | J4 |
| US10 | 3 | Solo researcher | build a literature matrix over selected papers from the cards already extracted, with editable cells labelled as mine | so that I can compare papers without re-extracting or corrupting provenance | J1, J2 |
| US11 | 3+ | Solo researcher | explore a project knowledge graph where inferred edges are visually distinguishable from API-derived ones | so that I can follow connections without trusting invented ones | J3 |
| US12 | 4 | Solo researcher | write LaTeX with live preview, insert citations from my project's references, and see unsupported claims flagged | so that I remain the author while the tool checks the citations | J5 |
| US13 | 5 | Solo researcher | see a feed of new papers matched to my project's editable interest profile, each stating why it surfaced | so that I stay current without a recommendation black box | J1 |

---

## 6. Proposed Experience

**Design Direction.**
A quiet, light-only academic workspace: a light-blue blueprint frame with near-white panels
floating on it, cyan as the single accent meaning *current / primary / mine*, serif for reading,
sans for chrome and chat, mono for values you might copy. The mental model is **a desk, not a chat
app** — the Companion is a permanent third column beside the work, never a modal and never a
takeover. The full palette, type ramp and component shapes live in `UI_DESIGN.md`; that file is
look-and-feel only and never outranks the behaviour specified here.

**Shell (D32, `UI_DESIGN.md` §2).** Top bar + three columns on **every** screen: left nav (200 px,
groups `LIBRARY` / `WORK` / `DISCOVER`, with `Matrix` added — `UI_DESIGN.md` §9.2 A), routed
center pane, and the 280 px persistent Companion. Views that need a secondary list, detail column
or overlay sheet take the width out of the center pane, never out of the Companion.

**Center-pane tabs (Grill R5 — builds `UI_DESIGN.md` §2's tab strip as spec, not contingency).**
The center pane carries a tab strip. Tabs are bottom-attached to the content panel; active tab =
`--surface` + `box-shadow: 0 -2px 0 var(--accent) inset` + trailing `✕`; inactive =
`--surface-inactive`; a `+` glyph closes the strip; the panel welds to the active tab via
`border-radius: 0 12px 14px 14px`. Consequences, all binding:
- **Tab state persists** across app restarts — it is real state, not view-local ephemera.
- **D32's routing statement is restated:** the URL owns the project plus a **stack of open
  center-pane routes with exactly one active**, not a single center-pane URL.
- **The reader supports multiple papers open simultaneously.**
- *(Derived from D32, not a user answer — flagged in Section 14 for confirmation.)* The Companion
  remains **one WebSocket session per project, not per tab**. It does not switch sessions when the
  active tab changes; the active tab is reported in the `ui_state` payload (D18 node 5).

**Key screens / states.**
- **Onboarding wizard** — gated, four required steps: Docker check → vault folder → validated LLM
  endpoint → first project with an optional focus seed (D35). No mock exists; derive from
  `UI_DESIGN.md` §1–§3.
- **Dashboard** (`/p/:id`) — stat tiles whose qualifier is always the actionable subset
  (`4 unmarked`), `CONTINUE WHERE YOU LEFT OFF` with real resume positions, `NEEDS ATTENTION`
  mixing dashed soft nudges and real error cards.
- **Reader** (`/p/:id/paper/:paperId`) — tab strip, compact header with the four-value relevance
  segmented control, collapsible structure sidebar (sections / references / datasets / code), real
  PDF.js pages, collapsible extractive card. Card field and PDF span light up **together**.
- **Papers library**, **Notes**, **Experiments board + detail sheet**, **Knowledge graph**,
  **Writing**, **Feed**, **Search results** — as drawn in `UI_DESIGN.md` §4.
- **Literature matrix** (`/p/:id/matrix/:id`) and **Settings / Models** — no mock; derive from
  `UI_DESIGN.md` §1–§3.
- **Empty state:** dashed border, no tint, italic muted copy. Absence is a state you *draw* —
  `not stated in this paper`, `unmarked`, `Unlinked`, `unsupported claim — no linked source yet`,
  `+ add metric`, empty lists.
- **Error state:** the error card — danger left rule, headline, explanation, and recovery actions
  that **say what still worked** ("Other sources returned normally — these results are
  incomplete"). Danger is for errors only, never a status value; there is no "failed" experiment
  status.
- **Loading state:** progressive reveal, never a single blocking spinner. Search streams
  per-source progress with real cards and shimmer skeletons side by side. Cold start shows a
  readiness strip with per-capability readiness.
- **Additional states this PRD requires that `UI_DESIGN.md` §6 does not draw:** per-paper
  processing (fetch / parse / embed / extract are queued jobs — the library card and reader header
  need it, and "still extracting" must be visually distinct from "not stated"); degraded full text
  (abstract only + source link); dropped WebSocket (disconnected / reconnecting, and the composer
  must say whether a queued message will send); LaTeX compile errors.

**Interaction model.**
1. Ask the Companion, or click the equivalent control — **both resolve to the same tool call and
   the same route transition** (D17/D18). No UI-only capability the agent cannot reach, and no
   agent capability with no UI surface.
2. Select text in the reader → inline popover: `Ask about this` · `Highlight` · `Explain`.
3. A companion citation, a card field and a PDF span are the same anchor object — clicking any of
   them drives `scroll_to` + `highlight_span` and lights the other two.
4. **Interrupt is first-class**: a visible `✕ Stop` control while a turn runs; cancelling keeps
   partial results (D18 node 5).
5. **Push-to-talk**, never always-on VAD — the microphone never opens unprompted (D36).
6. **Approval before execution**: the run prompt shows the code *and* the container spec (image,
   mounts, network, GPU). Agent-written cells are visually marked as unrun and pending approval —
   the user must never be unsure whether something executed (D31).
7. **Restart & run all** must read as a deliberate step, not a refresh button — it is the only
   action that can produce a `measured` metric (D32, D29).
8. **Undo:** notes and drafts are plain files with normal editor undo. There is no undo for a
   container run; there is an approval gate instead.

**Accessibility notes.**
- Every interactive element is reachable and operable by keyboard, with the `:focus-visible` ring
  above.
- Graph encodes node type by **colour and shape**, and edge provenance by **dash**; the legend
  documents both. Never colour alone. Filter chips keep their hue dot when toggled off.
- WCAG AA verified on muted text and on tinted badges; the `700 10px` badge size is the riskiest
  combination in the system and must be checked explicitly.
- Screen reader: the Companion transcript's five kinds (user, assistant reasoning, cited evidence,
  tool chip, tool result) must be distinguishable non-visually, and `⚠ unverified` must be
  announced, not just tinted.
- Prose measure caps at 600–640 px regardless of pane width.

**Figma / Design Link:** *(none — the visual source is the 10-screen static mock recorded in
`UI_DESIGN.md`.)*

---

## 7. Component Inventory

**→ See [DesignDecisions.md](./DesignDecisions.md)**

The enumerable UI components serving the Section 6 screens — shell, tab strip, Companion pane,
reader panes, relevance controls, quote/evidence blocks, absence and error blocks, notebook cells,
matrix cells, graph canvas, feed cards, wizard steps — are defined there.

---

## 8. Data Models

**→ See [Schema.md](./Schema.md)**

This feature creates and reads the global records (`papers`, `paper_content`, `paper_cards`,
`paper_chunks`, `paper_edges`) and the project-scoped records (`projects`, `project_papers`,
`notes`, `experiments`, `conversations`, `messages`, `project_chunks`, `highlights`, `feed_items`,
`seen_set`, `idea_edges`), plus the single-row local settings store.

---

## 9. API / Integration Surface

Two surfaces: a **WebSocket** channel carrying the agent turn, and a **REST** surface for
everything the frontend pulls by id. Both terminate on loopback and both require the per-launch
bearer token — there is no user auth (D1, D2), so the "Auth" column below means that token.

**WebSocket — `/ws/session/:projectId` (D18 node 5).** One session per project, surviving
center-pane navigation *and* tab switches.

| Direction | Event | Payload purpose | Stories |
|---|---|---|---|
| ↓ | `status` | Live status line ("reading paper…") | US7 |
| ↓ | `text_delta` | Streaming assistant text | US4, US6, US7 |
| ↓ | `tool_call` | Tool chip: tool name, running | US2, US9 |
| ↓ | `tool_result` | `ref` only — the rich `ui_view` is fetched by id, never sent through LLM context | US2, US6 |
| ↓ | `ui_action` | Drives the router: `open_paper`, `scroll_to`, `highlight_span`, `open_view` | US3, US7 |
| ↓ | `turn_complete` / `error` | End of turn / failure | all |
| ↑ | `user_message` | User turn **plus a UI-state snapshot**, including the active tab | US4, US7 |
| ↑ | `ui_state` | Incremental UI-state push mid-turn | US7 |
| ↑ | `interrupt` | Cancels the turn; partial results retained | US7 |

**REST resource surface.** Paths below are indicative; `TRD.md` owns their final shape, and the
TypeScript client is generated from the FastAPI OpenAPI schema and regenerated on every backend
change (D10).

| Method | Path | Description | Auth | Response | Stories |
|---|---|---|---|---|---|
| GET/POST | `/api/projects` | List / create projects (name + focus seed) | Token | `Project` | US1 |
| POST | `/api/search` | Federated search → `result_id` + summaries | Token | `{ result_id, results[] }` | US2 |
| GET | `/api/results/:resultId` | Fetch a cached result set / tool `ui_view` by id | Token | `ResultSet` | US2, US6 |
| POST | `/api/projects/:id/papers` | Add a paper by link, id, or upload | Token | `Paper` | US2 |
| GET | `/api/papers/:paperId` | Paper record; `?include=card,sections,references,datasets,code` | Token | `Paper` | US3 |
| GET | `/api/papers/:paperId/pdf` | The PDF bytes from the vault | Token | binary | US3 |
| PATCH | `/api/projects/:id/papers/:paperId` | Relevance level + why-relevant note | Token | `ProjectPaper` | US3 |
| GET/POST/PATCH | `/api/projects/:id/notes` | Notes CRUD; writes file + index in one operation | Token | `Note` | US5 |
| POST | `/api/projects/:id/highlights` | Create a quote anchor | Token | `Highlight` | US3, US4 |
| POST | `/api/projects/:id/memory/query` | Hybrid retrieval → reranked **cited rows** | Token | `{ rows[] }` | US6 |
| GET/POST/PATCH | `/api/projects/:id/experiments` | Experiment record CRUD | Token | `Experiment` | US9 |
| POST | `/api/experiments/:id/kernel` | Start / stop the per-experiment container kernel | Token | `KernelStatus` | US9 |
| POST | `/api/experiments/:id/run_all` | **Requires explicit user confirmation**; the only path to `source: measured` | Token | `{ run_id }` | US9 |
| GET | `/api/runs/:runId` | Outputs, logs, exit code, image digest, hashes | Token | `Run` | US9 |
| GET/PUT | `/api/projects/:id/matrix/:matrixId` | Matrix definition, overrides, custom-column cache | Token | `Matrix` | US10 |
| GET | `/api/projects/:id/graph` | Project-scoped edge union, typed and provenance-tagged | Token | `Graph` | US11 |
| GET/POST | `/api/projects/:id/documents` | LaTeX drafts; compile + BibTeX export | Token | `Document` | US12 |
| GET/POST | `/api/projects/:id/feed` | Feed items; save / dismiss | Token | `FeedItem[]` | US13 |
| GET/PUT | `/api/projects/:id/interest-profile` | Inspectable, editable `{categories, keywords}` | Token | `InterestProfile` | US13 |
| GET/PUT | `/api/settings/models` | Provider keys (write-only, `…last4` on read), primary + auxiliary model, **validate on save** | Token | `ModelSettings` | US1 |
| GET | `/api/health` | Per-capability readiness for the cold-start strip | Token | `{ capabilities }` | US1 |
| POST | `/api/voice/transcribe` · `/api/voice/synthesize` | The only voice endpoints; engine-agnostic (D37) | Token | `Transcript` / audio | US8 |

**External integrations:**
- **arXiv, OpenAlex, Semantic Scholar** — primary fan-out on every search (D21).
- **Papers with Code, GitHub** — enrichment on paper *open*, not on every search (D21).
- **Crossref** — on-demand DOI resolver only, when OpenAlex and S2 both missed (D21).
- **Unpaywall / arXiv / S2 OA links** — open-access PDF fetch. Never a paywall (D23, invariant #3).
- **LLM providers via LiteLLM** — Google, Groq, OpenAI, Anthropic, OpenRouter, DeepSeek, custom
  OpenAI-compatible base URL, plus **Ollama and vLLM as named first-class local entries** that
  take a base URL and **no API key, with the UI not demanding one**, and that are **queried for
  their available models** rather than making the user type a model string (D11, D12).
- **Docker Engine** — Postgres+pgvector container, per-experiment kernel containers, the Tectonic
  LaTeX escape-hatch image (D8, D30, D34).
- **OS keyring (`libsecret`)** — master key storage (D13).

---

## 10. State Management Map

| State | Location | Persistence | Notes |
|---|---|---|---|
| Papers, notes, experiments, drafts, PDFs | The vault folder (files) | Persistent, user-owned | Truth. Readable with the app closed (D3). |
| Embeddings, `tsv`, parsed sections, cards, edges, caches | Postgres in Docker, under `.research-os/` | Rebuildable | Derived. Deleting costs time, never data (D3). |
| All REST server data in the UI | React Query cache | Session | Invalidated by `tool_result` events on the WebSocket bus (D32). |
| Companion transcript + turn stream | WebSocket event bus → chat transcript | Session in memory; **verbatim in `conversations`/`messages`** | One session per **project**, not per tab; survives navigation and tab switches (D32, Grill R5). |
| Local UI state (open pane, selection, active tab) | Zustand | Session | **This store *is* the `ui_state` payload** sent up on every `user_message` and on incremental pushes (D18 node 5, D32). |
| Project + open center-pane routes | URL, as a **tab stack with one active** | **Persistent** — restored on app restart | Restates D32's "URL owns project + center-pane content" for tabs (Grill R5). Chat and nav are persistent shell, not routes. |
| Turn state (in-flight agent loop) | In-process `asyncio` task in the sidecar, bound to the WS session | Persisted incrementally | What makes interrupt real (D18 node 7). |
| Working set + compacted history | Sidecar context assembly | Full history in the DB | Compaction is a *window* op, not forgetting. Eviction order: working set → history → per-turn retrieval; system prompt / tool schemas / UI state are never evicted (D18 node 2). |
| Large tool payloads | Server-side result store, keyed by `result_id` | Session/cached | The model manipulates handles; `ui_view` never enters LLM context (D18 node 3). |
| LLM API keys | OS keyring | Persistent | Decrypted in memory at call time only; never in the vault, DB, repo, or logs; `…last4` in the UI (D13). |
| Sidecar port + per-launch bearer token | Electron main → preload → renderer | Per launch | Regenerated every launch (D2). |
| Interest profile | `projects` row (JSONB) + `project.md` | Persistent, user-editable | Refreshed by a weekly/on-growth catch-up job, never in a request path (D28, D9). |
| Scheduled-job cursors (`last_run_at`) | Postgres queue tables | Persistent | Catch-up-on-launch, not cron — a desktop app only runs when opened (D9). |
| Voice engine + model weights | Inside `backend/voice/` only | Cached in `.research-os/` | Lazy — the STT model must not load until the first push-to-talk press. No other module may know a model exists (D37). |

---

## 11. Tech Stack

**→ See [TRD.md](./TRD.md)**

---

## 12. Suggested File Structure

**→ See [MODULES.md](./MODULES.md)** — responsibilities, public interfaces, and dependencies for
each unit in the tree.

Reconciled against the approved `MODULES.md` (39 modules). Names and paths below are ground truth;
`docker/` and `tests/` are infra/test locations, not modules, and `backend/harness/`'s internal
files are intentionally collapsed — they are one self-contained package, not separate modules
(D18 node 7).

```
[root]/
├── backend/                        # FastAPI sidecar — Python only (D10)
│   ├── main.py                     # Sidecar Bootstrap: app factory, loopback bind, bearer token, readiness
│   ├── api/                        # REST API — one route, one domain-module delegate, no SQL/LLM in handlers
│   ├── ws/                         # Session Transport — WebSocket session, typed event stream, interrupt
│   ├── harness/                    # Agent Harness — self-contained, extractable agent runtime (D18 node 7);
│   │                                #   internal files (loop, context, tools/, result_store, mcp/) not enumerated
│   ├── llm/                        # LLM Gateway — LiteLLM wrapper, primary/auxiliary tiers, structured-output fallback
│   ├── search/                     # Search Federation — query understanding, dedup, rerank, cache
│   ├── papers/                     # Paper Pipeline — fetch (OA only), docling parse, extraction, cards
│   ├── provenance/                 # Provenance — substring validator + fuzzy quote locator + anchor object
│   ├── memory/                     # Memory Index — chunking, embedding, hybrid retrieval, cited rows
│   ├── graph/                      # Knowledge Graph — metadata + LLM-derived edges, project-scoped union
│   ├── matrix/                     # Literature Matrix — card projection, custom-column extractive queries
│   ├── experiments/                # Experiment Record — record, metrics, measured-gate rule
│   ├── sandbox/                    # Execution Sandbox — Docker orchestration, kernel transport, consent gate
│   ├── writing/                    # Manuscript — LaTeX docs, citation checks, BibTeX, Tectonic escape hatch
│   ├── feed/                       # Research Feed — interest profile, poll, deterministic ranking
│   ├── voice/                      # Voice Engine — engine-agnostic transcribe/synthesize + engine registry (D37)
│   ├── vault/                      # Vault Writer — the sole writer: file + index in one operation
│   ├── jobs/                       # Job Queue — Postgres-backed queue, catch-up-on-launch scheduler
│   ├── db/                         # Database Layer — models, migrations, pgvector/tsvector queries
│   └── settings/                   # Settings Store — keyring, provider config, validation
├── frontend/                       # Vite + React renderer
│   └── src/
│       ├── app/                    # App Shell — top bar, nav, router + tab stack (Grill R5)
│       ├── companion/              # Companion Pane — transcript, tool chips, evidence blocks, composer, Stop
│       ├── reader/                 # Reader — PDF.js canvas, structure sidebar, extractive card, popover
│       ├── library/                # Library View — papers library list, relevance controls, processing badges
│       ├── notes/                  # Notes Editor — plain markdown notes (Phase 1)
│       ├── writing/                # Manuscript Editor — CodeMirror LaTeX + KaTeX + SwiftLaTeX preview (Phase 4)
│       ├── experiments/            # Experiments Board — board, detail sheet, notebook cells, approval prompt
│       ├── matrix/                 # Matrix View — literature matrix grid
│       ├── graph/                  # Graph View — knowledge graph canvas + legend
│       ├── feed/                   # Feed View — surfaced feed items, save/dismiss
│       ├── dashboard/              # Dashboard — stat tiles, resume points, needs-attention
│       ├── search/                 # Search Results — streamed federated results per source
│       ├── onboarding/             # Onboarding Wizard — the gated four-step wizard (D35)
│       ├── settings/               # Settings Panel — provider keys, models, voice engine
│       ├── voice/                  # Voice Capture — push-to-talk capture + playback; one hook (D37)
│       ├── state/                  # Client State — React Query cache, WS event bus, Zustand ui_state
│       └── design/                 # Design Tokens — CSS custom properties + enum-to-label maps
├── desktop/                        # Desktop Shell — Electron main + preload, launcher only, no logic (D10)
├── packages/api-client/            # Generated API Client — TS client generated from OpenAPI
├── docker/                         # compose (Postgres+pgvector), experiment base image, tectonic — infra, not a module
├── tests/                          # the four pytest suites (D24, D25, D33, D29) — Grill R2 — test location, not a module
├── DECISIONS.md  UI_DESIGN.md  Research Companion Workspace OS.md
└── Makefile                        # make dev
```

---

## 13. Acceptance Criteria

Structured by phase (Grill R4). **Each phase is a hard stop for the user's sign-off; no phase
begins before the previous one is signed off.** Everything below is verified **manually, by eye,
against this checklist**, except the four items explicitly marked **[pytest]**, which are the only
automated suites in v1 (Grill R2). No coverage target, no CI service.

### Phase 1 — Workspace, search, reader, notes, memory

**US1 — Onboarding**
- [ ] The wizard cannot be skipped or completed out of order; all four steps are required.
- [ ] Step 1 fails closed when the Docker daemon is unreachable, and shows the exact `dnf` /
      `systemctl` commands.
- [ ] Step 2 defaults to `~/ResearchOS`, creates the D3 folder layout, and cannot be skipped.
- [ ] Step 3 accepts either a BYO key or a local base URL; **the local path never demands an API
      key**, discovered models are listed rather than typed, and a test call validates on save.
- [ ] Step 4 creates a project; the focus seed is optional and skippable.
- [ ] Finishing the wizard lands in a working project. No sample or demo project is created.
- [ ] Edge case: an invalid key shows the error card with a retry, not a dead end.

**US2 — Federated search**
- [ ] One natural-language query issues exactly **one** LLM query-understanding pass, then
      deterministic per-source parameter mapping — not one LLM rewrite per source.
- [ ] arXiv, OpenAlex and Semantic Scholar are all queried; Papers with Code and GitHub are **not**
      called on search, only on paper open; Crossref is called only to resolve a DOI the others
      missed.
- [ ] Results are deduped on the canonical id (DOI → arXiv → OpenAlex/S2) with all source ids
      retained. **[pytest]** the D25 canonical-id dedup suite covers each priority path and the
      collision cases.
- [ ] The top ~100 are cross-encoder reranked and cached under a `result_id`.
- [ ] Results render as abstract + metadata only — no structured card is built at this stage.
- [ ] Loading: per-source progress streams, with real cards and shimmer skeletons side by side.
      There is never a single blocking spinner.
- [ ] Error: one source failing degrades the page and names what still worked; it never blanks it.
- [ ] Empty: the dashed empty panel names the query and offers a way out.

**US3 — Reader and extractive card**
- [ ] The real PDF renders via PDF.js with figures, equations and layout intact — not reflowed
      prose.
- [ ] The structure sidebar lists sections (jump-to), a reference count, datasets and a code link,
      derived from the docling parse.
- [ ] Every extractive-card field is a verbatim span `{value, quote, char_offsets,
      section_heading}` and displays its `§section · start–end` in mono.
- [ ] **[pytest]** the D24 substring validator suite: a claimed quote resolves at the claimed
      offsets, or the field is dropped. A field that fails validation renders as `not stated in
      this paper` in the dashed treatment — **never as unverified prose**.
- [ ] **[pytest]** the D33 fuzzy quote locator suite: whitespace, hyphenation and ligature
      variants locate correctly across both the docling text and the PDF.js text layer.
- [ ] Clicking a card field drives `scroll_to` + `highlight_span`; the card field and the PDF span
      light up **together**, with exactly one active span at a time.
- [ ] Relevance is the four-value enum shown as `relevant / somewhat / not relevant / unmarked` —
      the enum value `unset` never appears in UI copy.
- [ ] Degradation: with no OA copy and no upload, the reader shows the abstract plus a source link
      and **no** card. No paywalled fetch is ever attempted.
- [ ] Processing: fetch / parse / embed / extract show a visible per-paper state, and "still
      extracting" is visually distinct from `not stated`.

**US4 — Ask about a highlight**
- [ ] Selecting text opens the popover with `Ask about this` · `Highlight` · `Explain`.
- [ ] The answer's every factual claim about the paper carries an inline citation to a span.
- [ ] Quoted evidence is visually distinct from the model's reasoning in the transcript.
- [ ] A cited span that fails the validator is stripped and its claim shows `⚠ unverified`.
- [ ] A cross-paper claim cites spans in **both** papers; if the compared paper is not in the read
      set, the model says so rather than answering from training knowledge.
- [ ] Reader Q&A goes through the normal agent loop — there is no `ask_paper` tool.

**US5 — Notes**
- [ ] A note is written to `projects/<slug>/notes/*.md` and indexed **in the same operation**.
- [ ] The note is keyed in the DB by its stable YAML frontmatter id, never by file path; moving the
      file does not break highlights, edges or citations.
- [ ] Notes are user-authored ground truth: always editable, never AI-overwritten.
- [ ] A note is readable and editable in any text editor with the app closed.
- [ ] `Unlinked` renders as a first-class dashed state, not a blank.

**US6 — Project memory**
- [ ] `query_memory` returns rows from papers, notes, experiments and past conversations, each
      citing the source row id.
- [ ] Retrieval is the query-time union `paper_chunks(papers in P) ∪ project_chunks(P)`; results
      from another project never appear.
- [ ] Paper embeddings are computed once globally and reused — adding the same paper to a second
      project triggers no re-parse and no re-embed.
- [ ] Conversations persist **verbatim**, with a summary used as index; recall links back to the
      verbatim turns. No AI-invented standalone fact is ever written to memory.
- [ ] Memory contents are user-visible and editable.
- [ ] Chunking splits on docling section boundaries with a token-budget sub-split and small
      overlap.

**US7 — Companion and tabs**
- [ ] The Companion is present on every screen, never a modal, never replaced by the center pane.
- [ ] One WebSocket session per project survives center-pane navigation **and tab switches**; the
      transcript does not reset.
- [ ] Opening a second paper opens a **new tab**; both remain open and independently scrolled.
- [ ] The tab stack is restored after an app restart.
- [ ] Every `user_message` carries a UI-state snapshot that includes the **active tab**;
      incremental `ui_state` pushes are sent mid-turn.
- [ ] `✕ Stop` is visible while a turn runs, hidden when idle; pressing it cancels the turn and
      retains partial results.
- [ ] The loop stops gracefully at the ~8–10 iteration cap.
- [ ] Every tool result reaches the UI **by id**; the rich `ui_view` never enters LLM context and
      the frontend never re-derives it.
- [ ] Anything the user can click, the Companion can do, and both produce the same tool call and
      route transition.
- [ ] Dropped WebSocket: disconnected and reconnecting states render, and the composer states
      whether a queued message will send.
- [ ] Cold start: the window paints before the sidecar is ready; search, notes and the vault tree
      are usable before embeddings are; the readiness strip reports per capability.

### Voice — built immediately after Phase 1

**US8 — Push-to-talk**
- [ ] Push-to-talk only. The microphone never opens without a key held; there is no VAD and no
      idle capture.
- [ ] A spoken turn and a typed turn are indistinguishable to the agent — same tools, same memory,
      same session, no separate code path.
- [ ] `backend/voice/` exposes only `transcribe(audio_bytes, *, lang)` and
      `synthesize(text, *, voice)` plus an engine registry with `stub` selectable by config.
- [ ] **No module outside `backend/voice/` imports an STT/TTS library, names an engine, or knows a
      model exists.** Swapping the engine touches that package and nothing else.
- [ ] `frontend/src/voice/` is the only place `getUserMedia` or an audio element is touched; the
      rest of the app uses one hook.
- [ ] The STT model does not load until the first push-to-talk press.
- [ ] Descope gate: shipping with the stub engine satisfies this phase. `faster-whisper` / Piper
      are the single droppable piece of v1.

### Phase 2 — Experiments

**US9 — Sandboxed notebook and measured metrics**
- [ ] The notebook is a `.ipynb` file in the vault; the vault copy is truth.
- [ ] No code executes anywhere but inside a Docker container — no path exists to run code on the
      host.
- [ ] Mounts are exactly `experiments/<exp>/` read-write and `library/` read-only when needed.
      Never the whole vault, never `$HOME`.
- [ ] Kernel network is off by default; dependencies install at image-build time; a networked run
      is an explicit per-experiment opt-in recorded in the run record.
- [ ] CPU, memory, idle-timeout and per-cell wall-clock limits are enforced; GPU is opt-in per
      experiment via `--gpus`.
- [ ] `propose_cell` writes a cell and **never executes**. Agent-written cells are visibly marked
      unrun and pending approval.
- [ ] `run_all` cannot complete without an explicit human confirmation. There is no auto-run, no
      trusted-experiment mode, no blanket per-project approval.
- [ ] The approval prompt shows the code **and** the container spec (image, mounts, network, GPU).
- [ ] **[pytest]** the D29 `measured` gate suite: `source: measured` is produced **only** by a
      clean restart-and-run-all that exited 0, and always carries `run_id`, image digest,
      `requirements.txt` hash, notebook hash and timestamp. Interactive or out-of-order runs never
      produce it.
- [ ] `source: llm` is impossible — no code path lets the model author a metric value.
- [ ] `source: user` metrics are typed by hand and fully supported.
- [ ] The structured experiment record (hypothesis, setup, metrics, notes, status, graph links) is
      indexed and retrievable. The `.ipynb` content is **not** embedded in v1.
- [ ] Cell execution and kernel lifecycle ride the job queue: cancellable, with logs streaming to
      the UI over the existing WebSocket.
- [ ] Status is the real enum `planned / remaining / in-progress / done`. There is no "failed"
      status and the danger family is never used as a status.

**Phase 2 contingency — descope, do not slip (Grill R3).** If the sidecar ↔ in-container Jupyter
kernel spike fails, Phase 2 falls back to **non-interactive restart-and-run-all only**: execute the
whole notebook in the container via `nbclient` under `docker run`, streaming logs and outputs to
the UI over the existing WebSocket. The **interactive stateful kernel moves post-v1; v1 still
ships.** Under the fallback, every criterion above still applies unchanged except the interactive
kernel ones — `source: measured` provenance, the consent gate, and invariants #4 and #5 all
survive intact, because the evidence-producing run was already defined as a clean
restart-and-run-all. What is lost is only free-form *exploration* (running cells out of order
against warm state), which was already classified as the non-evidential half of the workflow.
This is a stated fallback, not a footnote: take it as soon as the spike fails rather than
extending the phase.

### Phase 3 — Reader depth and literature matrix

**US10 — Literature matrix**
- [ ] Standard columns (Problem / Method / Datasets / Results / Limitations) are a **projection of
      existing extractive cards** — opening the matrix triggers **no re-extraction**.
- [ ] Custom columns run a per-paper scoped extractive query, cached per `(paper, column)`, with
      the `not stated` fallback intact.
- [ ] Editing a cell sets `source: user` and **labels** the override; it never overwrites or
      corrupts the extracted value.
- [ ] Extracted cells carry the quote treatment and click through to the source span; user cells
      render as plain body type.
- [ ] The matrix persists as a project artifact (`selected_paper_ids`, `column_defs`,
      `cell_overrides`, `custom_column_cache`).
- [ ] Experiment records appear as comparable rows in the same matrix.
- [ ] `Matrix` is reachable from the left nav under `DISCOVER`.

**US11 — Knowledge graph**
- [ ] Metadata edges (cites, cited-by, authored-by, uses-dataset, has-code, topic) come from
      OpenAlex / S2 / Papers with Code and are exact.
- [ ] LLM-derived edges are extracted **only for papers the user actually opened**, in the
      existing extraction pass — never as a separate build step.
- [ ] Solid edges = metadata-derived, dashed = LLM-derived, and the legend documents both
      encodings plus every node shape.
- [ ] Node type is encoded by colour **and** shape; the categorical palette never leaks into
      chrome.
- [ ] The graph scope is the project-scoped union, never a global blob.
- [ ] LLM-derived concept nodes are dup-tolerant: under-merging is accepted, false-merging is not.

### Phase 4 — Writing workspace

**US12 — LaTeX**
- [ ] CodeMirror 6 with LaTeX highlighting and live inline KaTeX math.
- [ ] SwiftLaTeX WASM preview is the default and updates within ~1–2 s of a debounced edit; a
      compile-error panel renders failures.
- [ ] Tectonic-in-Docker is available as the escape hatch for final compiles needing full package
      coverage.
- [ ] `\cite` autocomplete pulls from the project's own references; BibTeX export works.
- [ ] Inline citations render with the evidence tint; an unsupported claim renders in the dashed
      treatment as `unsupported claim — no linked source yet`.
- [ ] Missing-citation and consistency checks run and report.
- [ ] Insert paths work for image upload → VFS → `\includegraphics`, Mermaid diagrams, and
      workspace dataviz PNG/SVG exports.
- [ ] **The AI writes no prose and no paper sections.** No code path lets it.

### Phase 5 — Research feed

**US13 — Feed**
- [ ] The interest profile is inspectable and user-editable `{categories, keywords}`, seeded by
      the project's focus seed at creation.
- [ ] Keywords are synonym-expanded at extraction time.
- [ ] Fetch is **category-driven** (broad recall) per source, windowed to "since last poll",
      recency-sorted — not keyword-driven.
- [ ] Ranking is deterministic: synonym keyword match + embedding centroid cosine + cross-encoder
      rerank of the top N. **No LLM in the scoring path.**
- [ ] Every feed item states **why it surfaced** — matched keywords/categories plus similarity. An
      item without a match reason never renders.
- [ ] Dedup runs against the seen set = read ∪ library ∪ previously-surfaced ∪ dismissed;
      dismissed items never resurface.
- [ ] Saving an item adds it to the library and shifts the centroid for the next re-extraction.
- [ ] The feed runs as a catch-up-on-launch job per project, never in a live request path.
- [ ] Weekly (or on meaningful corpus growth) re-extraction reconciles the profile with the
      corpus.

---

## 14. Open Questions & Risks

**Risks carried from `DECISIONS.md`'s own "Open at implementation time" list**

- **Risk:** The sidecar ↔ in-container Jupyter kernel transport (likely `jupyter_client` over ZMQ,
  ports published to loopback only) is **the least-proven part of the design** and a hard
  prerequisite of Phase 2 — *Mitigation: spike it before Phase 2 work starts; if it fails, take
  the Section 13 Phase 2 fallback immediately (non-interactive restart-and-run-all via `nbclient`)
  rather than extending the phase (Grill R3).*
- **Risk:** Local voice is unprototyped (D37) — *Mitigation: spike `faster-whisper` before voice
  work starts; the stub engine already satisfies the v1 scope floor, and the engines are the one
  droppable piece.*
- **Q:** What should be chunked out of a `.ipynb` when notebook content eventually enters the
  memory index — code cells, markdown, text outputs; excluding base64 images and stack traces that
  would pollute retrieval? *Owner: Eng. Not blocking — v1 indexes the structured record only.*
- **Q:** Is the AES-256-GCM key-encryption layer redundant now that key and ciphertext sit on the
  same single-user disk with no network service, making plain OS-keyring storage sufficient?
  *Owner: Eng. Not urgent; it works as specified.*
- **Q:** Feed tuning — the fetch-vs-rank balance as the corpus grows, and the interest-profile
  refresh cadence. *Owner: PM, after Phase 5 lands.*
- **Q:** Does docling's reference extraction hold up, or does GROBID return as a service?
  *Owner: Eng, decided against real papers in Phase 1.*
- **Q:** Force-graph library — Cytoscape vs react-force-graph. *Owner: Eng, pick at build; D26
  leaves it open.*

**Design gaps carried from `UI_DESIGN.md`**

- **Q:** Three screens have no mock — the onboarding wizard (D35), settings / model config (D13),
  and the literature matrix (D27). *Owner: Design — derive from `UI_DESIGN.md` §1–§3; no auth
  screen is needed.*
- **Q:** `UI_DESIGN.md` predates D30 and has **no notebook screen**. The Experiments pane is
  designed freehand against the existing visual language. *Owner: Design. Inspiration-rank, so not
  blocking.*
- **Q:** Matrix cell provenance treatment and the expanded reader references list are undrawn.
  *Owner: Design.*
- **Q:** Graph vocabulary is inconsistent — filter chip reads `Method`, legend reads
  `Method / concept`. Pick one and document any abbreviation. *Owner: Design.*
- **Risk:** The frame grid sits at `0.16` on nine of ten mocks; reading-heavy screens need `0.07`
  and it may want to be global — *Mitigation: dim on Reader, Writing and Notes first, then judge.*
- **Risk:** The `700 10px` badge/label size is the riskiest AA-contrast combination in the system
  and is now used in more places than the previous revision — *Mitigation: verify AA on muted text
  and tinted badges explicitly before each phase sign-off.*
- **Risk:** The mock's fixed `min-width: 1380px` canvas is not a product constraint; real
  responsive behaviour at ~1280px and below is undesigned — *Mitigation: collapse the nav to icons
  before ever dropping the Companion; dropping the Companion breaks the product premise.*

**Tradeoffs already made, recorded so they are not reopened**

- **Tradeoff:** Interactive out-of-order cell runs can never produce a `measured` metric. Hidden
  kernel state makes the number unverifiable; the price is paid here rather than by weakening the
  provenance rule (D29).
- **Tradeoff:** GPU contention between a resident vLLM server and an experiment container is real
  and deliberately unhandled — the user stops one by hand (D11). Out of scope by decision; not a
  risk to re-raise.
- **Tradeoff:** No owned literature index. Live federation costs latency on every search and buys
  not building a 250M-work snapshot before feature one works (D20).
- **Tradeoff:** Rebuilding `.research-os/` costs minutes to hours of re-parse and re-embed. Files
  stay truth; that is the point (D3).
- **Tradeoff:** Center-pane tabs (Grill R5) add routing and persistence complexity that
  single-pane-per-route avoided. Accepted on direct user instruction.

**Needs confirmation**

- **Q:** *(Derived, not a user answer.)* The Companion stays **one WebSocket session per project,
  not per tab** — it does not switch sessions when the active tab changes; the active tab is
  reported in the `ui_state` payload. This follows from D32's "one WebSocket session per project,
  surviving center-pane navigation": tabs make navigation richer, not multi-session. *Owner: PM —
  confirm at PRD sign-off if this reads as a decision rather than a consequence.*

**Notes for the agents that own the pointer sections**

- *For schema-agent (Section 8):* the global/project boundary, the canonical-id priority
  (DOI → arXiv → OpenAlex/S2), the two memory tables (`paper_chunks` without `project_id`,
  `project_chunks` with it, both `{embedding vector(768), tsv, source_type, source_id,
  char_span}`), the quote-anchor shape `{quote, prefix, suffix}` + cached page/bbox hint, the
  experiment `runs[]` shape, the four-value relevance enum, and the four-value experiment status
  enum are all fully specified in `DECISIONS.md` D25, D29, D33 — pick them up there. There are no
  `users`, `owner_id`, or `storage_connections` tables, by decision.
- *For trd-agent (Section 11):* the stack is already locked — D2, D6–D15, D18, D30, D32–D34 — and
  Appendix A lists what was rejected and must not be re-proposed. The tab stack (Grill R5) is the
  one routing change the decisions do not yet describe.
- *For design-decisions-agent (Section 7):* `UI_DESIGN.md` §1 (tokens), §3 (cross-screen
  components) and §5 (rules to carry into the build) are the component source; §2's tab strip is
  now spec, not contingency.

---

## 15. Rollout & Next Steps

**MVP scope.** There is no reduced MVP: **v1 is the whole product — all five phases plus voice**
(Grill R1). The smallest shippable unit that validates the core job is **Phase 1**, which is why it
is built and hardened first, over text, where every harness event is debuggable.

- **Includes:** Phases 1–5, the voice layer at its D37 module-boundary-plus-stub floor, the three
  cross-phase contracts (tool catalog, memory index, quote anchor), and all five invariants.
- **Excludes:** everything in Section 3's Out of scope, and the two named descope levers below.

**Descope levers, in the order they may be pulled.**
1. **Voice engines.** If the D37 spike shows `faster-whisper` / Piper are too heavy, ship the stub
   engine behind the module boundary and slip the real engines post-v1. Nothing else changes.
2. **The interactive kernel.** If the Phase 2 kernel spike fails, ship non-interactive
   restart-and-run-all only. Provenance, consent, and invariants #4 and #5 survive intact.

Neither lever slips v1. No third lever exists.

**Build and review order** (Grill R4) — one PRD, phase-by-phase delivery, **a hard stop for the
user's sign-off at each boundary. No phase begins before the previous one is signed off.**

1. Phase 1 → sign-off
2. Voice layer → sign-off
3. Phase 2 (kernel spike first) → sign-off
4. Phase 3 → sign-off
5. Phase 4 → sign-off
6. Phase 5 → sign-off

**Post-v1 ideas** (recorded, not scheduled): interactive kernel if descoped; real voice engines if
descoped; always-on VAD; notebook content in the memory index; two-layer extractive→paraphrase card
display; full negative-example learning for feed dismissals; a small routing model for latency;
W&B/MLflow ingestion; GROBID-as-a-service; external vault editing (the dropped watchdog design is
recoverable from git at `b53bff8`).

**Sign-off needed from:**
- [ ] The user — at each of the six phase boundaries above. This is a solo project; the user is PM,
      engineering lead and design.

**Next steps:**
1. Approve this PRD. — *Owner: user*
2. Write `TRD.md` from `DECISIONS.md` D2, D6–D15, D18, D30, D32–D34. — *Owner: trd-agent*
3. Write `Schema.md` from D25/D29/D33. — *Owner: schema-agent*
4. Spike the sidecar ↔ in-container kernel transport before Phase 2 planning hardens. —
   *Owner: Eng*
5. Spike `faster-whisper` before voice work starts. — *Owner: Eng*
