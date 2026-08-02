"""Wire/value shapes for Search Federation (Rules.md: names match the wire shape)."""

from pydantic import BaseModel

from papers.models import SourceIds


class SearchFilters(BaseModel):
    year_min: int | None = None
    year_max: int | None = None
    venue: str | None = None
    has_code: bool | None = None
    author: str | None = None


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
