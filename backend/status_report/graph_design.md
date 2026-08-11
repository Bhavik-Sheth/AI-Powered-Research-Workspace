# Knowledge Graph — Design & Architecture

## TLDR

The Knowledge Graph is two Postgres tables (`paper_edges` global, `idea_edges` project-scoped) plus a thin write/read module (`backend/graph/__init__.py`) and a recursive-CTE traversal primitive in `backend/db/__init__.py`. Edges get written as a side effect of the paper pipeline: `extract_card_job` turns validated card fields (dataset/method) into LLM-derived edges, `enrich_paper_job` turns harvested code/dataset links into metadata edges, and `trace_references_job` turns resolved references into `cites` metadata edges. `GET /api/projects/{id}/graph` (`backend/api/graph.py`) calls `graph.get_graph`, which resolves the project's own papers as traversal roots, walks `paper_edges` outward up to 2 hops via `db.traverse_graph`, unions in every `idea_edges` row for the project flat (no traversal), and resolves paper titles/ids for any paper nodes touched. The frontend (`frontend/src/graph/GraphView.tsx`, wired into `AppShell.tsx`) renders the edge list as a Cytoscape graph, colouring/shaping nodes by type and dashing edges by provenance. The whole write→store→read→render path is live. The one structural gap: `idea_edges` is queried and unioned everywhere the graph is read, but nothing in the codebase ever inserts a row into it — the project-scoped half of the graph is permanently empty today.

## Storage / data model

**`paper_edges`** (`backend/db/models.py:279-312`) — the global graph:
- `src_type`/`dst_type` constrained to `paper, author, dataset, repo, topic, method, concept`.
- `relation` constrained to `cites, cited_by, authored_by, uses_dataset, has_code, has_topic, method_of, related_method`.
- `provenance` constrained to `metadata, llm` only (no `user` value here).
- `source_api` (nullable string) — which literature API asserted a metadata edge; `NULL` when the edge came from a source that wasn't actually a query (e.g. an in-text URL/id).
- `confidence` (nullable float) — only meaningful for `provenance='llm'` rows.
- Unique/conflict key: `(src_type, src_id, dst_type, dst_id, relation, provenance)` — re-extraction is idempotent, a re-run's edge is a no-op, not a duplicate row (`backend/graph/__init__.py:17-29`).
- Node ids for `paper` type are `papers.canonical_id`, not `papers.id`.

**`idea_edges`** (`backend/db/models.py:315-349`) — the project-scoped half:
- Has its own `project_id` column (FK to `projects`, cascade delete).
- `src_type`/`dst_type` constrained to `note, experiment, paper, dataset, method, concept, highlight`.
- `relation` constrained to `inspired_by, uses_dataset, references_note, relates_to, contradicts`.
- `provenance` constrained to `metadata, llm, user` (a third value `paper_edges` doesn't allow).
- No `source_api`/`confidence` columns at all.
- Unique key includes `project_id` in addition to the same 5-tuple.

**Wire/value models** (`backend/graph/models.py`):
- `MetadataEdge`/`LLMEdge` — the two write-side shapes, keyed to `paper_edges`' narrower `NodeType`/`Relation` literals (models.py:8-11).
- `GraphEdge` (models.py:57-65) — the read-side/wire shape returned by `get_graph`, with a wider `GraphNodeType`/`GraphRelation` union (models.py:39-53) covering both tables' vocabularies (adds `note, experiment, highlight` node types and `inspired_by, references_note, relates_to, contradicts` relations).
- `Graph` (models.py:68-77) — `{edges, paper_ids, paper_titles}`; the latter two are dicts keyed by `canonical_id`, populated so a frontend can resolve a paper node's real UUID/title without a second round trip.

## Core mechanics

**Write path — three call sites, all inside `backend/papers/__init__.py`:**

1. `extract_card_job` (papers/__init__.py:440-539) runs auxiliary-tier LLM extraction over section windows for 5 standard card fields. Two of those fields — `datasets` and `method` — are also graph-edge fields (`_GRAPH_EDGE_FIELDS`, papers/__init__.py:77-80: `datasets → (dataset, uses_dataset)`, `method → (method, related_method)`). For each such field that passes Provenance anchoring, an `LLMEdge` is appended (papers/__init__.py:514-525) with `src_type="paper"`, `dst_id=slugify(span.quote[:80])`. After the card/state commit, if any LLM edges were collected, `write_llm_edges` is called in a separate session (papers/__init__.py:535-537) — a card write failing never rolls back an edge write and vice versa. **`confidence` is never set on these `LLMEdge` constructions, so despite the schema supporting it, every LLM edge written today has `confidence=None`.**
2. `enrich_paper_job` (papers/__init__.py:656-708) harvests code/dataset links in three tiers (paper's own text → HuggingFace papers API → Firecrawl search, each only tried if the prior tier found nothing), writes `has_code`/`uses_dataset` `MetadataEdge`s tagged with whichever tier found them (`source_api ∈ {"text","huggingface","firecrawl"}`), and calls `write_metadata_edges`.
3. `trace_references_job` (papers/__init__.py:760+) resolves the paper's top-N references into `papers` stub rows via `add_reference_stub`, then writes a `cites` `MetadataEdge` for every reference that resolved (docstring at papers/__init__.py:770-775; the actual write call is at papers/__init__.py:864, inside the loop over resolved references). `source_api` names whichever source resolved it (`openalex`/`s2`), or is `None` when only an in-text arXiv/DOI id was parsed.

All three jobs are legitimately enqueued from the live pipeline (`backend/papers/__init__.py:238,242,246,263,339-341,539`; job names registered in `backend/jobs/__init__.py:78-93`) — reachable from `add_paper`/`reprocess_paper` and from opening a paper.

`write_metadata_edges`/`write_llm_edges` (graph/__init__.py:32-65) both funnel into `_upsert` (graph/__init__.py:25-29), an `INSERT ... ON CONFLICT DO NOTHING` keyed on the 6-tuple conflict key — this is the only write primitive the module exposes; there's no update/delete path for an edge.

**Read path:**

`graph.get_graph(session, project_id, types, depth=2)` (graph/__init__.py:68-95):
1. Selects `Paper.canonical_id` for every paper in the project (`Paper` joined through `ProjectPapers`) — these become the traversal roots, tagged `("paper", canonical_id)`.
2. Calls `db.traverse_graph` (db/__init__.py:197-216), which runs one hand-written recursive CTE (`_GRAPH_TRAVERSAL_SQL`, db/__init__.py:159-193):
   - `graph_edges` CTE walks `paper_edges` outward from the root set, hop by hop, unioned with itself up to `depth` hops (default 2) — any edge touching a node already reached at hop < depth is pulled in at hop+1.
   - Final `SELECT` unions that traversal result (deduped, optionally filtered by `types`) with **every** `idea_edges` row for `project_id` (db/__init__.py:189-192), flat, no traversal — because `idea_edges` is already project-scoped, it doesn't need a hop-bounded walk.
   - An empty `roots` list still returns whatever `idea_edges` rows exist, never an error (docstring at db/__init__.py:207-208).
3. Back in `get_graph`, every edge whose `src_type`/`dst_type` is `"paper"` has its canonical id collected, then one query resolves `Paper.id`/`Paper.title` for those ids — this is what populates `Graph.paper_ids`/`Graph.paper_titles` so the frontend doesn't need a second lookup per paper node.
4. Returns a `Graph` — empty `edges: []` for a project with nothing yet, not an error.

**API surface:** `GET /api/projects/{project_id}/graph?types=a,b` (api/graph.py:16-20) — parses the `types` query param into a list or `None`, calls `graph.get_graph` inside one `db.session()`, registered in `backend/main.py:35,182` behind `require_bearer_token`.

**Frontend shaping:** `GraphView.tsx` fetches the edge list once per `projectId` change (GraphView.tsx:208-223), derives nodes implicitly from the edge endpoints (`nodesFromEdges`, `frontend/src/graph/nodeStyle.ts:69-78` — "a concept node has no table by design"), maps the graph's `GraphNodeType` vocabulary down to 6 legend categories (`paper, author, dataset, method, repo, idea` — `nodeStyle.ts:11-24`), colours/shapes nodes by category, dashes edges whose `provenance === "llm"` (GraphView.tsx:183), and lays out with Cytoscape's `cose` layout. Labels are client-side wrapped/capped at 3 lines with a canvas-measured word-wrap (GraphView.tsx:54-121) — a rendering concern with no backend counterpart.

## Callers & dependents

**Live:**
- `backend/api/graph.py` → `graph.get_graph` → `db.traverse_graph` — the only HTTP entry point, reachable from `AppShell.tsx:626` rendering `<GraphView>`.
- `backend/papers/__init__.py` → `write_llm_edges` (1 call site, papers/__init__.py:537) and `write_metadata_edges` (2 call sites, papers/__init__.py:708 and 864) — all three inside jobs that are genuinely enqueued from `add_paper`/`reprocess_paper`/paper-open flows.
- `frontend/src/graph/GraphView.tsx` and `frontend/src/graph/nodeStyle.ts` — consume the exact `Graph`/`GraphEdge` wire shape the backend emits; no shape mismatch found.

**Dead / wired-to-nothing found in step 3:**
- **`idea_edges` has no write path anywhere in the codebase.** It's declared (`db/models.py:315`), unioned into every `get_graph`/`traverse_graph` call (`db/__init__.py:189-192`), and read by `backend/notes/__init__.py:list_unlinked_note_ids` (notes/__init__.py:28-49) to compute which notes have no graph link — but grepping the whole tree for `IdeaEdges(` construction or an insert against `idea_edges` turns up nothing outside the model declaration and the migration (`backend/alembic/versions/0011_phase3_idea_edges.py`). Concretely: `list_unlinked_note_ids` will always report every note in a project as unlinked, since `linked` (notes/__init__.py:48) can never be non-empty. The project-scoped side of the graph — notes, experiments, highlights, `inspired_by`/`relates_to`/`contradicts` relations — is schema-complete but functionally inert.
- `LLMEdge.confidence` (graph/models.py:33) is a real field with a real DB column, but the only code path that constructs an `LLMEdge` (papers/__init__.py:517-524) never sets it — every `provenance='llm'` edge in `paper_edges` today carries `confidence=None`.

## Open questions / rough edges

- **`idea_edges` is dead on the write side** (see above) — the graph's project-specific vocabulary (`note`, `experiment`, `highlight`, `inspired_by`, `references_note`, `relates_to`, `contradicts`) exists only in constraints and types, never in a row. Anything downstream that assumes "the graph shows my notes' connections" will see nothing.
- **`confidence` is asserted but never populated.** The `LLMEdge`/`GraphEdge` models carry a `confidence: float | None` clearly meant to let the UI show LLM-edge certainty, but the sole writer never sets it, so it's `None` for every LLM edge that exists. The frontend also never reads `confidence` at all (GraphView.tsx has no reference to it) — dead on both ends.
- **Traversal depth is a hardcoded default (`_DEFAULT_TRAVERSAL_DEPTH = 2`, graph/__init__.py:22) with no way to override it from the API** — `get_project_graph` never threads a `depth` param through, so every request gets exactly 2 hops regardless of graph size.
- **Node identity for non-paper types is a slugified quote fragment** (`slugify(span.quote[:80])`, papers/__init__.py:522, and `_dataset_edge_dst_id`, papers/__init__.py:648-653) — two papers describing "attention mechanism" and "an attention mechanism" would slugify to different node ids and never merge, silently fragmenting the graph rather than erroring. The dataset case is split by trust: HuggingFace-sourced datasets use the real slug (merges correctly), text-harvested ones use the same lossy slugify (dup-tolerant only by luck).
- **`write_metadata_edges`/`write_llm_edges` both take a `paper_id: uuid.UUID` parameter that is never used inside either function** (graph/__init__.py:32,50) — every edge value already carries its own `src_id`/`dst_id`, so `paper_id` is dead weight in the signature.
- **No delete/update path for an edge at all** — a paper's dataset/method changing on re-extraction, or a bad edge needing correction, has no mechanism; `ON CONFLICT DO NOTHING` only prevents duplicates of an edge already correct, it can't retract a stale one.
- **`GraphEdge.confidence` typed as `float | None`** in the wire model but the underlying `paper_edges.confidence` column is `Float` with no bounds check — nothing constrains it to `[0,1]` at the DB or Pydantic level, though nothing writes a real value today regardless.
