"""Wire/value shapes for Search Federation (Rules.md: names match the wire shape)."""

from pydantic import BaseModel

from papers.models import SourceIds


class SearchFilters(BaseModel):
    year_min: int | None = None
    year_max: int | None = None
    venue: str | None = None
    has_code: bool | None = None
    author: str | None = None


class FirecrawlHit(BaseModel):
    """One ordered hit from Firecrawl `/search` (Phase 6.1/D21 amendment) —
    relevance-ranked, not yet resolved to a canonical paper id. Only the
    fields the enrichment match needs; anything else Firecrawl returns is
    ignored rather than modelled."""

    url: str
    title: str
    description: str | None = None


class RawHit(BaseModel):
    """One source's un-deduped, un-reranked hit, before it becomes a `PaperSummary`."""

    source_ids: SourceIds
    title: str
    abstract: str | None = None
    authors: list[str] = []
    year: int | None = None
    venue: str | None = None
    citation_count: int | None = None
    source_url: str | None = None
    pdf_url: str | None = None


class PaperSummary(BaseModel):
    canonical_id: str
    source_ids: SourceIds
    title: str
    abstract: str | None = None
    authors: list[str] = []
    year: int | None = None
    venue: str | None = None
    citation_count: int | None = None
    source_url: str | None = None
    pdf_url: str | None = None


class ResultSet(BaseModel):
    result_id: str
    query: str
    results: list[PaperSummary]
    sources_failed: list[str] = []
    # Phase 6.2 ("Search more"): `pool_size` is how many cached (and, for
    # `GET /api/results/:resultId`, filter-matched) papers exist behind this
    # `result_id` in total; `has_more` is whether `results` stops short of
    # that pool. Both default to the "this is the whole set" shape so a
    # caller that never asks for a page (e.g. `refine_results`'s pre-6.2
    # callers) sees the same `ResultSet` as before.
    has_more: bool = False
    pool_size: int = 0
