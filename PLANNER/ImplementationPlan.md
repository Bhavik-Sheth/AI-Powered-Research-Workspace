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

---

# Fix Round 1 — post-V0 issue fixes (Phase 6)

**Same binding vocabulary as above.** Build units are `Phase 6.M`, each a vertical,
independently runnable slice; the last sub-unit (Phase 6.11) is this round's hard sign-off.
Source of the issue list: `PLANNER/Things_to_finish.md`. Every decision below was resolved
in the grill session recorded in `PLANNER/GrillLog.md` (Fix Round 1 section) — this document
introduces no new decision.

Build order, fixed by the grill session: **search → references → graph → experiments → dashboard**.
References come before graph on purpose: the empty References box and the missing graph tracebacks
are one root cause (nothing in the backend ever writes a `cites` edge), so Phase 6.3/6.4 fix both.

---

## Phase 6.1: Firecrawl ranks the search, top 5 results, deterministic fallback

### What this delivers
Searching "attention is all you need" returns that paper first: Firecrawl `/search` becomes the
relevance authority and its result order drives ranking, with arXiv/OpenAlex/S2 demoted to
enrichment (resolving each Firecrawl hit to a real canonical id, citation count and OA PDF so
dedup, `add_paper` and the whole Paper Pipeline are unchanged). Exactly 5 results render. When no
`FIRECRAWL_API_KEY` is configured, the quota is exhausted, or the call fails, search degrades to
today's arXiv/OpenAlex/S2 fan-out ranked by a new deterministic lexical score (exact-title match,
phrase overlap, citation count) and names `firecrawl` in `sources_failed` — search is never
unusable and never falls back to raw source order again.

### Depends on
none

### Touches
`backend/search/` (Search Federation) — new `search_firecrawl` client in `sources.py`, new
deterministic `lexical_score` module, `search_papers` reordered to rank-then-enrich, `_rank`
replaced (the cross-encoder in `reranker.py` becomes an optional refinement, never the only
ranker); `backend/config.py` — reads `FIRECRAWL_API_KEY` (flagged deviation: D13's
Settings-Store-encrypted path covers *LLM provider* credentials; Firecrawl is an infra key, same
category as the DB DSN — recorded in the D21 amendment below); `backend/.env.example` — documents
`FIRECRAWL_API_KEY`; `backend/papers/` (Paper Pipeline) — `resolve_canonical_id` reused unchanged
to key enrichment; `backend/api/search.py` (REST API) — `POST /api/search` gains a `limit`
(default 5); `frontend/src/search/` (Search Results) — renders 5 cards; `packages/api-client/`
(Generated API Client) — regenerated; `PLANNER/DECISIONS.md` — D21 amended (Firecrawl is the
relevance authority; the three literature APIs become enrichment sources), invariants unchanged.

---

## Phase 6.2: "Search more" widens the search only when asked

### What this delivers
A `Search more` control below the 5 results reveals the rest of the already-fetched pool
(results 6 onward) with no network call; once that pool is exhausted, a further click runs a
genuinely wider query (next Firecrawl page / deeper per-source `max_results`) and appends the new
results. No additional papers are ever searched, resolved or enriched unless the user asks.

### Depends on
Phase 6.1

### Touches
`backend/search/` (Search Federation) — `search_papers` gains a `page`/`offset` parameter,
`refine_results` extended to serve an already-cached page from `result_store` without re-querying;
`backend/api/search.py` (REST API) — `GET /api/results/:resultId` accepts an offset,
`POST /api/search` accepts `page`; `frontend/src/search/` (Search Results) — `Search more` button,
reveal-then-widen state, exhausted state; `packages/api-client/` (Generated API Client) —
regenerated; table: `result_store` (`ui_view` holds the full fetched pool, `expires_at` unchanged).

---

## Phase 6.3: A paper's top 5 references appear in the References box and are clickable

### What this delivers
Opening a paper shows its top 5 references (ranked by citation count) in the Reader's References
box, each with a real title, and clicking one opens that paper. References come from OpenAlex
`referenced_works` / Semantic Scholar `references`; a paper with no API record falls back to
parsing the References section out of the docling `full_text`. Each reference is written as a
metadata-only stub `papers` row (title/authors/year/canonical id, no PDF fetch, no parse, no
extraction), so clicking it opens a lightweight detail with an `Add to library` action that
triggers the full pipeline on demand.

### Depends on
none

### Touches
`backend/papers/` (Paper Pipeline) — new `trace_references_job` (API-first, PDF-section fallback),
new `add_reference_stub` (writes a `papers` row with `fetch_state='skipped'`), `parse_paper_job`
enqueues it, `parser.py` gains reference-section splitting; `backend/search/sources.py`
(Search Federation) — reference-list fetch reused from the existing OpenAlex/S2 clients;
`backend/api/papers.py` (REST API) — `GET /api/papers/:id/content` returns resolved references
with `paper_id`, `POST /api/projects/:id/papers/:paperId/promote` promotes a stub to a full paper;
`frontend/src/reader/ReaderTab.tsx` (Reader) — References box renders clickable rows +
`Add to library`; `packages/api-client/` (Generated API Client) — regenerated; tables: `papers`
(stub rows), `paper_content.references_` (now populated).

---

## Phase 6.4: Citations reach the Knowledge Graph, and old papers heal on open

### What this delivers
The traced references are written as `cites` edges, so the Graph View finally shows real
paper→paper tracebacks with real titles (the stub rows from Phase 6.3 supply
`Graph.paper_titles`). Papers added before this round heal automatically: opening a paper whose
reference trace has never run enqueues it once, and the Library's existing `Retry` action
re-runs it on demand — no mass migration job, no startup stall.

### Depends on
Phase 6.3

### Touches
`backend/graph/` (Knowledge Graph) — `write_metadata_edges` called with `relation='cites'`,
`source_api='openalex'|'s2'`; `backend/papers/` (Paper Pipeline) — `trace_references_job` writes
the edges, `reprocess_paper` re-drives the trace stage, an open-paper read enqueues the trace when
it has never run; `backend/api/papers.py` (REST API) — open-paper read path;
`frontend/src/graph/GraphView.tsx` (Graph View) — no change beyond consuming the new edges;
tables: `paper_edges` (`relation='cites'`, `provenance='metadata'`), `papers` (a new
`references_state` column, mirroring `parse_state`/`extract_state`, via a new Alembic migration).

---

## Phase 6.5: Implementation repos and dataset sources are traced

### What this delivers
An opened paper shows where its code and datasets actually live: URLs harvested verbatim from the
parsed full text (github.com / gitlab / huggingface.co / zenodo / kaggle), then — if the paper
states none — HuggingFace's papers API by arXiv id (the real successor to the discontinued
Papers-with-Code), then Firecrawl search for `"<title>" official implementation` as a last resort.
Results populate `paper_content.code_links` / `datasets` and become `has_code` / `uses_dataset`
edges in the graph. The dead paperswithcode.com call is removed.

### Depends on
Phase 6.1

### Touches
`backend/papers/` (Paper Pipeline) — `enrich_paper_job` rewritten: text-scan → HuggingFace papers
API → Firecrawl fallback, PwC call deleted, writes `code_links`/`datasets` instead of leaving them
`[]`; `backend/search/sources.py` (Search Federation) — Firecrawl client reused;
`backend/graph/` (Knowledge Graph) — `has_code` metadata edges (`source_api='huggingface'|'text'`),
`uses_dataset` edges; `backend/api/papers.py` (REST API) — content response carries the links;
`frontend/src/reader/ReaderTab.tsx` (Reader) — code/dataset links rendered with their origin;
`PLANNER/DECISIONS.md` — D26 amended (enrichment source is the paper's own text → HuggingFace →
Firecrawl, never PwC); tables: `paper_content` (`code_links`, `datasets`), `paper_edges`.

---

## Phase 6.6: Graph node labels are readable

### What this delivers
A node's title is legible on the canvas: labels wrap onto up to 3 lines at ~160px instead of
ellipsising mid-word, layout spacing grows so wrapped labels do not collide, and hovering any node
shows its complete title. Selecting a node still shows the full title and its connections in the
detail panel.

### Depends on
Phase 6.4

### Touches
`frontend/src/graph/GraphView.tsx` (Graph View) — `STYLESHEET` label rules (`text-wrap: wrap`,
`text-max-width`, `text-max-lines`), `LAYOUT` node repulsion/padding, hover tooltip via a
`mouseover`/`mouseout` handler; `frontend/src/graph/GraphView.css` (Graph View) — tooltip styling
from Design Tokens; `frontend/src/graph/nodeStyle.ts` (Graph View) — unchanged legend.

---

## Phase 6.7: A notebook survives navigating away

### What this delivers
Leaving an experiment no longer destroys work. The per-experiment Jupyter server stays alive when
the user navigates away or collapses the card, and coming back reattaches to the same live session
(kernel state intact); it stops only on the explicit `Stop notebook` action or the existing 4-hour
ceiling. Every stop path first forces a save through Jupyter's own REST API and waits for
`notebook.ipynb` to land in the vault at
`~/ResearchOS/projects/<project>/experiments/<experiment>/notebook.ipynb` before the container is
removed — so nothing is ever lost to an unsaved autosave window.

### Depends on
none

### Touches
`backend/sandbox/` (Execution Sandbox) — `stop_notebook_server` gains a save-then-verify step
(`PUT`/`POST` against the container's Jupyter contents API, then confirm the vault file's mtime
advanced) before `container.remove`; the sidecar-shutdown path and `_enforce_ceiling` use the same
step; `sweep_orphaned_notebook_servers` unchanged; `backend/vault/` (Vault Writer) —
`write_experiment_files` unchanged, called after the confirmed save; `backend/api/experiments.py`
(REST API) — `POST /api/experiments/:id/notebook_server` unchanged in shape;
`frontend/src/experiments/LiveNotebookPanel.tsx` (Experiments Board) — the unmount cleanup no
longer stops the server; mount reattaches via
`GET /api/experiments/:id/notebook_server` before starting a new one; table: `experiments`
(`notebook_path`).

---

## Phase 6.8: The notebook gets the page — rail plus wide detail pane

### What this delivers
The Experiments Board becomes a ~240px rail of experiment titles grouped by the four statuses,
itself collapsible; selecting an experiment opens its live notebook in a wide detail pane filling
the rest of the content area. When the available width would drop the notebook below JupyterLab's
own 760px floor, the Companion pane auto-collapses (leaving its existing restore handle) rather
than letting the framed app break. The notebook is genuinely usable: full-width cells, readable
text, real editing.

### Depends on
Phase 6.7

### Touches
`frontend/src/experiments/ExperimentsBoard.tsx` (Experiments Board) — four-column kanban replaced
by rail + detail pane, `expandedId` becomes the selected experiment, `RunLogPanel` and
`ApprovalPrompt` move into the detail pane; `frontend/src/experiments/ExperimentsBoard.css`
(Experiments Board) — rail/detail grid; `frontend/src/experiments/LiveNotebookPanel.css`
(Experiments Board) — the 760px horizontal-scroll workaround becomes the last resort, not the
normal case; `frontend/src/app/AppShell.tsx` (App Shell) — width-driven Companion auto-collapse;
`frontend/src/state/usePaneWidth.ts`, `frontend/src/state/useCollapsible.ts` (Client State) —
programmatic collapse with a restorable prior width.

---

## Phase 6.9: Experiment status can actually be changed

### What this delivers
Moving an experiment planned → in-progress → done works from the UI: a segmented control in the
detail pane header shows all four statuses for the open experiment and PATCHes on click, and each
rail item carries a compact status dropdown for triage without opening anything. The rail regroups
immediately, and the status badge vocabulary (no `failed` status, never the danger family) is
unchanged.

### Depends on
Phase 6.8

### Touches
`frontend/src/experiments/ExperimentsBoard.tsx` (Experiments Board) — segmented control + rail
dropdown, both calling the existing
`PATCH /api/projects/:id/experiments/:experimentId`; `frontend/src/experiments/ExperimentsBoard.css`
(Experiments Board) — control styling from Design Tokens; `backend/api/experiments.py` (REST API) —
`update_experiment` unchanged (it already accepts `status`); `backend/experiments/` (Experiment
Record) — unchanged; table: `experiments` (`status`).

---

## Phase 6.10: The dashboard answers "what am I working on, and how far along am I"

### What this delivers
The project's landing view opens with what the user is actually doing: the project's `focus_seed`
plus the hypotheses of every in-progress experiment as "currently working on", a single segmented
progress meter of experiment completion (planned / remaining / in-progress / done as bands of one
bar), the pending experiments themselves, and the top relevant papers from the project's own
library ranked against that focus using the existing embedding/rerank machinery. "Continue where
you left off" and "Needs attention" are kept below; the four bare count tiles fold into the new
blocks.

### Depends on
Phase 6.9

### Touches
`backend/projects/` (Dashboard's data source) — `get_dashboard` extended; `DashboardSummary` in
`backend/projects/models.py` gains `focus`, `progress` (per-status experiment counts),
`pending_experiments`, `relevant_papers`; `backend/memory/` (Memory Index) — existing embedding +
`reranker.py` reused to rank library papers against the focus text, no new model;
`backend/api/projects.py` (REST API) — `GET /api/projects/:id/dashboard`;
`frontend/src/dashboard/Dashboard.tsx` and `Dashboard.css` (Dashboard) — new focus/progress/pending/
relevant-papers sections, resume rows and needs-attention retained; `packages/api-client/`
(Generated API Client) — regenerated; tables: `projects` (`focus_seed`, `corpus_centroid`),
`experiments` (`status`, `hypothesis`), `project_papers`, `paper_chunks`.

---

## Phase 6.11: Fix Round 1 sign-off

### What this delivers
Every symptom in `PLANNER/Things_to_finish.md` is verified gone in the running app, and the locked
architecture documents match what was built. Live acceptance, in one pass: searching
"attention is all you need" returns that paper first with 5 results and a working `Search more`;
an opened paper shows 5 clickable references, its code repo and its dataset sources; the graph
shows readable titles and real `cites` tracebacks; a notebook survives closing the experiment,
switching tabs and reopening, at full page width, with status changeable in both places; the
dashboard opens on the current focus, a progress meter, pending experiments and relevant papers.
`PLANNER/DECISIONS.md` carries the D21 and D26 amendments (invariants #1–#4 untouched), and
`PLANNER/Things_to_finish.md` is closed out.

### Depends on
Phase 6.10

### Touches
`PLANNER/DECISIONS.md` — D21/D26 amendments verified consistent; `PLANNER/Things_to_finish.md` —
closed; `PLANNER/Tracker.md` — Fix Round 1 entry in the post-approval verification log;
`backend/` and `frontend/` — the existing pytest and vitest suites pass, plus the targeted tests
added in Phase 6.1 (ranking), Phase 6.3 (reference resolution) and Phase 6.7 (save-then-stop).
