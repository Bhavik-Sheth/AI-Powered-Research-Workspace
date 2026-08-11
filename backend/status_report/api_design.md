# REST API — Design & Architecture

`backend/api/` is the FastAPI route layer: 19 routers, 20 files (18 resource
files + `deps.py` + `errors.py`), every one of them registered in
`main.py` and gated by a single bearer-token dependency. Routes are thin —
almost none contain SQL, an LLM call, or business logic; each opens a
`db.session()`, delegates to the domain module of the same name (`papers`,
`notes`, `experiments`, `search`, `matrix`, `graph`, `feed`, `writing`,
`memory`, `voice`, `settings`, `vault`, `provenance`, `sandbox`, `jobs`),
and turns the result into a Pydantic response model. A generated TypeScript
SDK (`packages/api-client`, built from FastAPI's OpenAPI schema) is the
frontend's only supported way to reach these routes; a handful of binary
endpoints (PDF bytes, voice audio) bypass the generated client and go
through hand-written `fetch` helpers in `frontend/src/state/bridge.ts`
instead, but still hit the same routes.

---

## Storage / data model

Routers in `backend/api/` own **no tables**. Storage is always the
delegate module's (`db.models.Project`, `papers.models`, `experiments.models`,
etc.) — routers only define the request/response envelope Pydantic models
that wrap those domain models for HTTP:

- `errors.py:24` — `ErrorEnvelope` (`code`, `message`, `recoverable`,
  `what_still_worked`), the one error shape every REST error carries.
- `health.py:18` — `HealthResponse` wrapping `app.state.readiness`
  (a dict set by `main.py`'s lifespan, not persisted anywhere).
- `projects.py:51-91` — `TabRef`, `ProjectResponse` (adds `tab_stack`/
  `active_tab` projection over `db.models.Project`), `CreateProjectRequest`,
  `SaveTabStackRequest`.
- `experiments.py:55-169` — `RunSpecPreview`, `ProposeCellRequest`,
  `KernelActionRequest`, `RunAllRequest`, `RunAllResponse` — route-local
  wrappers around `sandbox.models` / `experiments.models` types.
- `papers.py:22-128` — `PaperDetail`, `LibraryEntry`,
  `PatchProjectPaperRequest`, `ProjectPaperResponse` — the last one because
  a project-paper membership row has no standalone model of its own.
- `notes.py`, `highlights.py` — no local models; pass `vault.models.Note` /
  `Highlight` straight through.
- `memory.py:17-23` — `MemoryQueryRequest`, `MemoryQueryResponse` wrapping
  `memory.models.CitedRow`.
- `feed.py:42` — `FeedActionRequest` (`action: "save"|"dismiss"`).
- `search.py:15-32` — `SearchRequest` (owns the pagination fields: `limit`,
  `offset`, `page`, `result_id` — `search.search_papers`'s own signature is
  untouched, so paging/slicing happens here, at the API boundary).
- `settings.py:23-79` — `SaveProviderRequest`, `SaveModelBudgetRequest`,
  `DiscoverModelsRequest/Response`, `VaultPathRequest`.
- `voice.py:16` — `SynthesizeRequest`.
- `writing.py:23-31` — `SaveDocumentRequest`, `CheckCitationsResponse`.
- `conversations.py:18-29` — `MessageOut`, `ConversationResponse`.
- `runs.py:25-31` — `RunDetail` (adds the run's stdout log text, read off
  disk at request time, to `experiments.models.ExperimentRun`).

---

## Core mechanics — the route surface

**Auth/errors (cross-cutting, `deps.py`, `errors.py`)**
Every router except `ws_router` is mounted in `main.py:168-184` with
`dependencies=[Depends(require_bearer_token)]`; `deps.py:12` 401s any
request whose `Authorization` header doesn't equal `Bearer {config.bearer_token}`
before the route body ever runs. Three exception handlers
(`errors.py:34,43,48`) normalize every error into `ErrorEnvelope` — routes
raise `HTTPException` for expected failures (typically 404/422/409/503) and
let anything else propagate to the catch-all 500 handler, which manually
sets `Access-Control-Allow-Origin` because it runs outside the CORS
middleware layer.

**health.py** — `GET /api/health` reads `request.app.state.readiness`,
populated once at startup by `main.py`'s lifespan; the route itself does
nothing but project it.

**projects.py** — `GET/POST /api/projects`, `GET /api/projects/:id`,
`PUT /api/projects/:id/tab-stack` delegate straight to `projects` module.
`GET /api/projects/:id/dashboard` (`projects.py:117`) is the one route
with real logic in it: it fan-reads `projects`, `notes`, `experiments`,
`feed` inside one session, then in Python computes stall detection
(`_stalled`, `projects.py:41`, mirroring frontend `LibraryView.needsRetry`
minus the `failed` case), needs-attention items, and a "relevant to your
focus" ranking that calls `search.reranker.rerank` against the project's
focus text + open hypotheses — degrading to an empty list on
`TimeoutError` rather than failing the whole dashboard.

**papers.py** — `POST /api/projects/:id/papers` (add + link to project),
`GET /api/projects/:id/papers` (library list with per-project relevance),
`GET /api/papers/:id` (parameterized `include=card,sections,references,datasets,code`;
passes `heal=True` to `papers.get_paper`, the only read path that can
trigger `trace_references_job`), `GET /api/papers/:id/pdf` (streams via
`FileResponse`), `POST /api/projects/:id/papers/:id/reprocess` (re-enqueues
incomplete pipeline stages — the Library "Retry" action), `POST
.../promote` (reuses `reprocess_paper` to turn a reference stub into a
full paper, then links it to the project), `PATCH /api/projects/:id/papers/:id`
(sets relevance/why_relevant).

**notes.py** — `GET/POST/PATCH /api/projects/:id/notes`, all delegating to
`vault.write_note`/`notes.list_notes`. `POST` rejects a body carrying
`frontmatter_id` (422 — must go through PATCH instead); after a successful
create it enqueues `jobs.enqueue("chunk_and_embed_job", source_type="note", ...)`
— the only place in `api/` that triggers memory indexing directly.

**highlights.py** — `POST /api/projects/:id/highlights`: validates+anchors
the quote via `provenance.validate_and_anchor` (422 if it doesn't resolve
in the parsed paper text), then writes through `vault.write_highlight`
(404 on bad paper id, 500 on `vault.VaultWriteFailed`).

**memory.py** — `POST /api/projects/:id/memory/query` calls
`memory.query_memory` directly and wraps the `CitedRow[]` result. Per
`memory_design.md`, this is *not* the live retrieval path used during a
turn — `harness._maybe_retrieve` calls `query_memory` in-process — so this
route is a standalone, user-triggerable query surface.

**conversations.py** — `GET /api/projects/:id/conversation` — read-only
transcript dump via `conversations.list_messages`.

**experiments.py** — `GET/POST /api/projects/:id/experiments`,
`PATCH /api/projects/:id/experiments/:id` (CRUD, delegating to
`experiments` module). The approval-gate group: `GET
/api/experiments/:id/run_spec` (no side effect — preview only), `POST
.../cells` (writes an unrun pending cell via `sandbox.propose_cell`,
never executes it — added post-sign-off per its own docstring, "had no
way to reach a real cell from the UI"), `POST .../confirmation` (mints a
one-shot token, always recomputing the spec server-side), `POST .../kernel`
(`"start"` is a no-op status read since there's no persistent kernel per
D30; `"stop"` really cancels the run container), `GET/POST
.../notebook_server` (live Jupyter container status/start/stop; a 409 if
a run is already in flight, a 503 if a forced-save-on-stop can't be
confirmed), `POST .../run_all` (dispatches `run_experiment_job` via
`jobs.enqueue`, mints `run_id` up front so the client can correlate
WS events before any job output exists; repeats the notebook-server
mutual-exclusion check synchronously so the 409 surfaces immediately
instead of after the job silently produces nothing).

**runs.py** — `GET /api/runs/:id` reads the run row plus its stdout log
off disk (`get_vault_path() / run.stdout_ref`) as plain text — the
docstring flags this as the seam to swap for streaming if logs grow.
`POST /api/experiments/:id/metrics` records a hand-typed (`source: user`)
metric via `experiments.record_metric`.

**search.py** — `POST /api/search`: owns pagination on top of
`search.search_papers`. `page > 0` requires `result_id` (400 otherwise)
and means "widen" — `search_papers` itself fetches a deeper pool and
appends to the cached `result_id` entry; this route then slices to
`[offset:offset+limit]` and computes `has_more`/`pool_size`. `GET
/api/results/:id` re-slices a previously cached result set via
`search.refine_results` (404 if expired/missing).

**matrix.py** — `GET/POST /api/projects/:id/matrix`,
`GET/PUT /api/projects/:id/matrix/:id`,
`PATCH /api/projects/:id/matrix/:id/cells` — all thin delegation to
`matrix` module; `POST` optionally chains a `build_matrix` + `update_matrix`
call in one request when the create body already carries selection/column
data.

**graph.py** — `GET /api/projects/:id/graph?types=a,b` — parses the CSV
`types` query param into a list (or `None`), delegates to `graph.get_graph`.

**writing.py** — `GET/POST /api/projects/:id/documents`,
`GET/PUT .../documents/:id`, `GET .../documents/:id/pdf` (streamed file),
`POST .../documents/:id/assets` (multipart upload via `UploadFile`),
`POST .../documents/:id/check_citations`, `GET /api/projects/:id/references?prefix=`
(autocomplete), `GET /api/projects/:id/bibtex` (plaintext export). Create/
update both can return `Document | CompileResult` from the same status
code — `writing.save_document` decides which.

**feed.py** — `GET/PUT /api/projects/:id/interest-profile`,
`GET /api/projects/:id/feed`, `POST /api/projects/:id/feed`
(`action: "save"|"dismiss"`, returns the refreshed feed list).

**settings.py** — `GET/PUT /api/settings/models`,
`PUT /api/settings/models/budget` (rejects `budget <= 0` with 422; the
same map the LLM Gateway's rate-limit self-heal writes to automatically,
per its own comment), `POST /api/settings/models/discover` (probes an
Ollama/vLLM base URL, 502 on `httpx.HTTPError`), `PUT
/api/settings/vault-path` (onboarding step 2 — takes effect next launch),
`PUT /api/settings/onboarding-complete`.

**voice.py** — `POST /api/voice/transcribe` (reads the raw request body as
audio bytes, no size/format validation visible in this file — delegated
entirely to `voice.transcribe`), `POST /api/voice/synthesize` (returns raw
`audio/wav` bytes).

**anchors.py** — `GET /api/anchors/:id` — resolves a quote anchor via
`provenance.get_anchor`, 404 if missing. Pure glue, per its own docstring.

---

## Callers & dependents

The frontend never calls these routes with raw strings/`fetch` except for
four binary endpoints; everything else goes through the generated SDK in
`packages/api-client` (built from the FastAPI OpenAPI schema, confirmed via
`packages/api-client/openapi.json` and `sdk.gen.ts`), configured once in
`frontend/src/state/bridge.ts:33` (`configureApiClient`, sets base URL +
bearer header from the Electron-bridge `{port, token}`, dev fallback via
`VITE_DEV_PORT`/`VITE_DEV_TOKEN`).

**Live, called via generated SDK** — confirmed present in
`frontend/src/**` for: `projects` list/create/get/dashboard/tab-stack,
`papers` add/list/get/reprocess/promote/patch (`ReaderTab.tsx`,
`LibraryView.tsx`), `notes` list/create/update, `highlights` create,
`search` post/get-result (`SearchResults.tsx`), `matrix` full CRUD,
`graph` get, `writing` list/create/update documents + assets +
check_citations + references + bibtex (`useManuscriptPreview.ts`), `feed`
interest-profile + list + act, `settings` full group
(`ReadinessStrip.tsx`, onboarding views), `conversations` get, `experiments`
list/create/update/run_spec (`ExperimentsBoard.tsx`), `experiments`
confirmation + run_all (`ApprovalPrompt.tsx`), `experiments`
notebook_server get/post (`LiveNotebookPanel.tsx`).

**Live but reached via hand-written `fetch`, not the generated SDK** (still
wired, per `bridge.ts`'s `fetchBinary`/`postBinaryForJson`/`postJsonForBinary`):
- `GET /api/papers/:id/pdf` — `ReaderTab.tsx:202`.
- `GET /api/projects/:id/documents/:id/pdf` — `useManuscriptPreview.ts:88`.
- `POST /api/voice/transcribe` — `useVoice.ts:61`.
- `POST /api/voice/synthesize` — `useVoice.ts:72`.
These are unreachable through the typed client for binary bodies, so
`bridge.ts` carries its own auth header by hand for them.

**Defined but not called anywhere in `frontend/src`** (grepped both the
generated SDK function name and the raw path; none appear):
- `POST /api/experiments/:id/kernel` (`kernelActionApiExperimentsExperimentIdKernelPost`)
  — start/stop, unused; the UI never issues an explicit kernel action.
- `POST /api/experiments/:id/cells` (`proposeCellApiExperimentsExperimentIdCellsPost`)
  — matches its own docstring's admission that it was "only ever called
  directly in-process during manual verification" when added; still true.
- `GET /api/runs/:id` (`getRunApiRunsRunIdGet`) — no run-detail view calls it.
- `POST /api/experiments/:id/metrics` (`createMetricApiExperimentsExperimentIdMetricsPost`)
  — the "hand-typed user metric" capability this route exists for has no
  UI entry point yet.
- `GET /api/projects/:id/documents/:id` (single-document fetch,
  `getDocumentApiProjectsProjectIdDocumentsDocumentIdGet`) — the frontend
  only uses list/create/update/pdf/assets/check_citations for documents,
  never the single-GET.
- `POST /api/projects/:id/memory/query` (`queryProjectMemoryApiProjectsProjectIdMemoryQueryPost`)
  — no standalone "search memory" UI; the only live memory retrieval path
  is the backend-internal `harness._maybe_retrieve` call, which bypasses
  this HTTP route entirely (per `memory_design.md`).

All 19 routers *are* mounted in `main.py:168-184` (confirmed against every
import at `main.py:30-45`) — none of the above is dead at the wiring level,
only at the "nothing in the shipped UI reaches it" level.

---

## Open questions / rough edges

- **Kernel/cell/metric/run-detail/memory-query routes are shipped but
  orphaned.** Five endpoints exist, are registered, and have full
  request/response models, but nothing in `frontend/src` calls them. Either
  the UI work for these is still pending, or these are meant as
  API-only/future surfaces — the code gives no signal either way.
- **`propose_cell`'s own docstring says it was bolted on after sign-off**
  (`experiments.py:88-96`) to close a gap in the approval flow, yet it's
  still not reachable from any component today — the gap it was meant to
  close may still be open on the frontend side.
- **Two parallel PDF-fetch mechanisms.** `GET /api/papers/:id/pdf` and
  `GET /api/projects/:id/documents/:id/pdf` both stream via `FileResponse`
  server-side but are called from the frontend through a completely
  separate hand-rolled fetch path (`bridge.ts`) rather than the generated
  client — same for the two voice routes. This is a structural constraint
  (binary bodies + bearer header), but it means the generated SDK
  functions for these four routes are simply never exercised, which could
  mask a schema drift the type generator wouldn't catch.
- **Metrics route was added for a checklist requirement, not a named
  frontend need** (`runs.py:1-10`'s own docstring: "not named in PRD's REST
  table... this capability needs *some* API path") — consistent with it
  being unused today.
- **`memory.py`'s REST route and the harness's internal retrieval are two
  independent entry points into the same `query_memory` function** with no
  code linking them — a caller hitting the HTTP route gets the identical
  hybrid-retrieve+rerank behavior the harness uses mid-turn, but there is
  no evidence anything (frontend or backend) actually calls it that way.
- **No request size/format validation visible in `voice.py`** — the
  transcribe route reads `request.body()` raw with no content-type or size
  check in the route itself; whatever guarding exists is inside the
  `voice` module, invisible from here.
- **`RunDetail.stdout` reads the whole log file into memory** with no size
  cap (`runs.py:47-49`) — the docstring flags this as a known future seam,
  not yet a real problem given current log sizes, but there's no guard in
  code today.
