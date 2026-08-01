# Implementation Plan

**Vocabulary (binding, per PRD/TRD/Schema/Rules/MODULES).** Build units are **Phase 1 … Phase 5**
plus the cross-cutting **Voice** phase. The word "Slice" does not appear anywhere in this document.
Each phase below is broken into numbered sub-units (`Phase N.M`) so a coding agent can pick up one
vertical, independently runnable piece at a time; the **last sub-unit of every phase is that
phase's hard sign-off checkpoint** — no work on the next phase begins until it passes the PRD §13
manual acceptance checklist for that phase (plus the relevant pytest suite where one exists).

Build order, fixed by PRD §15 / TRD §2: **Phase 1 → Voice → Phase 2 (kernel spike first) →
Phase 3 → Phase 4 → Phase 5.**

---

## Phase 1.1: Sidecar boots, window paints, readiness strip is live

### What this delivers
Launching the app spawns the FastAPI sidecar, opens the Electron window immediately, and shows a
readiness strip that turns each capability green as `GET /api/health` reports it — the minimal
runnable, user-observable foundation the rest of the system builds on.

### Depends on
none

### Touches
`desktop/` (Desktop Shell) — spawn/supervise sidecar, per-launch bearer token, `{port, token}` via
preload; `backend/main.py` (Sidecar Bootstrap) — `create_app`, `get_readiness`, lifespan launch
sequence (vault check → `docker compose up -d` → Alembic migrations → job worker → catch-up pass);
`backend/db/` (Database Layer) — connection pool, `run_migrations`; `backend/vault/` (Vault Writer)
— reads vault path at startup; `backend/settings/` (Settings Store) — `get_vault_path`;
`backend/jobs/` (Job Queue) — worker startup, `run_catchup_pass`; `backend/api/` (REST API) —
`GET /api/health`; `frontend/src/app/` (App Shell) — top bar + nav shell; `frontend/src/state/`
(Client State) — polls `GET /api/health`; `frontend/src/design/` (Design Tokens); `packages/api-client/`
(Generated API Client) — initial OpenAPI-generated client; `docker/` — Postgres+pgvector compose;
tables: `api_keys`, `scheduled_jobs`, `result_store` (created by Phase 1 Alembic migrations).

---

## Phase 1.2: Gated onboarding wizard creates the first project

### What this delivers
A first-run user completes the four required onboarding steps (Docker check, vault folder,
validated LLM endpoint, first project with optional focus seed) and lands in a working, empty
project — satisfying US1.

### Depends on
Phase 1.1

### Touches
`backend/settings/` (Settings Store) — `save_provider`, `discover_models`, `set_voice_engine`
default; `backend/api/` (REST API) — `GET/PUT /api/settings/models`, `GET/POST /api/projects`;
`backend/db/` (Database Layer) — `projects` table writes; `frontend/src/onboarding/` (Onboarding
Wizard) — four-step gated flow, error card with retry on invalid key/unreachable Docker;
`frontend/src/settings/` (Settings Panel) — shared provider-key form components; `frontend/src/state/`
(Client State); `frontend/src/design/` (Design Tokens); tables: `api_keys` (`providers`,
`primary_model`, `auxiliary_model`, `vault_path`, `onboarding_completed_at`), `projects` (`id`,
`name`, `slug`, `focus_seed`, `interest_profile` default).

---

## Phase 1.3: Federated search returns one deduped, reranked result list

### What this delivers
A user types a natural-language query and sees one deduped, cross-encoder-reranked, streaming
result list fanned out across arXiv, OpenAlex and Semantic Scholar, cached under a `result_id` —
satisfying US2.

### Depends on
Phase 1.2

### Touches
`backend/search/` (Search Federation) — `search_papers`, `refine_results`, one LLM
query-understanding pass, deterministic per-source parameter mapping, canonical-id dedup;
`backend/papers/` (Paper Pipeline) — `resolve_canonical_id` (DOI → arXiv → OpenAlex/S2), used by
Search Federation; `backend/llm/` (LLM Gateway) — `complete`/`complete_structured` for query
understanding; `backend/db/` (Database Layer) — `result_store` cache writes; `backend/api/`
(REST API) — `POST /api/search`, `GET /api/results/:resultId`; `frontend/src/search/` (Search
Results) — per-source progress, real cards + skeletons, error card naming what still worked;
`frontend/src/state/`, `frontend/src/design/`; **pytest:** `tests/` — D25 canonical-id dedup suite
(DOI/arXiv/OpenAlex/S2 priority paths and collisions), fixture-based, no network.

---

## Phase 1.4: Reader renders the real PDF with a validated extractive card

### What this delivers
Opening a paper (from search or a direct add) fetches its open-access PDF or accepts a user
upload, parses it with docling, renders the real PDF.js pages with a structure sidebar, and shows
an extractive card where every field is a verbatim span validated by the deterministic substring
validator — satisfying US3.

### Depends on
Phase 1.3

### Touches
`backend/papers/` (Paper Pipeline) — `add_paper`, `get_paper`, `parse_paper_job`,
`extract_card_job`, `enrich_paper_job`; `backend/provenance/` (Provenance) — `validate_and_anchor`,
`locate`; `backend/vault/` (Vault Writer) — `write_paper_asset`; `backend/graph/` (Knowledge Graph)
— `write_metadata_edges`, `write_llm_edges` (written now, surfaced Phase 3); `backend/jobs/` (Job
Queue) — fetch/parse/extract job dispatch; `backend/api/` (REST API) —
`POST /api/projects/:id/papers`, `GET /api/papers/:paperId`, `GET /api/papers/:paperId/pdf`,
`PATCH /api/projects/:id/papers/:paperId`; `frontend/src/reader/` (Reader) — `ReaderTab`,
`useAnchorSync`; `frontend/src/library/` (Library View) — relevance control, processing badges;
tables: `papers`, `paper_content`, `quote_anchors`, `paper_cards`, `paper_edges`, `project_papers`;
**pytest:** `tests/` — D24 provenance substring validator suite, D33 fuzzy quote locator suite
(whitespace/hyphenation/ligature variants across docling text and PDF.js text layer), both
fixture-based, no network/DB/Docker.

---

## Phase 1.5: Ask-about-highlight through the Companion, with cited answers

### What this delivers
A user selects text in the reader, asks the Companion about it through the normal agent loop, and
receives an answer whose every factual claim carries an inline citation to a validated span, with a
failed citation stripped and shown as `⚠ unverified` — satisfying US4.

### Depends on
Phase 1.4

### Touches
`backend/harness/` (Agent Harness) — `run_turn` with reader Q&A as an ordinary tool call (no
`ask_paper` tool), citation stripping on failed validation; `backend/provenance/` (Provenance) —
reused `validate_and_anchor` for citation checks; `backend/vault/` (Vault Writer) —
`write_highlight`; `backend/ws/` (Session Transport) — carries the turn's `text_delta` /
`tool_result` / `ui_action` events; `backend/db/` (Database Layer) — `messages.citations` writes;
`backend/api/` (REST API) — `POST /api/projects/:id/highlights`; `frontend/src/reader/` (Reader) —
selection popover (`Ask about this` / `Highlight` / `Explain`); `frontend/src/companion/`
(Companion Pane) — cited-evidence vs. reasoning visual distinction, `⚠ unverified` badge; tables:
`highlights`, `quote_anchors`, `conversations`, `messages`.

---

## Phase 1.6: Plain-file notes, written and indexed in one operation

### What this delivers
A user writes a markdown note that is saved to `projects/<slug>/notes/*.md` and indexed in the same
operation, keyed by its stable frontmatter id rather than its path — satisfying US5.

### Depends on
Phase 1.2

### Touches
`backend/vault/` (Vault Writer) — `write_note` (assigns frontmatter id on create, file-then-index
transaction); `backend/db/` (Database Layer); `backend/api/` (REST API) —
`GET/POST/PATCH /api/projects/:id/notes`; `frontend/src/notes/` (Notes Editor) — `NotesView`,
`Unlinked` dashed empty state; tables: `notes`.

---

## Phase 1.7: Project memory — cited rows over papers, notes, experiments, conversations

### What this delivers
A user asks the project a question and gets back cited rows drawn from the query-time union of the
project's paper chunks and its own project chunks (notes, experiments, conversation summaries),
each linking back to its source row — satisfying US6.

### Depends on
Phase 1.4, Phase 1.6

### Touches
`backend/memory/` (Memory Index) — `chunk_and_embed_job`, `query_memory`, section-aware chunking
with token-budget sub-split; `backend/db/` (Database Layer) — `hybrid_retrieve`; `backend/harness/`
(Agent Harness) — `query_memory` wired as a tool; `backend/jobs/` (Job Queue) — embed job dispatch;
`backend/api/` (REST API) — `POST /api/projects/:id/memory/query`; `frontend/src/companion/`
(Companion Pane) — renders cited rows; tables: `paper_chunks`, `project_chunks`, `conversations`
(`summary`, `summarised_through_seq`), `messages`.

---

## Phase 1.8: One Companion session, persistent tab stack — Phase 1 sign-off

### What this delivers
The Companion stays connected as one WebSocket session per project across every screen and every
tab switch, the center-pane tab stack persists across an app restart, interrupt is first-class, and
opening a second paper opens a new independently-scrolled tab — the full Phase 1 acceptance
checklist (US1–US7) is now demonstrable end to end and is the Phase 1 sign-off checkpoint.

### Depends on
Phase 1.5, Phase 1.7

### Touches
`backend/ws/` (Session Transport) — `handle_connect`, `handle_message`, `broadcast`, session
registry keyed by project; `backend/harness/` (Agent Harness) — `interrupt`, iteration-cap graceful
stop, `ui_state` merge mid-turn; `backend/db/` (Database Layer) — `projects.tab_stack`,
`projects.active_tab` persistence; `backend/api/` (REST API) — tab-stack read/write path;
`frontend/src/app/` (App Shell) — `useTabStack`, tab-stack rehydration on launch;
`frontend/src/companion/` (Companion Pane) — `✕ Stop`, disconnected/reconnecting states, queued-send
messaging; `frontend/src/state/` (Client State) — `useCompanionSocket`, `useUIState`, reconnect
backoff; `frontend/src/dashboard/` (Dashboard) — `CONTINUE WHERE YOU LEFT OFF` resume tiles;
tables: `projects.tab_stack`, `projects.active_tab`, `projects.last_opened_at`.

---

## Voice.1: Push-to-talk boundary, wired end to end with the stub engine

### What this delivers
A user holds a key, speaks, and a spoken turn hits the identical agent session and tools as a typed
turn — the D37 module boundary plus the stub engine (canned STT text, beep/silence TTS) shipped end
to end, satisfying US8 at the v1 scope floor. This is the Voice phase's sign-off checkpoint; the
real `faster-whisper`/Piper engines are the only piece allowed to slip post-v1 without changing
anything else.

### Depends on
Phase 1.8

### Touches
`backend/voice/` (Voice Engine) — `transcribe`, `synthesize`, engine registry with `stub` selected
by `api_keys.voice_engine`, lazy load on first push-to-talk press; `backend/api/` (REST API) —
`POST /api/voice/transcribe`, `POST /api/voice/synthesize`; `backend/ws/` (Session Transport) —
`input_modality` tagging on `user_message`; `frontend/src/voice/` (Voice Capture) — `useVoice`, the
only module touching `getUserMedia`/audio element; `frontend/src/companion/` (Companion Pane) —
mic control wired to `useVoice`; table: `messages.input_modality`; `api_keys.voice_engine`.

---

## Phase 2.1: Execution Sandbox interface and the kernel-transport spike

### What this delivers
A `propose_cell` call writes an unrun, pending-approval cell into a per-experiment notebook backed
by a Docker container spec (mounts, network-off default, CPU/memory/idle/GPU-opt-in limits) behind
one interface — and the sidecar↔in-container `jupyter_client`/ZMQ kernel transport spike runs here,
before any other Phase 2 work, with its outcome recorded as the descope decision point.

### Depends on
Phase 1.8

### Touches
`backend/sandbox/` (Execution Sandbox) — `propose_cell`, container spec construction, mount/limit
enforcement, the interactive-kernel-vs-`nbclient`-fallback interface; `backend/experiments/`
(Experiment Record) — `create_experiment`/`update_experiment` backing the notebook's owning record;
`backend/vault/` (Vault Writer) — `write_experiment_files` (`notebook.ipynb`,
`requirements.txt`); `docker/` — pinned experiment base image (numpy/pandas/torch/scikit-learn/
matplotlib), `uv`-layered per-experiment deps; `backend/api/` (REST API) —
`GET/POST/PATCH /api/projects/:id/experiments`; `frontend/src/experiments/` (Experiments Board) —
board + unrun/pending-approval cell marking; tables: `experiments`.

**Descope trigger and decision point.** If the `jupyter_client`/ZMQ transport spike does not reach
a working per-experiment interactive kernel by the end of this sub-unit, immediately swap Execution
Sandbox's implementation behind the same public interface to the `nbclient`-under-`docker-run`
fallback (TRD §2.7) and continue — do not extend Phase 2 to keep trying the interactive path. Every
downstream Phase 2 sub-unit is written against the interface only and is unaffected either way.

---

## Phase 2.2: Approval gate mints the only path to container execution

### What this delivers
A user reviews the proposed code and the full container spec (image, mounts, network, GPU) in the
approval prompt and explicitly confirms before `run_all` is allowed to execute anything — the
consent gate (invariant #5) working end to end, independent of the Docker sandbox itself
(invariant #4).

### Depends on
Phase 2.1

### Touches
`backend/sandbox/` (Execution Sandbox) — `mint_confirmation`, `run_all` token validation,
`stop_kernel`; `backend/api/` (REST API) — `POST /api/experiments/:id/kernel`,
`POST /api/experiments/:id/run_all`; `backend/jobs/` (Job Queue) — cancellable run job dispatch,
log/output streaming over `backend/ws/` (Session Transport); `frontend/src/experiments/`
(Experiments Board) — `ApprovalPrompt`, the only UI path minting a `confirmation_token`; tables:
`experiment_runs` (`approved_at` NOT NULL as the consent gate at rest).

---

## Phase 2.3: Measured metrics from a clean restart-and-run-all — Phase 2 sign-off

### What this delivers
A user runs "Restart & run all" on an approved experiment inside the Docker container and, only on
a clean run that exits 0, gets a metric carrying `source: measured` with its run id, image digest,
`requirements.txt` hash, notebook hash and timestamp — comparable as a row beside published results
— satisfying US9 and closing Phase 2 with the D29 gate as its sign-off criterion.

### Depends on
Phase 2.2

### Touches
`backend/experiments/` (Experiment Record) — `record_metric` (`source` CHECK-limited to
`user`/`measured`), `record_run`; `backend/sandbox/` (Execution Sandbox) — completes the run,
captures exit code/image digest/hashes, hands the result to Experiment Record; `backend/db/`
(Database Layer) — `CHECK (source <> 'measured' OR run_id IS NOT NULL)`,
`experiment_runs.run_kind` (`clean_run_all`/`interactive`) gating promotion; `backend/api/`
(REST API) — `GET /api/runs/:runId`; `frontend/src/experiments/` (Experiments Board) — status enum
rendering (`planned`/`remaining`/`in-progress`/`done`, no `failed`/danger status), log streaming
display; tables: `experiment_metrics`, `experiment_runs`; **pytest:** `tests/` — D29 `measured` gate
suite (fixture run records; clean `run_kind=clean_run_all` + exit 0 + all four provenance fields
required, interactive/out-of-order/non-zero-exit never promoted).

---

## Phase 3.1: Reader depth — references, datasets, code, cross-paper compare

### What this delivers
The reader's structure sidebar now surfaces the full references list, datasets and code links
parsed in Phase 1, and a Companion citation can compare claims across two open papers, saying so
explicitly if the second paper is not in the read set — extending US3/US4 with the reader-depth
half of Phase 3.

### Depends on
Phase 2.3

### Touches
`backend/papers/` (Paper Pipeline) — `get_paper(include=references,datasets,code)` surfaced reads
(data already written in Phase 1.4's `parse_paper_job`/`enrich_paper_job`); `backend/harness/`
(Agent Harness) — cross-paper citation tool behaviour (cites spans in both papers or declines);
`frontend/src/reader/` (Reader) — expanded references-list panel, open-reference navigation; tables
read (no schema change): `paper_content.references`, `paper_content.datasets`,
`paper_content.code_links`.

---

## Phase 3.2: Literature matrix — extractive-card projection with editable overrides

### What this delivers
A user builds a matrix over selected papers whose standard columns are a pure projection of
existing extractive cards (no re-extraction), can add a custom per-paper scoped-query column cached
per `(paper, column)`, and can edit a cell as a labelled `source: user` override without corrupting
the extracted value — satisfying US10.

### Depends on
Phase 3.1

### Touches
`backend/matrix/` (Literature Matrix) — `build_matrix`, `update_matrix`, `get_matrix_view`,
`update_cell`; `backend/papers/` (Paper Pipeline) — projected `paper_cards` read; `backend/llm/`
(LLM Gateway) — `complete_structured` for custom-column scoped extraction; `backend/provenance/`
(Provenance) — validates custom-column extractions before caching; `backend/api/` (REST API) —
`GET/PUT /api/projects/:id/matrix/:matrixId`; `frontend/src/matrix/` (Matrix View) — grid, quote
treatment vs. plain body type by `source`; `frontend/src/app/` (App Shell) — `Matrix` entry under
`DISCOVER` nav group; tables: `matrices`, `matrix_cells`.

---

## Phase 3.3: Knowledge graph — project-scoped union, typed and provenance-tagged — Phase 3 sign-off

### What this delivers
A user explores a project-scoped knowledge graph where metadata edges render solid and LLM-derived
edges render dashed, node type is encoded by colour and shape, and the legend documents both —
satisfying US11 and closing Phase 3.

### Depends on
Phase 3.2

### Touches
`backend/graph/` (Knowledge Graph) — `get_graph` surfaced now (edges already written by Phase 1.4's
`write_metadata_edges`/`write_llm_edges` and any Phase-2-linked `idea_edges`); `backend/db/`
(Database Layer) — `traverse_graph` recursive-CTE; `backend/api/` (REST API) —
`GET /api/projects/:id/graph`; `frontend/src/graph/` (Graph View) — canvas + legend + filter chips
(force-graph library choice made at this build point, Cytoscape.js or react-force-graph per TRD
§1.3); tables: `paper_edges`, `idea_edges` (read-surfaced; `idea_edges` writable from Phase 2
experiment links).

---

## Phase 4.1: LaTeX editor with live math and debounced preview

### What this delivers
A user writes LaTeX in a CodeMirror 6 editor with live inline KaTeX math and sees a SwiftLaTeX WASM
preview update within ~1–2 s of a debounced edit, with a Tectonic-in-Docker escape hatch available
for a final full-package compile — the editing half of US12.

### Depends on
Phase 1.8

### Touches
`backend/writing/` (Manuscript) — `save_document`; `backend/vault/` (Vault Writer) —
`write_document`; `docker/` — Tectonic image; `backend/api/` (REST API) —
`GET/POST /api/projects/:id/documents`; `frontend/src/writing/` (Manuscript Editor) —
`ManuscriptTab`, compile-error panel; tables: `documents` (`file_path`, `body`,
`last_compiled_at`, `last_compile_engine`).

---

## Phase 4.2: Citation insertion and unsupported-claim checks — Phase 4 sign-off

### What this delivers
A user autocompletes `\cite` from the project's own references, exports BibTeX, and sees an
unsupported claim rendered as `unsupported claim — no linked source yet` after a citation check —
completing US12 with the AI writing no prose or paper sections anywhere in the path, and closing
Phase 4.

### Depends on
Phase 4.1

### Touches
`backend/writing/` (Manuscript) — `check_citations`, `autocomplete_citations`, `export_bibtex`;
`backend/memory/` (Memory Index) — reference lookup backing autocomplete; `backend/api/` (REST API)
— citation-check and BibTeX export routes on `/api/projects/:id/documents`; `frontend/src/writing/`
(Manuscript Editor) — `\cite` autocomplete, inline evidence-tinted citations, dashed unsupported-
claim treatment; tables: `documents.citation_findings`.

---

## Phase 5.1: Interest profile and catch-up-on-launch category fetch

### What this delivers
On launch, an overdue per-project feed poll fetches broadly by category since the last poll against
the project's inspectable, user-editable interest profile (seeded by the focus seed), deduping
against the seen set — the fetch half of US13.

### Depends on
Phase 1.8

### Touches
`backend/feed/` (Research Feed) — `poll_feed_job`, `get_interest_profile`/`update_interest_profile`,
synonym expansion, category-driven fetch, seen-set anti-join; `backend/jobs/` (Job Queue) —
`scheduled_jobs` catch-up dispatch for `job_kind='feed_poll'`; `backend/db/` (Database Layer) —
seen-set anti-join query; `backend/api/` (REST API) —
`GET/PUT /api/projects/:id/interest-profile`; `frontend/src/settings/` (Settings Panel) or
project-level view exposing the editable profile; tables: `feed_items` (write path begins),
`seen_set`, `scheduled_jobs` (`job_kind='feed_poll'`).

---

## Phase 5.2: Deterministic feed ranking with why-relevant, save and dismiss — Phase 5 sign-off

### What this delivers
Every surfaced feed item states why it surfaced (matched keywords/categories plus similarity) via a
non-LLM deterministic rank (synonym match + centroid cosine + cross-encoder rerank), a user can save
an item into the library (shifting the corpus centroid) or dismiss it (never resurfacing), and the
weekly/on-growth re-extraction reconciles the profile — completing US13 and Phase 5, the last phase
in v1 scope.

### Depends on
Phase 5.1

### Touches
`backend/feed/` (Research Feed) — deterministic scoring, `save_item`/`dismiss_item`,
`interest_profile_reextract` job; `backend/papers/` (Paper Pipeline) — `add_paper` invoked on save;
`backend/db/` (Database Layer) — `projects.corpus_centroid` update; `backend/api/` (REST API) —
`GET/POST /api/projects/:id/feed`; `frontend/src/feed/` (Feed View) — card list with why-relevant,
save/dismiss actions; tables: `feed_items` (`score`, `why_relevant`, `state`), `seen_set`,
`projects.corpus_centroid`, `scheduled_jobs` (`job_kind='interest_profile_reextract'`).
