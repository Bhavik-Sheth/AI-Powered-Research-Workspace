"""Search Federation — one deduped, reranked, cached result set from the
literature APIs (MODULES.md). One LLM query-understanding pass, then
deterministic per-source parameter mapping — never a per-source LLM
rewrite (D21).
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import db
from db.models import ResultStore
from papers import resolve_canonical_id
from search.models import PaperSummary, RawHit, ResultSet, SearchFilters
from search.query_understanding import understand_query
from search.reranker import rerank
from search.sources import search_arxiv, search_openalex, search_s2

_RESULT_TTL = timedelta(hours=1)
_RERANK_TOP_N = 100


async def _fan_out(keywords: list[str], filters: SearchFilters) -> tuple[list[RawHit], list[str]]:
    """Runs every source in parallel; a failing source degrades the result
    (names itself in `sources_failed`), never a 500 for a partial failure."""
    sources = {
        "arxiv": search_arxiv(keywords),
        "openalex": search_openalex(keywords, filters),
        "s2": search_s2(keywords),
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
        title=hit.title,
        abstract=hit.abstract,
        authors=hit.authors,
        year=hit.year,
        venue=hit.venue,
        citation_count=hit.citation_count,
        source_url=hit.source_url,
        pdf_url=hit.pdf_url,
    )


async def _rank(query: str, hits: list[RawHit]) -> list[RawHit]:
    head, tail = hits[:_RERANK_TOP_N], hits[_RERANK_TOP_N:]
    scores = await rerank(query, [f"{hit.title}\n{hit.abstract or ''}" for hit in head])
    ranked_head = [hit for hit, _ in sorted(zip(head, scores), key=lambda pair: pair[1], reverse=True)]
    return ranked_head + tail


async def _cache(result_set: ResultSet) -> None:
    async with db.session() as session:
        session.add(
            ResultStore(
                result_id=result_set.result_id,
                tool_name="search_papers",
                ui_view=result_set.model_dump(mode="json"),
                model_view=f"{len(result_set.results)} results for '{result_set.query}'",
                expires_at=datetime.now(timezone.utc) + _RESULT_TTL,
            )
        )


async def search_papers(query: str, filters: SearchFilters | None = None) -> ResultSet:
    """Fan-out to arXiv/OpenAlex/S2, cross-encoder rerank of the top ~100, cached under `result_id`."""
    understanding = await understand_query(query)
    effective_filters = filters or understanding.filters

    hits, failed = await _fan_out(understanding.keywords, effective_filters)
    ranked = await _rank(query, _dedupe(hits))

    result_set = ResultSet(
        result_id=str(uuid.uuid4()), query=query, results=[_to_summary(hit) for hit in ranked], sources_failed=failed
    )
    await _cache(result_set)
    return result_set


def _matches(paper: PaperSummary, filters: SearchFilters) -> bool:
    if filters.year_min is not None and (paper.year is None or paper.year < filters.year_min):
        return False
    if filters.year_max is not None and (paper.year is None or paper.year > filters.year_max):
        return False
    if filters.venue is not None and (paper.venue or "").lower() != filters.venue.lower():
        return False
    return True


async def refine_results(result_id: str, filters: SearchFilters) -> ResultSet:
    """Re-filters a cached set without re-querying sources."""
    async with db.session() as session:
        row = await session.get(ResultStore, result_id)
    if row is None:
        raise ValueError(f"no cached result set {result_id}")

    cached = ResultSet.model_validate(row.ui_view)
    return ResultSet(
        result_id=cached.result_id,
        query=cached.query,
        results=[paper for paper in cached.results if _matches(paper, filters)],
        sources_failed=cached.sources_failed,
    )
