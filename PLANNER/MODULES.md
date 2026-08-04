# Modules — Research Companion OS (v1)

**Authority.** `DECISIONS.md` (D1–D37) fixes the repo layout (D10) and the module boundaries named in
this brief — `backend/harness/`, `backend/voice/` + `frontend/src/voice/`, `desktop/`'s zero-logic
rule, the tool catalog, the memory index, the shared quote anchor. This document renders those
boundaries as module blocks; it does not redesign them. `TRD.md`, `Schema.md` and `Rules.md` supply
every type, wire shape and naming convention referenced below.

**Vocabulary.** Build units are **Phase 1 … Phase 5** plus the cross-cutting **Voice** layer. Every
module states the phase it **first appears in**, so `ImplementationPlan.md` can stage the build. The
word "Slice" does not appear.

Modules are listed in dependency order — a module never depends on one defined below it.

---

## Database Layer

**Path:** `backend/db/`
**Responsibility:** Persist and query every Postgres-backed record through a pooled connection and two hand-written SQL primitives that ORM cannot express cleanly.
**Hides:** Connection pooling, Alembic migration application, the pgvector/tsvector fusion query, the recursive-CTE graph traversal.
**State:** Owns the async connection pool; created during Sidecar Bootstrap's lifespan, closed on shutdown; safe for concurrent callers via the pool.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `session` | `() -> AsyncSession` (context manager) | One unit of work; commits on clean exit, rolls back on exception. |
| `hybrid_retrieve` | `(scope: RetrievalScope, query_embedding: vector, query_tsquery: str, k: int) -> list[RetrievedChunk]` | The parameterised pgvector + tsvector fusion behind the memory union (D25). |
| `traverse_graph` | `(project_id: UUID, roots: list[NodeRef], depth: int) -> list[EdgeRow]` | Recursive-CTE traversal behind the project-scoped graph union (D26). |
| `run_migrations` | `() -> None` | Applies pending Alembic revisions once, at startup. |

**Depends on:** — (confines SQLAlchemy 2.x async, `asyncpg`, and Alembic)
**Errors:** A migration failure at startup is unrecoverable — the sidecar reports it and the `database` readiness capability never turns green. A query failure inside `session()` rolls back and re-raises; callers decide whether it is user-facing.
**Must not know:** Any domain module's business rules — table shapes and the two shared queries only, nothing about relevance, provenance, or tool semantics.
**Phase:** 1

---

## Vault Writer

**Path:** `backend/vault/`
**Responsibility:** Write every durable vault file and its Postgres index row as one atomic operation.
**Hides:** The vault folder layout, write-then-commit ordering, `library/` ↔ `projects/` symlinking.
**State:** Stateless per call; the vault root is read once from Settings Store at startup and held for the process lifetime.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `write_note` | `(project_id: UUID, note: NoteInput) -> Note` | Creates/updates `projects/<slug>/notes/*.md`; assigns the frontmatter id on create. |
| `write_highlight` | `(project_id: UUID, paper_id: UUID, highlight: HighlightInput) -> Highlight` | Writes the `papers/highlights/<canonical-id>.json` entry and the `highlights` + `quote_anchors` rows together (D4). |
| `write_paper_asset` | `(paper_id: UUID, kind: Literal["pdf","parsed"], content: bytes \| dict) -> Path` | Writes into `library/papers/<canonical-id>/`. |
| `write_experiment_files` | `(experiment_id: UUID, notebook: bytes, run?: RunArtifacts) -> None` | Writes `notebook.ipynb`, `requirements.txt`, and run logs/artifacts under `experiments/<exp>/`. |
| `write_document` | `(project_id: UUID, document: DocumentInput) -> Document` | Writes `manuscript/*.tex`. |

**Depends on:** Database Layer, Settings Store
**Errors:** The file is written before the index update, inside the same DB transaction, committed only after the file write succeeds. A failed index update rolls the DB row back and reports `VaultWriteFailed`; the file already on disk stays orphaned until the same key is written again — an accepted consequence of D4's no-reconciliation rule (see Known Compromises).
**Must not know:** What the content means — it writes bytes/rows to the shapes `Schema.md` defines, nothing about relevance, provenance, or extraction.
**Phase:** 1 (extended per artifact type: experiment files Phase 2, document Phase 4)

---

## Settings Store

**Path:** `backend/settings/`
**Responsibility:** Store and validate the single-row local configuration, including encrypted provider keys.
**Hides:** AES-256-GCM encryption, the OS-keyring integration, which providers need a key vs. a base URL only.
**State:** Owns the single `api_keys` row via Database Layer; the keyring master key is fetched per call, never cached beyond the call.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `get_settings` | `() -> ModelSettings` | Provider config with keys redacted to `…last4`. |
| `save_provider` | `(provider: str, credentials: ProviderCredentials) -> ModelSettings` | Encrypts and validates with a live test call before persisting. |
| `discover_models` | `(provider: Literal["ollama","vllm"], base_url: str) -> list[str]` | Queries a local endpoint for its available models — never a typed model string. |
| `get_vault_path` | `() -> Path` | The resolved vault root, read once at startup. |
| `set_voice_engine` | `(engine: str) -> None` | Persists the selected `backend/voice/` registry engine. |

**Depends on:** Database Layer
**Errors:** An invalid key or unreachable local endpoint returns a validation failure and is never persisted. A missing keyring master key at first use surfaces as the `llm` readiness capability failing, not a crash.
**Must not know:** Which module consumes a given provider config.
**Phase:** 1

---

## Provenance

**Path:** `backend/provenance/`
**Responsibility:** Confirm a claimed quote resolves in a paper's parsed text and produce the durable anchor object every other consumer cites.
**Hides:** The deterministic substring-match algorithm, whitespace/hyphenation/ligature normalisation, how docling offsets bridge to the PDF.js text layer.
**State:** Stateless.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `validate_and_anchor` | `(paper_id: UUID, quote: str, prefix: str, suffix: str) -> QuoteAnchor \| None` | D24's substring check plus D33's fuzzy locator in one call; `None` means the quote does not resolve. |
| `locate` | `(quote: str, text_stream: str) -> CharSpan \| None` | The bare fuzzy-match primitive, reused by the reader's PDF.js-side re-location. |

**Depends on:** Database Layer (persists `quote_anchors` rows)
**Errors:** A quote that cannot be located is not an exception — it is a legal `None` return. Every caller (Paper Pipeline, Agent Harness citations, Literature Matrix) is contractually required to drop the field to "not stated" or strip the citation and mark it `⚠ unverified`; the failure is defined out of existence as a return value, per Rules.md.
**Must not know:** Which field or citation consumes the anchor — no knowledge of `paper_cards`, `highlights`, or `matrix_cells`.
**Phase:** 1

---

## LLM Gateway

**Path:** `backend/llm/`
**Responsibility:** Route a completion or structured-extraction request to the configured primary or auxiliary model.
**Hides:** LiteLLM, per-provider request shaping, the prompted-structured-output fallback for models without native tool-calling.
**State:** Stateless; reads provider config from Settings Store per call.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `complete` | `(messages: list[Message], tools?: list[ToolSchema], tier: Literal["primary","auxiliary"]) -> AsyncIterator[LLMChunk]` | Streaming completion; auxiliary falls back to primary when unset (D11). |
| `complete_structured` | `(messages: list[Message], schema: type[BaseModel], tier: Literal["primary","auxiliary"]) -> BaseModel` | Structured extraction, used by Paper Pipeline and Literature Matrix custom columns. |

**Depends on:** Settings Store
**Errors:** A provider error retries per LiteLLM policy, then surfaces as a recoverable `error` event. Embeddings never route through this module — invariant #1 has no code path here that accepts the embedding model id.
**Must not know:** Tool semantics or the business meaning of what it is asked to complete.
**Phase:** 1

---

## Knowledge Graph

**Path:** `backend/graph/`
**Responsibility:** Maintain and serve the project-scoped union of metadata-derived and LLM-derived edges.
**Hides:** The recursive-CTE traversal, dup-tolerant concept-node slugging, which edges are solid vs. dashed.
**State:** Stateless; edge rows persist via Database Layer.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `write_metadata_edges` | `(paper_id: UUID, edges: list[MetadataEdge]) -> None` | Called from Paper Pipeline's enrichment pass; writes `provenance='metadata'` rows. |
| `write_llm_edges` | `(paper_id: UUID, edges: list[LLMEdge]) -> None` | Called from Paper Pipeline's extraction pass, opened papers only; writes `provenance='llm'` rows. |
| `get_graph` | `(project_id: UUID, types?: list[str], depth?: int) -> Graph` | The project-scoped union, via `Database Layer.traverse_graph`. |

**Depends on:** Database Layer
**Errors:** A traversal with no matching endpoints returns an empty `Graph`, not an error.
**Must not know:** Rendering rules (colour/shape/dash) — those belong to the frontend; it returns typed, provenance-tagged edges only.
**Phase:** 1 (written), 3 (surfaced)

---

## Paper Pipeline

**Path:** `backend/papers/`
**Responsibility:** Turn a paper reference into a fetched, parsed, extracted, validated global paper record.
**Hides:** The OA-source priority order, the docling invocation, the auxiliary-extraction prompt, canonical-id derivation.
**State:** Stateless; durable state is the `papers` / `paper_content` / `paper_cards` rows via Database Layer.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `resolve_canonical_id` | `(source_ids: SourceIds) -> str` | The one function that derives `canonical_id` (DOI → arXiv → OpenAlex/S2); never re-derived inline elsewhere. |
| `add_paper` | `(input: PaperInput) -> Paper` | Fetches (OA only) or accepts an upload, dedupes on canonical id, enqueues parse/embed/extract. |
| `get_paper` | `(paper_id: UUID, include: list[IncludeField]) -> Paper` | One parameterised read. |
| `parse_paper_job` | `(paper_id: UUID) -> None` | docling parse; writes `paper_content` and section boundaries. |
| `extract_card_job` | `(paper_id: UUID) -> None` | Auxiliary-tier extraction of the five standard fields, each validated through Provenance before a `paper_cards` row is written; also calls Knowledge Graph's `write_llm_edges`. |
| `enrich_paper_job` | `(paper_id: UUID) -> None` | Papers with Code / GitHub, on open only; calls Knowledge Graph's `write_metadata_edges`. |
| `reprocess_paper` | `(paper_id: UUID) -> Paper` | Re-drives whichever stage (`fetch`/`parse`/`embed`/`extract`) stalled or failed, re-enqueuing only the incomplete ones (Bug Fix Plan Phase 1.3). |

**Depends on:** Database Layer, Vault Writer, Provenance, LLM Gateway, Knowledge Graph
**Errors:** No OA copy and no upload leaves `pdf_path` NULL and `fetch_state = 'degraded'`; callers render abstract + source link, never a paywall attempt. A field failing Provenance validation is simply not written.
**Must not know:** Which project a paper belongs to (global, no `project_id`), or the reader UI.
**Phase:** 1

---

## Memory Index

**Path:** `backend/memory/`
**Responsibility:** Answer a retrieval query with cited rows drawn from the query-time union of a project's paper and project chunks.
**Hides:** The section-aware chunking boundary, hybrid dense+lexical fusion, reranking, that the union is a query rather than a table.
**State:** Stateless; chunk rows persist via Database Layer.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `chunk_and_embed_job` | `(source_type: Literal["paper_section","abstract","note","experiment","conversation_summary"], source_id) -> None` | Chunks and embeds one artifact with `gte-modernbert-base`. |
| `query_memory` | `(project_id: UUID, query: str, types?: list[str]) -> list[CitedRow]` | `paper_chunks(papers in P) ∪ project_chunks(P)`, hybrid-fused and reranked (D25). |

**Depends on:** Database Layer
**Errors:** No matching rows is a legal empty list, never an error.
**Must not know:** The meaning of what it retrieves — uniform treatment of chunk text regardless of source.
**Phase:** 1

---

## Search Federation

**Path:** `backend/search/`
**Responsibility:** Turn a natural-language query into one deduped, reranked, cached result set from the literature APIs.
**Hides:** The single LLM query-understanding pass, per-source parameter mapping, the parallel fan-out, which source is queried when.
**State:** Stateless; caches result sets in `result_store` via Database Layer.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `search_papers` | `(query: str, filters?: SearchFilters) -> ResultSet` | Fan-out to arXiv/OpenAlex/S2, cross-encoder rerank of the top ~100, cached under `result_id`. |
| `refine_results` | `(result_id: str, filters: SearchFilters) -> ResultSet` | Re-filters a cached set without re-querying sources. |

**Depends on:** Database Layer, LLM Gateway, Paper Pipeline (`resolve_canonical_id`)
**Errors:** One source timing out degrades the response with the hits that arrived and names the failed source; never a 500 for a partial failure.
**Must not know:** Which project issued the search.
**Phase:** 1

---

## Experiment Record

**Path:** `backend/experiments/`
**Responsibility:** Own the structured experiment record and the rule for which metric writes may claim `source: measured`.
**Hides:** That `source: llm` is structurally unrepresentable; the measured-gate rule is enforced by the DB `CHECK`, not re-implemented here.
**State:** Stateless; rows persist via Database Layer.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `create_experiment` / `update_experiment` | `(project_id: UUID, fields: ExperimentInput) -> Experiment` | The structured record — hypothesis, setup, status, notes. |
| `record_metric` | `(experiment_id: UUID, metric: MetricInput) -> ExperimentMetric` | `source` is `user` or `measured` only. |
| `record_run` | `(experiment_id: UUID, run: RunResult) -> ExperimentRun` | Persists the run's reproducibility fingerprint after Execution Sandbox completes it. |

**Depends on:** Database Layer
**Errors:** A `measured` write missing its `run_id` fails at the database `CHECK` and surfaces as a validation error; this module does not pre-validate what the constraint already guarantees.
**Must not know:** How a container runs, or Docker.
**Phase:** 2

---

## Execution Sandbox

**Path:** `backend/sandbox/`
**Responsibility:** Run notebook code inside an isolated Docker container only after an explicit human confirmation.
**Hides:** Which of the two execution paths is active (interactive `jupyter_client`/ZMQ kernel vs. the `nbclient` fallback), image build/layering, mount/limit enforcement, the confirmation-token scheme.
**State:** Owns the live kernel-container handle per open experiment; created when the experiment pane opens, torn down on close or idle timeout; one kernel per experiment.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `propose_cell` | `(experiment_id: UUID, code: str, index?: int) -> Notebook` | Writes a cell to the vault notebook; never executes. |
| `mint_confirmation` | `(experiment_id: UUID, spec: RunSpec) -> ConfirmationToken` | Issued only after the UI displays code + container spec to the human. |
| `run_all` | `(experiment_id: UUID, token: ConfirmationToken, network_optin?: bool, gpu?: bool) -> ExperimentRun` | The only path to a run; rejects an invalid or reused token. |
| `stop_kernel` | `(experiment_id: UUID) -> None` | Idempotent teardown. |

**Depends on:** Vault Writer, Experiment Record
**Errors:** `run_all` without a valid token is impossible by construction. A non-zero exit or an interactive/out-of-order run is recorded via Experiment Record but refused promotion to `measured` — that refusal is the correct outcome, not an error.
**Must not know:** An experiment's hypothesis or the meaning of its metrics.
**Phase:** 2 (kernel spike gates start; fallback is a swapped implementation behind this same interface)

---

## Literature Matrix

**Path:** `backend/matrix/`
**Responsibility:** Project existing extractive cards and experiment metrics into a persisted comparison grid.
**Hides:** That standard columns are a pure projection with no re-extraction; the per-`(paper, column)` custom-query cache key.
**State:** Stateless; matrix and cell rows persist via Database Layer.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `build_matrix` | `(project_id: UUID, name: str) -> Matrix` | Creates an empty matrix artifact. |
| `update_matrix` | `(matrix_id: UUID, selected_paper_ids?, selected_experiment_ids?, column_defs?) -> Matrix` | One `PUT`-shaped write; row order is part of the artifact. |
| `get_matrix_view` | `(matrix_id: UUID) -> MatrixView` | Standard columns via projection of Paper Pipeline's cards; custom columns from cache or a fresh scoped extraction. |
| `update_cell` | `(matrix_id: UUID, row_id: UUID, column_key: str, value: str) -> MatrixCell` | Sets `source: user`; never touches the extracted value. |

**Depends on:** Database Layer, Paper Pipeline, LLM Gateway, Provenance
**Errors:** A cell with no cached custom-column result and no override renders `not stated` by row absence, not an exception.
**Must not know:** Rendering (quote treatment vs. plain text) — driven by the `source` field it returns.
**Phase:** 3

---

## Manuscript

**Path:** `backend/writing/`
**Responsibility:** Own LaTeX document records and check their citations against the project's references.
**Hides:** The Tectonic-in-Docker escape-hatch invocation, the missing/unsupported citation detection rule.
**State:** Stateless; document rows persist via Database Layer; the `.tex` file is written through Vault Writer.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `save_document` | `(project_id: UUID, tex: str, engine?: Literal["swiftlatex","tectonic"]) -> Document` | Persists the source; `tectonic` triggers the Docker compile. |
| `check_citations` | `(document_id: UUID) -> list[CitationFinding]` | Missing/unsupported claim detection over the project's references. |
| `autocomplete_citations` | `(project_id: UUID, prefix: str) -> list[Reference]` | Backs `\cite` autocomplete. |
| `export_bibtex` | `(project_id: UUID) -> str` | BibTeX for the project's library. |

**Depends on:** Vault Writer, Database Layer, Memory Index
**Errors:** A Tectonic compile failure returns a `CompileResult` carrying the error panel content, never a 500; the previous `.tex` on disk is untouched.
**Must not know:** How to produce prose — no code path here writes `documents.body`.
**Phase:** 4

---

## Research Feed

**Path:** `backend/feed/`
**Responsibility:** Surface new papers matching a project's interest profile, each stating why it surfaced.
**Hides:** Synonym expansion, the category-driven fetch window, the deterministic scoring formula, the seen-set anti-join.
**State:** Stateless; profile and feed-item rows persist via Database Layer.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `poll_feed_job` | `(project_id: UUID) -> None` | Catch-up-on-launch: fetch since last poll, rank, dedupe, write `feed_items`. |
| `get_feed` | `(project_id: UUID) -> list[FeedItem]` | Items with `state='new'`, best score first. |
| `save_item` / `dismiss_item` | `(project_id: UUID, item_id: UUID) -> None` | `save` adds to the library and shifts `corpus_centroid`; `dismiss` writes to `seen_set`. |
| `get_interest_profile` / `update_interest_profile` | `(project_id: UUID, profile?: InterestProfile) -> InterestProfile` | Inspectable and user-editable. |

**Depends on:** Database Layer, Paper Pipeline, Memory Index
**Errors:** An item with no computable match reason is dropped before it reaches `feed_items` — never rendered without a reason, by construction.
**Must not know:** The reader, matrix, or graph.
**Phase:** 5

---

## Voice Engine

**Path:** `backend/voice/`
**Responsibility:** Convert between audio and text through whichever engine is currently configured.
**Hides:** Which STT/TTS engine is active, model weights and warm-up, that `faster-whisper` / Piper / `whisper.cpp` exist at all.
**State:** Owns the lazily-loaded engine instance; loaded on first call, guarded by a lock; cached for the process lifetime.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `transcribe` | `(audio_bytes: bytes, *, lang: str) -> Transcript` | Engine-agnostic STT. |
| `synthesize` | `(text: str, *, voice: str) -> bytes` | Engine-agnostic TTS. |

**Depends on:** Settings Store (reads `voice_engine`)
**Errors:** An engine load failure keeps the `voice` readiness capability at `failed`; individual call failures return a recoverable error, never a crash of the caller's turn.
**Must not know:** That it is called from a WebSocket turn or a REST endpoint — no awareness of the harness, sessions, or tabs. No module outside this package may import an STT/TTS library, name an engine, or know a model exists (D37).
**Phase:** Voice

---

## Agent Harness

**Path:** `backend/harness/`
**Responsibility:** Run one agent turn — assembling context, calling tools, streaming events — from a message to completion or interruption.
**Hides:** The control-loop iteration cap and graceful stop, context compaction and eviction order, the tool catalog's dispatch table, the unused MCP adapter, how a turn's `asyncio.Task` is cancelled.
**State:** Owns one in-flight `asyncio.Task` per session, created on `user_message`, cancelled on `interrupt`; only one turn per session may be in flight.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `run_turn` | `(session: SessionRef, message: str, ui_state: UIState) -> AsyncIterator[TurnEvent]` | The single entry point; emits `status` / `text_delta` / `tool_call` / `tool_result` / `ui_action` / `turn_complete` events (D18 node 5). |
| `interrupt` | `(session: SessionRef) -> None` | Cancels the in-flight task; partial results are retained, never rolled back. |

**Depends on:** LLM Gateway, Search Federation, Paper Pipeline, Memory Index, Execution Sandbox, Vault Writer, Knowledge Graph, Literature Matrix, Manuscript, Research Feed, Voice Engine, Database Layer
**Errors:** Hitting the iteration cap ends the turn with a final assistant message, never an exception. A tool error is caught at the dispatch boundary and reported via `tool_result` / `error`. Cancellation is caught in exactly this one place in the backend (Rules.md).
**Must not know:** Transport details (WebSocket framing, HTTP) — it produces and consumes typed events only. Nothing outside this package imports its internals (D18 node 7).
**Phase:** 1 (Phase-1 tools), extended per phase as tools are added (2, 3, 4, 5, Voice)

---

## Session Transport

**Path:** `backend/ws/`
**Responsibility:** Carry one Companion WebSocket session per project between the renderer and the agent harness.
**Hides:** The session registry keyed by project id, event serialization, the bearer-token check on upgrade.
**State:** Owns the live session map; one entry per open project, created on first connect, removed on disconnect; survives tab switches and center-pane navigation.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `handle_connect` | `(project_id: UUID, token: str) -> Session` | `401`s before a session exists if the token is missing or wrong. |
| `handle_message` | `(session: Session, event: UpstreamEvent) -> None` | Dispatches `user_message` / `ui_state` / `interrupt` to Agent Harness. |
| `broadcast` | `(session: Session, event: DownstreamEvent) -> None` | Pushes a harness-produced event to the connected client, if any. |

**Depends on:** Agent Harness
**Errors:** A message on a nonexistent or unauthenticated session is refused, never silently dropped. A dropped socket leaves the session live so a reconnect can rehydrate; the transcript is never lost because Agent Harness persists it via Database Layer independent of socket state.
**Must not know:** What a tool does or how context is assembled — a pure event pipe.
**Phase:** 1

---

## Job Queue

**Path:** `backend/jobs/`
**Responsibility:** Run cancellable background work — fetch, parse, embed, extract, poll, container runs — off the request path.
**Hides:** The Postgres-backed queue library's enqueue/dequeue mechanics, the catch-up-on-launch cursor check.
**State:** Owns the worker loop and the `scheduled_jobs` cursor table; the worker starts during Sidecar Bootstrap and drains on shutdown.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `enqueue` | `(job_kind: str, payload: JobPayload) -> JobHandle` | Enqueued in the same DB transaction as the row it concerns. |
| `run_catchup_pass` | `() -> None` | Runs any `scheduled_jobs` overdue since `last_run_at`, once, at startup. |
| `cancel` | `(job_handle: JobHandle) -> None` | Cancellable long-running jobs stream progress over Session Transport. |

**Depends on:** Database Layer, Paper Pipeline, Memory Index, Execution Sandbox, Research Feed
**Errors:** A failed job is recorded with its failure reason and does not retry silently in a loop; it surfaces via the same progress channel used for success.
**Must not know:** The business meaning of a payload — it dispatches to the named handler and streams progress.
**Phase:** 1

---

## REST API

**Path:** `backend/api/`
**Responsibility:** Expose every REST resource as a validated, token-checked route that delegates to exactly one domain module.
**Hides:** Routing table structure, request/response Pydantic-to-OpenAPI generation, the bearer-token dependency.
**State:** Stateless.

**Public interface**

The full route table mirrors `TRD.md` §4.2, one row per REST resource, each a thin delegate:

| Symbol (representative) | Signature | Purpose |
|---|---|---|
| `GET /api/health` | `() -> {capabilities}` | Backs Sidecar Bootstrap's `get_readiness`. |
| `POST /api/projects/:id/papers` | `(PaperInput) -> Paper` | Delegates to Paper Pipeline's `add_paper`. |
| `POST /api/search` | `(query, filters?) -> ResultSet` | Delegates to Search Federation's `search_papers`. |
| `POST /api/projects/:id/memory/query` | `(query, types?) -> {rows}` | Delegates to Memory Index's `query_memory`. |
| `POST /api/experiments/:id/run_all` | `(confirmation_token, network_optin?, gpu?) -> {run_id}` | Delegates to Execution Sandbox's `run_all`. |
| — every other route in `TRD.md` §4.2 — | — | One route, one domain-module call, no SQL or LLM call in the handler (Rules.md). |

**Depends on:** Knowledge Graph, Paper Pipeline, Search Federation, Memory Index, Literature Matrix, Manuscript, Research Feed, Execution Sandbox, Experiment Record, Vault Writer, Settings Store, Voice Engine
**Errors:** Every unhandled exception below a route is caught by one FastAPI exception handler producing `{code, message, recoverable, what_still_worked}`; routers never construct it by hand.
**Must not know:** How any domain module does its work — only each module's public signatures.
**Phase:** 1, extended per phase

---

## Sidecar Bootstrap

**Path:** `backend/main.py`
**Responsibility:** Bring the sidecar from process start to per-capability readiness and back down cleanly on shutdown.
**Hides:** The launch ordering (vault check → compose up → migrations → job worker → catch-up pass), the readiness capability map, the shutdown sequence.
**State:** Owns the FastAPI app's lifespan; runs exactly once per process launch.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `create_app` | `() -> FastAPI` | The app factory; wires REST API and Session Transport onto one ASGI app, binds `127.0.0.1:0`. |
| `get_readiness` | `() -> dict[Capability, ReadinessState]` | Backs `GET /api/health`; never blocks first paint. |

**Depends on:** Database Layer, Vault Writer, Job Queue, Settings Store, REST API, Session Transport
**Errors:** An unwritable vault path fails the `vault` capability and every dependent capability stays `pending`/`failed`; the process itself does not crash and the window still paints.
**Must not know:** Any domain logic — it sequences other modules' own startup functions.
**Phase:** 1

---

## Generated API Client

**Path:** `packages/api-client/`
**Responsibility:** Give the frontend compile-time types and request functions for every REST route, regenerated from the backend's own schema.
**Hides:** Nothing beyond the OpenAPI-to-TypeScript codegen tool and its config — deliberately shallow, so a backend rename becomes a frontend compile error rather than a runtime `undefined` (D10).
**State:** Stateless; a regenerated build artifact, never hand-edited.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| (generated, one per route) | e.g. `getPaper(paperId): Promise<Paper>` | Every frontend caller imports these instead of declaring a matching interface (Rules.md: "never hand-declare a type the client already exports"). |

**Depends on:** REST API (schema source, not a runtime dependency)
**Errors:** A schema mismatch fails the generation step, not a runtime request.
**Must not know:** N/A — see Hides.
**Phase:** 1

---

## Desktop Shell

**Path:** `desktop/`
**Responsibility:** Spawn and supervise the sidecar process and own the native window, with no business logic.
**Hides:** Child-process spawn/kill mechanics, per-launch token generation, the systemd-attach escape hatch, native dialog wiring.
**State:** Owns the sidecar child-process handle and the `BrowserWindow`; created at Electron app-ready, torn down on `before-quit`.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `main` (Electron entry) | spawns sidecar, generates the per-launch token, shows the window, exposes `{port, token}` via preload | The only crossing of the IPC boundary. |
| `openFileDialog` / `openFolderDialog` | `(options) -> Path \| null` | Native dialog proxies, e.g. the vault-folder picker at onboarding. |

**Depends on:** — (treats the sidecar as an opaque external process)
**Errors:** If the sidecar fails to bind a port within a timeout, the window shows a startup-failure state rather than hanging.
**Must not know:** Anything about projects, papers, or the agent — zero business logic; any new import beyond spawn/dialog/window is a review stop (D2/D10's named failure mode).
**Phase:** 1

---

## Design Tokens

**Path:** `frontend/src/design/`
**Responsibility:** Provide the single set of CSS custom properties and enum-to-label maps every component renders against.
**Hides:** The literal colour/spacing/type values from `UI_DESIGN.md` §1, and the render-time mapping from wire enum values to UI copy.
**State:** Stateless; static exports.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `tokens` | CSS custom properties | Consumed via `var(--accent)` etc. across every component. |
| `relevanceLabel` / `experimentStatusLabel` / `metricSourceLabel` | `(value: WireEnum) -> string` | The one place a wire value becomes display copy — e.g. `unset` → "unmarked" — never done inline (Rules.md). |

**Depends on:** —
**Errors:** An unmapped enum value is a build-time type error, not a runtime fallback — the label maps are exhaustive over the generated client's `Literal` types.
**Must not know:** Any component's layout.
**Phase:** 1

---

## Client State

**Path:** `frontend/src/state/`
**Responsibility:** Hold every piece of client-side state — server cache, UI state, and the WebSocket event bus — in one place the rest of the frontend reads from.
**Hides:** React Query's cache-invalidation wiring to `tool_result` events, the Zustand store shape, WebSocket reconnect/backoff.
**State:** Owns the React Query cache and the Zustand `ui_state` store for the renderer's lifetime; the Zustand store **is** the `ui_state` payload sent on every `user_message`.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `useCompanionSocket` | `() -> { send, events, connectionState }` | The one hook wrapping the WebSocket session. |
| `useUIState` | `() -> UIState` (Zustand selector) | Read/write the active tab, selection, working set. |
| `queryClient` | `QueryClient` | Invalidated by incoming `tool_result` events. |

**Depends on:** Generated API Client
**Errors:** A dropped socket sets `connectionState = "reconnecting"` and queues outgoing messages; the composer reads this to tell the user whether a message will send.
**Must not know:** What any specific view does with the state it holds.
**Phase:** 1

---

## Voice Capture

**Path:** `frontend/src/voice/`
**Responsibility:** Capture push-to-talk audio and play back synthesized speech.
**Hides:** `getUserMedia`, the push-to-talk key-state machine, audio-element playback.
**State:** Owns the active `MediaStream` while the talk key is held; opened on key-down, closed on key-up, never open otherwise.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `useVoice` | `() -> { startCapture, stopCapture, isRecording, playAudio }` | The one hook; no other component touches `getUserMedia` or an audio element (D37). |

**Depends on:** Client State, Generated API Client
**Errors:** A denied microphone permission disables push-to-talk with an explanatory tooltip, never a silent no-op.
**Must not know:** Which engine the backend uses — it only calls the two voice REST endpoints.
**Phase:** Voice

---

## App Shell

**Path:** `frontend/src/app/`
**Responsibility:** Render the top bar, left nav, and the center-pane tab stack that every screen sits inside.
**Hides:** Tab-stack persistence and restoration, how `ui_action` events push/activate tabs.
**State:** Owns the React Router tab stack; rehydrated from `GET /api/projects/:id` on launch, persisted via `PUT /api/settings/tabs` on every change.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `AppShell` | React component | Nav + tab strip + routed center pane + Companion Pane; the composition root for every screen. |
| `useTabStack` | `() -> { openTabs, activeTab, openTab, closeTab, activateTab }` | The single place tab mutations happen. |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** A tab referencing a paper that no longer exists renders that tab's content as an error card, not a crash of the stack.
**Must not know:** What any individual view renders inside a tab.
**Phase:** 1

---

## Companion Pane

**Path:** `frontend/src/companion/`
**Responsibility:** Render the persistent Companion transcript and composer, and let the user interrupt a running turn.
**Hides:** How the transcript's five kinds (user, reasoning, cited evidence, tool chip, tool result) are visually and semantically distinguished.
**State:** Stateless — reads the transcript and turn status from Client State.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `CompanionPane` | React component | Transcript + composer + `✕ Stop`; present on every screen, never a modal. |

**Depends on:** Client State, Design Tokens, Voice Capture
**Errors:** A cited span marked `⚠ unverified` by the backend renders as such and is announced non-visually, never silently dropped.
**Must not know:** How to call a tool or assemble context — it sends `user_message` / `interrupt` and renders what comes back.
**Phase:** 1

---

## Reader

**Path:** `frontend/src/reader/`
**Responsibility:** Render a paper's real PDF pages alongside its structure sidebar and extractive card, keeping all anchor views in sync.
**Hides:** PDF.js page/text-layer rendering, the click-through sync between a card field, a PDF span, and a companion citation.
**State:** Owns per-tab scroll/selection position for each open paper; one instance per open reader tab, independent of the others.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `ReaderTab` | React component, one per open paper | Structure sidebar, PDF.js canvas, collapsible extractive card, selection popover. |
| `useAnchorSync` | `() -> { activeAnchor, focusAnchor }` | Drives "click any one of card field / PDF span / citation lights up the others" from `ui_action` events; exactly one active span at a time. |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** A field with no validated anchor renders `not stated in this paper` in the dashed treatment; a paper with no OA copy renders the degraded state instead of reader chrome.
**Must not know:** How the anchor was validated.
**Phase:** 1

---

## Library View

**Path:** `frontend/src/library/`
**Responsibility:** Render the papers library list with relevance controls and per-paper processing-state badges.
**Hides:** Nothing beyond layout.
**State:** Stateless.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `LibraryView` | React component | Paper list + four-value relevance segmented control + processing badges; "still extracting" visually distinct from "not stated". |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** Handled by the shared error card; no module-specific error path.
**Must not know:** How relevance or processing state is computed — it round-trips the enum and state columns.
**Phase:** 1

---

## Notes Editor

**Path:** `frontend/src/notes/`
**Responsibility:** Let the user write and edit project markdown notes.
**Hides:** Nothing beyond the markdown editor widget — notes are plain files and the editor is a thin wrapper.
**State:** Stateless; delegates persistence to Generated API Client on every save.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `NotesView` | React component | Note list + markdown editor; renders `Unlinked` as a first-class dashed state, never a blank. |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** A save failure keeps the unsaved edit in the editor and shows the error card; never silently discards user text.
**Must not know:** How a note is chunked or embedded.
**Phase:** 1

---

## Manuscript Editor

**Path:** `frontend/src/writing/`
**Responsibility:** Provide a LaTeX editor with live math and a debounced compiled preview.
**Hides:** CodeMirror 6 + KaTeX wiring, the SwiftLaTeX WASM worker, the Tectonic-vs-SwiftLaTeX engine choice at the UI layer.
**State:** Owns the debounce timer that triggers a preview recompile; one instance per open document tab.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `ManuscriptTab` | React component | Editor + live preview + compile-error panel; `\cite` autocomplete from Manuscript's `autocomplete_citations`. |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** A SwiftLaTeX compile failure renders the compile-error panel without discarding editor content; an unsupported claim renders in the dashed treatment.
**Must not know:** How citation checking determines "unsupported" — it only renders the findings it is given.
**Phase:** 4

---

## Experiments Board

**Path:** `frontend/src/experiments/`
**Responsibility:** Render the experiment board, its detail sheet, and the run-approval prompt.
**Hides:** The four-value status rendering rule (no "failed"/danger status), how agent-written cells are marked unrun-and-pending.
**State:** Owns the currently-open experiment detail sheet; one at a time.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `ExperimentsBoard` | React component | Board + detail sheet + notebook cell list, keyed by the four-value `status` enum. |
| `ApprovalPrompt` | React component | Shows code + container spec and requests confirmation; the only UI path that can produce a `confirmation_token`. |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** A run in flight streams logs from the WebSocket progress channel; a non-zero exit renders the outcome without ever using danger styling as a status value.
**Must not know:** How the sandbox enforces mounts or limits — it displays the spec it is handed.
**Phase:** 2

---

## Matrix View

**Path:** `frontend/src/matrix/`
**Responsibility:** Render the literature matrix grid and let the user edit a cell without corrupting extracted values.
**Hides:** Nothing beyond render-layer distinction between an extracted cell's quote treatment and a user cell's plain body type — the `source` field itself comes from the backend.
**State:** Stateless; the matrix artifact is fetched and written whole via Generated API Client.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `MatrixView` | React component | Grid of paper/experiment rows × standard/custom/user columns; reachable from the left nav under `DISCOVER`. |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** A cell absent after a completed custom-column query renders `not stated`, never a stuck spinner.
**Must not know:** How a custom column's extractive query runs.
**Phase:** 3

---

## Graph View

**Path:** `frontend/src/graph/`
**Responsibility:** Render the project-scoped knowledge graph with node type and edge provenance visually encoded.
**Hides:** The force-graph library choice (Cytoscape.js or react-force-graph — D26 leaves this open), layout algorithm details.
**State:** Owns the canvas's current pan/zoom/filter selection; view-local, not persisted.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `GraphView` | React component | Canvas + legend + filter chips; node type by colour and shape, edge provenance by dash, never colour alone. |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** An empty graph renders the dashed empty state, not a blank canvas.
**Must not know:** How an edge was derived beyond the `provenance` field it is given.
**Phase:** 3

---

## Feed View

**Path:** `frontend/src/feed/`
**Responsibility:** Render surfaced feed items with their match reason and let the user save or dismiss them.
**Hides:** Nothing beyond card layout — an item with no `why_relevant` never reaches this module.
**State:** Stateless.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `FeedView` | React component | Feed card list + save/dismiss actions; every card states why it surfaced. |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** A save/dismiss failure leaves the item in its prior state and shows the error card.
**Must not know:** The ranking formula.
**Phase:** 5

---

## Dashboard

**Path:** `frontend/src/dashboard/`
**Responsibility:** Summarise a project's state — actionable counts, resume points, and items needing attention.
**Hides:** Nothing beyond layout — all figures come from existing REST reads.
**State:** Stateless.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `Dashboard` | React component | Stat tiles + "continue where you left off" + "needs attention"; tile qualifiers are always the actionable subset. |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** A missing resume position omits that tile's action, never a broken link.
**Must not know:** How any figure it displays was computed.
**Phase:** 1

---

## Search Results

**Path:** `frontend/src/search/`
**Responsibility:** Render federated search results as they stream in, per source.
**Hides:** Nothing beyond layout — per-source progress and skeleton placement.
**State:** Stateless.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `SearchResults` | React component | Query box + per-source progress + result cards + skeletons; never a single blocking spinner. |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** A failed source renders its slot with the "what still worked" framing, never blanking the page.
**Must not know:** How dedup or reranking works.
**Phase:** 1

---

## Onboarding Wizard

**Path:** `frontend/src/onboarding/`
**Responsibility:** Walk the user through the four gated setup steps before any project exists.
**Hides:** Nothing beyond step sequencing and the recovery commands shown on a failed Docker check.
**State:** Owns the current step index; cannot be skipped or reordered; unmounts once `onboarding_completed_at` is set.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `OnboardingWizard` | React component | Four required steps; the local-provider path never demands an API key, discovered models are listed not typed. |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** An invalid key or unreachable Docker daemon shows the error card with a retry, never a dead end.
**Must not know:** What happens after onboarding beyond routing to the new project.
**Phase:** 1

---

## Settings Panel

**Path:** `frontend/src/settings/`
**Responsibility:** Let the user view and edit provider keys, models, and the voice engine selection.
**Hides:** Nothing beyond form layout — `…last4` redaction is enforced by the backend, not re-implemented here.
**State:** Stateless.

**Public interface**

| Symbol | Signature | Purpose |
|---|---|---|
| `SettingsPanel` | React component | Provider keys + primary/auxiliary model + voice engine; local providers never show a key field. |

**Depends on:** Client State, Generated API Client, Design Tokens
**Errors:** A failed save keeps the form populated (except the key itself) and shows the error card.
**Must not know:** How a key is encrypted.
**Phase:** 1

---

## Divergence from PRD §12

- **`docker/` and `tests/` are not modules.** `docker/` holds compose files and Dockerfiles consumed by Execution Sandbox, Database Layer, and Manuscript's Tectonic path; it has no callable interface. `tests/` holds the four pytest suites (D24/D25/D33/D29), which exercise Provenance, Paper Pipeline's dedup, and Execution Sandbox/Experiment Record's measured gate — it is a test location, not a code boundary.
- **`backend/harness/`'s internal files (`loop.py`, `context.py`, `tools/`, `result_store.py`, `mcp/`) are not separate modules.** They are Agent Harness's own implementation, invisible outside its public entry point, per D18 node 7's "self-contained, extractable package."
- **`frontend/src/notes/  writing/` is two modules, not one.** Notes Editor (Phase 1, plain markdown) and Manuscript Editor (Phase 4, CodeMirror 6 + KaTeX + SwiftLaTeX) are different phases and different technology; the PRD bullet compressed them onto one line for brevity.
- **`frontend/src/library/` is an addition.** PRD §6 draws a "Papers library" screen that PRD §12's candidate list omitted a folder for; it is added here as its own module rather than folded into Dashboard, since library browsing and project-summary are different responsibilities.
- **Backend `experiments/` and `sandbox/` are rendered as two modules — Experiment Record and Execution Sandbox — exactly as PRD §12 already listed them separately;** not a divergence, called out here only because their names differ slightly (`Experiment Record` vs. the PRD's bare `experiments/`) for clarity against the frontend's `Experiments Board`.

## Known Compromises

- **Vault Writer, error behaviour (gate condition 6).** D4's own ordering — write the file, then update the index inside the same DB transaction, commit only after the file write succeeds — means a failed index update after a successful file write leaves an orphaned file on disk with no index row. There is no reconciliation pass (Appendix A retires file watching/hash-diffing/reconciliation outright), so the orphan is resolved only the next time the same key is written, or never, for a key that is never retried. This is accepted because D3 already treats the vault as rebuildable-costs-time for derived data, and because adding a reconciliation pass is explicitly the retired design this project rejected — the alternative is worse than the compromise.
- **Generated API Client, depth (gate condition 9 in spirit).** Its interface is essentially as large as its implementation — it is, by design, a pass-through of backend types with no hidden decision. This is the intended shape mandated by D10 (a backend rename must be a frontend compile error), not a design failure; it is recorded here because it would otherwise read as a shallow-module defect.
