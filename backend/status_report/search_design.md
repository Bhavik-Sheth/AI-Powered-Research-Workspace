# Search Federation — Design & Architecture

Search = one LLM query-understanding pass, then a deterministic ranking step (Firecrawl `/search` order when available, else a lexical-score fallback over an arXiv/OpenAlex/S2 fan-out), an optional cross-encoder rerank on top, and the full pool cached under a `result_id` in `result_store` so "search more" can reveal or widen without redoing the LLM step.

---

## Storage / data model

**`search/models.py`** defines the wire shapes (no DB tables of its own — persistence rides on the shared `result_store` table from `db/models.py:76`):

- `SearchFilters` — `year_min`, `year_max`, `venue`, `has_code`, `author` (`has_code`/`author` are declared but never read anywhere in `search/`).
- `FirecrawlHit` — `url`, `title`, `description`; one ordered hit from Firecrawl before it's resolved to a real paper.
- `RawHit` — one source's un-deduped hit: `source_ids` (a `papers.models.SourceIds`), `title`, `abstract`, `authors`, `year`, `venue`, `citation_count`, `source_url`, `pdf_url`.
- `PaperSummary` — same shape as `RawHit` plus a resolved `canonical_id`; this is what actually ships to callers.
- `ResultSet` — `result_id`, `query`, `results: list[PaperSummary]`, `sources_failed`, `has_more` (default `False`), `pool_size` (default `0`).

**`db.models.ResultStore`** (`backend/db/models.py:76-91`): `result_id` (PK, string — a `uuid4` minted in `search_papers`), `project_id` (nullable, no FK yet), `tool_name`, `ui_view` (JSONB — the full `ResultSet.model_dump(mode="json")`), `model_view` (a string summary, `"{n} results for '{query}'"`), `expires_at`, `created_at`. `search/__init__.py` never sets `project_id` when inserting — every cached result set is unscoped.

`_cache` (`search/__init__.py:227-248`) is the only writer: it fetches by `result_id`, and either overwrites `ui_view`/`model_view`/`expires_at` in place (widen path) or inserts a fresh row with a 1-hour TTL (`_RESULT_TTL`, line 47). There is no delete/eviction path in this module — expired rows just sit there until whatever job (not in `search/`) reaps `result_store`.

---

## Core mechanics

### 1. Query understanding (`search/query_understanding.py`)
One `complete_structured` call (`llm.complete_structured`, `tier="auxiliary"`, 20s timeout) extracts `keywords: list[str]` and a `SearchFilters` from the raw query text. This is the *only* LLM call in the whole pipeline (`__init__.py:1-4`, "never a per-source LLM rewrite"). Everything downstream is deterministic parameter mapping.

### 2. Ranking (`search/__init__.py:191-208`, `_rank`)
Tries Firecrawl first (`_rank_via_firecrawl`, lines 131-188):
- Skips straight to `None` if `config.firecrawl_api_key` is unset (`sources.py` reads it from `get_config()`).
- Calls `search_firecrawl(query, api_key, pool_size)` (`sources.py:37-69`) — POST `https://api.firecrawl.dev/v1/search`, walks the response defensively (`.get()`, `isinstance` checks), returns `[]` on anything malformed rather than raising.
- On `httpx.HTTPError` or `ValueError` it logs `event=firecrawl_search_failed` and returns `None`.
- If Firecrawl returns hits, it also runs the full arXiv/OpenAlex/S2 fan-out (`_fan_out`) and dedupes it (`_dedupe`), then for each Firecrawl hit (in Firecrawl's own order) finds the best `title_overlap` match among not-yet-used fan-out candidates. A match below `_ENRICHMENT_MATCH_THRESHOLD` (0.4) is dropped rather than guessed at. If nothing clears the bar, the whole thing returns `None` (fall through to fallback).

If Firecrawl's path returns `None` for any reason, `_rank` falls back to the raw fan-out (`_fan_out`, lines 79-100 — `asyncio.gather(..., return_exceptions=True)` across `search_arxiv`, `search_openalex`, `search_s2`, a failing source names itself in `sources_failed` rather than failing the call), dedupes it, and sorts by `lexical_score` (`lexical_score.py:44-52`: exact-title match dominates, then title token-overlap, then a small `log1p(citation_count)` tiebreaker). `"firecrawl"` is appended to `sources_failed` in this branch.

### 3. Dedup (`_dedupe`, `__init__.py:103-113`)
Keeps the first hit seen per `resolve_canonical_id(hit.source_ids)` (imported from `papers`); a hit whose ids don't resolve to any canonical id (`ValueError`) is dropped outright, never corrupts the dedup key.

### 4. Rerank (`_refine_with_reranker`, `__init__.py:211-224`)
Takes the top `_RERANK_TOP_N` (100) hits from whichever ranking is active, scores `f"{title}\n{abstract or ''}"` against the query with the cross-encoder (`reranker.py`), and reorders just that head; the tail past 100 is untouched and appended after. On `TimeoutError` from the reranker it returns the input order unchanged and the caller appends `"reranker"` to `sources_failed`. The reranker itself (`reranker.py`) lazy-loads `cross-encoder/ms-marco-MiniLM-L-6-v2` on first use behind an `asyncio.Lock`, bounded by a 30s `asyncio.wait_for` around an `asyncio.shield`ed load task — a timed-out caller doesn't kill the download; it keeps running in the background and a later call (or the same one retried) attaches to it.

### 5. Fresh search (`search_papers`, `__init__.py:251-293`)
`page=0` (default): runs query understanding → `_rank` (pool size `_FIRECRAWL_POOL_SIZE=20` for Firecrawl / `_FALLBACK_BASE_MAX_RESULTS=30` per source for fallback) → rerank → mints a fresh `uuid4()` `result_id` → caches the full `ResultSet` via `_cache`. `pool_size` on the returned set equals `len(results)` — i.e. everything fetched, not what's later shown.

`page > 0` requires `result_id` (raises `ValueError` otherwise) and delegates to `_widen`.

### 6. "Search more" — reveal vs. widen (Phase 6.2, per the module docstring `__init__.py:18-25`)
Two distinct mechanisms behind one feature name:
- **Reveal** = `refine_results` (`__init__.py:344-373`): no network call. Loads the cached `ResultSet` from `result_store` by `result_id`, re-applies `_matches` (year/venue filter, `__init__.py:334-341` — note `has_code` and `author` filters are defined in `SearchFilters` but `_matches` never checks them), then slices `[offset:offset+limit]` server-side. Returns `has_more` = whether the slice stopped short of the filtered pool, and `pool_size` = the filtered pool's length. This backs `GET /api/results/:resultId`.
- **Widen** = `_widen` (`__init__.py:296-331`), triggered by `search_papers(page>0, result_id=...)`: a real deeper fetch. Loads the cached set, re-runs query understanding, and re-runs `_rank` with `pool_size`/`fallback_max_results` increased by `page * _WIDEN_STEP` (20) over the base constants. New hits not already in the cached pool (by `canonical_id`) are appended to the existing `results` list; `_cache` is called with the *original* `expires_at` preserved (`row.expires_at`, not a fresh TTL) so the widened set keeps the original cache lifetime.

Note: arXiv/OpenAlex/S2 have no real cursor pagination, so "deeper" for the fallback fan-out and for Firecrawl's flat `limit` both just mean "ask for a bigger single page again" (per the comment at `__init__.py:57-69`) — not a true offset-paged continuation. A widen re-fetches from position 0 each time and relies on the append-if-new-canonical-id dedup to avoid duplicating what's already cached.

### 7. Rate limiting (`search/rate_limit.py`)
Thin per-source tuning on top of the shared `ratelimit.RateLimiter`/`call_with_backoff` (`backend/ratelimit.py`, not part of this module): `ARXIV_LIMITER` 1 req/3s, `OPENALEX_LIMITER` ~6-7/s, `S2_LIMITER_WITH_KEY` 1/s, `S2_LIMITER_NO_KEY` 1/6s, `FIRECRAWL_LIMITER` 1/2s. Every outbound call in `sources.py` (`search_arxiv`, `search_openalex`, `search_s2`, `search_firecrawl`, `fetch_openalex_references`, `fetch_s2_references`) goes through `call_with_backoff` with its source's limiter.

### 8. Reference-fetching (`sources.py:170-262`, `fetch_openalex_references`, `fetch_s2_references`)
Two functions not touched by `search_papers`/`refine_results` at all — they fetch a *paper's own* cited references (OpenAlex `referenced_works`, S2 `references`), sorted by citation count, capped at `max_results` (default 5). Not called anywhere inside `search/` itself.

---

## Callers & dependents

Two live call paths, both confirmed reachable from a running request:

1. **`backend/api/search.py`** — `POST /api/search` and `GET /api/results/{result_id}`, registered in `backend/main.py:44,180` via `app.include_router(search_router, ...)`.
   - `post_search` (line 36) calls `search.search_papers(body.query, body.filters, page=body.page, result_id=body.result_id)`, then re-slices the *already-cached full* `result_set.results` by `body.offset`/`body.limit` itself (default limit 5) — a second, API-layer slice on top of whatever `search_papers` returned, independent of `refine_results`'s own slicing.
   - `get_result` (line 55) calls `search.refine_results(result_id, offset=offset, limit=limit)` directly — this is the only live caller of `refine_results`.
   - Both catch `ValueError` from a missing/expired `result_id` and turn it into a 404.

2. **`backend/harness/tools.py`** — the agent's tool-dispatch. `search_papers` is declared in `TOOL_SCHEMAS` (line 44) with only a `query` string parameter (no filters, no page, no result_id exposed to the LLM). `dispatch` (line 134) calls `search.search_papers(args.get("query", ""))` — always `page=0`, always a fresh search, filters always `None` (so `understand_query`'s inferred filters are what's used). The result is wrapped into a `ToolResult` with `ui_view_result_id=result_set.result_id` and a `ui_actions: [{"action": "open_search_results", ...}]` — the harness never sees the full pool, only the `model_view` summary string of the top 5 titles.

No dead code found in the reachable core pipeline. Two things are present but unused by the reachable pipeline itself:
- `fetch_openalex_references` / `fetch_s2_references` (`sources.py:170-262`) — not called anywhere under `search/`; grepping the rest of the backend for these names would be needed to confirm a caller elsewhere (e.g. a "related papers"/citation-graph feature), but within this module's own pipeline they're unreferenced.
- `SearchFilters.has_code` and `SearchFilters.author` — accepted on the wire (`post_search`'s `body.filters`) but `_matches` (`__init__.py:334-341`) never checks them, so setting either has no effect on `refine_results`'s filtering.

---

## Open questions / rough edges

- **`_matches` silently ignores two of five filter fields.** `has_code` and `author` are declared in `SearchFilters`, accepted from the API, even passed through to `search_papers`'s initial `_rank` call as `effective_filters` (used only by `search_openalex`'s `filter=` param, which also doesn't touch `has_code`/`author`) — but `refine_results`'s reveal-time filtering never applies them at all. A caller setting `has_code=True` gets no filtering behavior anywhere in this module.
- **Widen re-runs query understanding from scratch every time.** `_widen` calls `understand_query(query)` again on every widen (`__init__.py:306`) — an extra LLM call per "search more" click, even though the original `keywords`/`filters` from the first pass aren't cached anywhere on the `ResultSet` to reuse.
- **Widen isn't a true pagination — it's "ask again, bigger."** Because arXiv/OpenAlex/S2 have no stable cursor and Firecrawl's `/search` has no offset param, each widen re-fetches from the start with a larger `limit`/`max_results` and relies on canonical-id dedup against the cached pool to find only the new tail. If a source's own ranking isn't stable between calls (no guarantee it is), a widen could plausibly surface different "new" hits than a literal continuation would.
- **`result_store` rows are never scoped to a `project_id`** from this module — `_cache` never sets it, so every cached result set is effectively global/anonymous within the table, keyed only by the random `result_id`. Nothing in `search/` reads or enforces project ownership on a `result_id` fetch either — `refine_results`/`_widen` will happily load and mutate any `result_id` regardless of which project asked.
- **No eviction.** `expires_at` is written but nothing in this module deletes expired rows; whatever reaps `result_store` (if anything does) lives outside `search/`.
- **`post_search`'s offset/limit slicing duplicates `refine_results`'s slicing logic** (`_matches`-free, simpler) rather than calling `refine_results` itself — two independent slice implementations over the same cached pool shape, one filter-aware (`refine_results`), one not (`post_search`'s inline slice of `search_papers`'s direct return, which was never filtered by `_matches` in the first place, since `search_papers` doesn't take a filter for its own results — only `effective_filters` used at fetch time via `search_openalex`).
- **Enrichment match can silently favor an inferior fan-out hit.** `_rank_via_firecrawl`'s per-hit best-match loop (`__init__.py:170-188`) picks the *first* fan-out candidate reaching the highest `title_overlap` score for each Firecrawl hit in order; ties are broken by fan-out iteration order (dict insertion via `_dedupe`), which is itself dependent on `asyncio.gather`'s return order from `_fan_out` — not deterministic across runs if two sources return same-titled works.
