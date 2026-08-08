"""Search Federation — one deduped, reranked, cached result set from the
literature APIs (MODULES.md). One LLM query-understanding pass, then
deterministic per-source parameter mapping — never a per-source LLM
rewrite (D21).

Phase 6.1 / D21 amendment: Firecrawl `/search` is now the relevance
authority — its result order drives ranking, and arXiv/OpenAlex/S2 are
demoted to *enrichment*, resolving each Firecrawl hit to a real canonical
id, citation count and OA PDF via their existing clients, so dedup,
`resolve_canonical_id` and `add_paper` stay untouched. When no
`FIRECRAWL_API_KEY` is configured, the quota is exhausted, or the call
fails, search degrades to the arXiv/OpenAlex/S2 fan-out ranked by the
deterministic `lexical_score` — never raw source order — and names
`firecrawl` in `sources_failed`. The cross-encoder reranker is an optional
refinement pass on top of whichever ranking is active, never the sole
ranker and never a blocking dependency.

Phase 6.2 ("Search more"): `search_papers`'s `page`/`result_id` and
`refine_results`'s `offset`/`limit` split "reveal" from "widen" (PRD/UI):
reveal reads more of the pool already cached in `result_store.ui_view` —
`refine_results` slicing it server-side, no network call to a literature
source; widen (`page > 0`) is a real deeper fetch — a bigger Firecrawl
`limit` or a bigger per-source `max_results` — whose new, not-already-cached
hits are appended in place to the same `result_id`'s cached pool, so a
still-open search tab's cache stays coherent and `expires_at` is preserved.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx

import db
from config import get_config
from db.models import ResultStore
from papers import resolve_canonical_id
from search.lexical_score import lexical_score, title_overlap
from search.models import PaperSummary, RawHit, ResultSet, SearchFilters
from search.query_understanding import understand_query
from search.reranker import rerank
from search.sources import search_arxiv, search_firecrawl, search_openalex, search_s2

logger = logging.getLogger(__name__)

_RESULT_TTL = timedelta(hours=1)
_RERANK_TOP_N = 100

# How many ordered hits to ask Firecrawl for on the initial (page 0) search.
# Kept well above the 5 that render so the cached pool has enough for Phase
# 6.2's "Search more" to reveal without a new network call — not specified
# by Firecrawl's docs fixture used here, so treated as an internal tuning
# constant, not a wire contract.
_FIRECRAWL_POOL_SIZE = 20

# The arXiv/OpenAlex/S2 fallback fan-out's own per-source page size on the
# initial search — matches each client's own default (`sources.py`) so
# page-0 behaviour is unchanged from Phase 6.1.
_FALLBACK_BASE_MAX_RESULTS = 30

# Phase 6.2 "widen": how much deeper each `page` reaches — added to
# `_FIRECRAWL_POOL_SIZE`/`_FALLBACK_BASE_MAX_RESULTS` per page. arXiv/
# OpenAlex/S2 don't offer stable cursor pagination (D21), so "page 2" is
# "ask each client for `_WIDEN_STEP` more results", not a traditional
# offset-paged request; same idea applied to Firecrawl's `limit` since its
# `/search` endpoint documents only a flat result-count cap, not an
# offset/page parameter (see `search/sources.py`'s own note on this).
_WIDEN_STEP = 20

# Minimum token-overlap (search/lexical_score.title_overlap) between a
# Firecrawl hit's title and an arXiv/OpenAlex/S2 candidate's title to
# accept that candidate as the hit's enrichment match. Below this, the
# Firecrawl hit has no confident canonical id and is dropped rather than
# guessed at.
_ENRICHMENT_MATCH_THRESHOLD = 0.4


async def _fan_out(
    keywords: list[str], filters: SearchFilters, max_results: int = _FALLBACK_BASE_MAX_RESULTS
) -> tuple[list[RawHit], list[str]]:
    """Runs every source in parallel; a failing source degrades the result
    (names itself in `sources_failed`), never a 500 for a partial failure.
    `max_results` is per-source (Phase 6.2 widen deepens it; page 0 uses
    each client's own default)."""
    sources = {
        "arxiv": search_arxiv(keywords, max_results),
        "openalex": search_openalex(keywords, filters, max_results),
        "s2": search_s2(keywords, max_results),
    }
    outcomes = await asyncio.gather(*sources.values(), return_exceptions=True)

    hits: list[RawHit] = []
    failed: list[str] = []
    for name, outcome in zip(sources.keys(), outcomes):
        if isinstance(outcome, BaseException):
            failed.append(name)
        else:
            hits.extend(outcome)
    return hits, failed


def _dedupe(hits: list[RawHit]) -> list[RawHit]:
    """Keeps the first hit seen per canonical id (D25); a hit with no
    resolvable source id is dropped rather than corrupting the dedup key."""
    seen: dict[str, RawHit] = {}
    for hit in hits:
        try:
            canonical_id = resolve_canonical_id(hit.source_ids)
        except ValueError:
            continue
        seen.setdefault(canonical_id, hit)
    return list(seen.values())


def _to_summary(hit: RawHit) -> PaperSummary:
    return PaperSummary(
        canonical_id=resolve_canonical_id(hit.source_ids),
        source_ids=hit.source_ids,
        title=hit.title,
        abstract=hit.abstract,
        authors=hit.authors,
        year=hit.year,
        venue=hit.venue,
        citation_count=hit.citation_count,
        source_url=hit.source_url,
        pdf_url=hit.pdf_url,
    )


async def _rank_via_firecrawl(
    query: str,
    keywords: list[str],
    filters: SearchFilters,
    pool_size: int,
    fallback_max_results: int,
) -> tuple[list[RawHit], list[str]] | None:
    """Firecrawl-ordered, enriched hits, or `None` to signal "fall back to
    lexical-scored fan-out" — a missing key, exhausted quota or failed call
    are all the same degrade-and-name-`firecrawl`-in-`sources_failed` path
    (Rules.md: partial failure degrades, never a 500).

    Enrichment resolves each Firecrawl hit to a real canonical id by
    matching its title (via `title_overlap`) against the same
    arXiv/OpenAlex/S2 fan-out `search_papers` already runs for the
    fallback path — reusing the existing per-source clients rather than
    issuing a new API call per hit, so dedup and `resolve_canonical_id`
    stay exactly as they are today. A Firecrawl hit with no confident
    match is dropped, not guessed at.

    `pool_size`/`fallback_max_results` are how deep this call reaches —
    `_FIRECRAWL_POOL_SIZE`/`_FALLBACK_BASE_MAX_RESULTS` on the initial
    search, deeper on a Phase 6.2 widen.
    """
    config = get_config()
    if not config.firecrawl_api_key:
        return None

    try:
        firecrawl_hits = await search_firecrawl(query, config.firecrawl_api_key, pool_size)
    except (httpx.HTTPError, ValueError):
        logger.info("event=firecrawl_search_failed query=%r", query)
        return None
    if not firecrawl_hits:
        return None

    pool, fan_out_failed = await _fan_out(keywords, filters, fallback_max_results)
    candidates = _dedupe(pool)

    ranked: list[RawHit] = []
    used_canonical_ids: set[str] = set()
    for firecrawl_hit in firecrawl_hits:
        best_candidate: RawHit | None = None
        best_score = 0.0
        for candidate in candidates:
            canonical_id = resolve_canonical_id(candidate.source_ids)
            if canonical_id in used_canonical_ids:
                continue
            score = title_overlap(firecrawl_hit.title, candidate.title)
            if score > best_score:
                best_candidate, best_score = candidate, score
        if best_candidate is not None and best_score >= _ENRICHMENT_MATCH_THRESHOLD:
            ranked.append(best_candidate)
            used_canonical_ids.add(resolve_canonical_id(best_candidate.source_ids))

    if not ranked:
        return None
    return ranked, fan_out_failed


async def _rank(
    query: str,
    keywords: list[str],
    filters: SearchFilters,
    pool_size: int,
    fallback_max_results: int,
) -> tuple[list[RawHit], list[str]]:
    """The Firecrawl-or-lexical-fallback ranking shared by a fresh search
    (page 0) and a Phase 6.2 widen (page > 0) — identical shape, only how
    deep it reaches differs."""
    firecrawl_result = await _rank_via_firecrawl(query, keywords, filters, pool_size, fallback_max_results)
    if firecrawl_result is not None:
        return firecrawl_result

    hits, fan_out_failed = await _fan_out(keywords, filters, fallback_max_results)
    deduped = _dedupe(hits)
    deduped.sort(key=lambda hit: lexical_score(query, hit), reverse=True)
    return deduped, [*fan_out_failed, "firecrawl"]


async def _refine_with_reranker(query: str, hits: list[RawHit]) -> tuple[list[RawHit], bool]:
    """Cross-encoder refinement of the top `_RERANK_TOP_N` (D21 amendment:
    optional refinement pass on top of whichever ranking — Firecrawl-order
    or lexical-score — is already active, never the sole ranker). A
    reranker that isn't loaded within its bound degrades to that base
    order — same partial-failure shape as one literature source timing out
    in `_fan_out`, never a block on the search."""
    head, tail = hits[:_RERANK_TOP_N], hits[_RERANK_TOP_N:]
    try:
        scores = await rerank(query, [f"{hit.title}\n{hit.abstract or ''}" for hit in head])
    except TimeoutError:
        return hits, False
    ranked_head = [hit for hit, _ in sorted(zip(head, scores), key=lambda pair: pair[1], reverse=True)]
    return ranked_head + tail, True


async def _cache(result_set: ResultSet, *, expires_at: datetime | None = None) -> None:
    """Inserts a fresh cache row, or — Phase 6.2 widen — overwrites an
    existing `result_id`'s row in place so a still-open search tab's cache
    stays coherent. `expires_at` is only ever passed by the widen path, to
    preserve the original TTL (Schema.md: "`expires_at` unchanged")."""
    resolved_expires_at = expires_at or datetime.now(timezone.utc) + _RESULT_TTL
    async with db.session() as session:
        row = await session.get(ResultStore, result_set.result_id)
        if row is not None:
            row.ui_view = result_set.model_dump(mode="json")
            row.model_view = f"{len(result_set.results)} results for '{result_set.query}'"
            row.expires_at = resolved_expires_at
        else:
            session.add(
                ResultStore(
                    result_id=result_set.result_id,
                    tool_name="search_papers",
                    ui_view=result_set.model_dump(mode="json"),
                    model_view=f"{len(result_set.results)} results for '{result_set.query}'",
                    expires_at=resolved_expires_at,
                )
            )


async def search_papers(
    query: str,
    filters: SearchFilters | None = None,
    page: int = 0,
    result_id: str | None = None,
) -> ResultSet:
    """Firecrawl `/search` ranks (enriched via arXiv/OpenAlex/S2), or —
    when unavailable — the arXiv/OpenAlex/S2 fan-out ranked by deterministic
    lexical score; the cross-encoder refines whichever is active. Cached in
    full under `result_id` (`backend/api/search.py` caps the returned
    `results` to its `limit`, the full pool stays in `result_store`).

    Phase 6.2: `page > 0` is a "Search more" widen call, not a fresh search
    — it requires the `result_id` being widened, fetches a deeper pool (a
    bigger Firecrawl `limit` / per-source `max_results`), and appends only
    the hits not already in that `result_id`'s cached pool, preserving its
    `expires_at`. `page=0` (the default) is unchanged from Phase 6.1: it
    always mints a fresh `result_id`."""
    if page > 0:
        if not result_id:
            raise ValueError("page > 0 requires the result_id being widened")
        return await _widen(query, filters, page, result_id)

    understanding = await understand_query(query)
    effective_filters = filters or understanding.filters

    ranked, failed = await _rank(
        query, understanding.keywords, effective_filters, _FIRECRAWL_POOL_SIZE, _FALLBACK_BASE_MAX_RESULTS
    )
    refined, reranked = await _refine_with_reranker(query, ranked)
    if not reranked:
        failed = [*failed, "reranker"]

    results = [_to_summary(hit) for hit in refined]
    result_set = ResultSet(
        result_id=str(uuid.uuid4()),
        query=query,
        results=results,
        sources_failed=failed,
        pool_size=len(results),
    )
    await _cache(result_set)
    return result_set


async def _widen(query: str, filters: SearchFilters | None, page: int, result_id: str) -> ResultSet:
    """Phase 6.2 "Search more" widen: a real deeper fetch, appended to the
    `result_id`'s existing cached pool (deduped against it, same
    canonical-id dedup as everywhere else), never a fresh result set."""
    async with db.session() as session:
        row = await session.get(ResultStore, result_id)
    if row is None:
        raise ValueError(f"no cached result set {result_id}")
    cached = ResultSet.model_validate(row.ui_view)

    understanding = await understand_query(query)
    effective_filters = filters or understanding.filters

    pool_size = _FIRECRAWL_POOL_SIZE + page * _WIDEN_STEP
    fallback_max_results = _FALLBACK_BASE_MAX_RESULTS + page * _WIDEN_STEP
    ranked, failed = await _rank(query, understanding.keywords, effective_filters, pool_size, fallback_max_results)

    already_cached = {paper.canonical_id for paper in cached.results}
    new_papers: list[PaperSummary] = []
    for hit in ranked:
        summary = _to_summary(hit)
        if summary.canonical_id in already_cached:
            continue
        already_cached.add(summary.canonical_id)
        new_papers.append(summary)

    merged_results = [*cached.results, *new_papers]
    merged = ResultSet(
        result_id=result_id,
        query=cached.query,
        results=merged_results,
        sources_failed=failed,
        pool_size=len(merged_results),
    )
    await _cache(merged, expires_at=row.expires_at)
    return merged


def _matches(paper: PaperSummary, filters: SearchFilters) -> bool:
    if filters.year_min is not None and (paper.year is None or paper.year < filters.year_min):
        return False
    if filters.year_max is not None and (paper.year is None or paper.year > filters.year_max):
        return False
    if filters.venue is not None and (paper.venue or "").lower() != filters.venue.lower():
        return False
    return True


async def refine_results(
    result_id: str,
    filters: SearchFilters | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> ResultSet:
    """Re-filters a cached set without re-querying sources — and, Phase
    6.2, slices it: this is the fat-backend home for `GET
    /api/results/:resultId`'s "Search more" reveal step, which asks for
    "results `offset`..`offset + limit`" and gets back exactly that slice
    of the already-cached pool, `has_more`/`pool_size` telling it whether
    reveal is exhausted (D17 — the renderer never slices a pool it already
    holds; it always asks the backend for the page it wants)."""
    async with db.session() as session:
        row = await session.get(ResultStore, result_id)
    if row is None:
        raise ValueError(f"no cached result set {result_id}")

    cached = ResultSet.model_validate(row.ui_view)
    effective_filters = filters if filters is not None else SearchFilters()
    matched = [paper for paper in cached.results if _matches(paper, effective_filters)]
    sliced = matched[offset:] if limit is None else matched[offset : offset + limit]
    return ResultSet(
        result_id=cached.result_id,
        query=cached.query,
        results=sliced,
        sources_failed=cached.sources_failed,
        has_more=offset + len(sliced) < len(matched),
        pool_size=len(matched),
    )
