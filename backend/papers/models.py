"""Wire/value shapes for Paper Pipeline (Rules.md: Pydantic model names match the wire shape)."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class SourceIds(BaseModel):
    """Whatever source ids a literature API returned for one paper."""

    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None


class PaperInput(BaseModel):
    """What the caller submits to add a paper — from a search result or a
    pasted link/id. A pure upload with no resolvable id is out of scope for
    v1: `canonical_id_source` (Schema.md) has no "upload" value, so at least
    one of doi/arxiv_id/openalex_id/s2_id is required."""

    source_ids: SourceIds
    title: str | None = None
    abstract: str | None = None
    source_url: str | None = None
    pdf_url: str | None = None


class Paper(BaseModel):
    id: uuid.UUID
    canonical_id: str
    title: str
    abstract: str | None = None
    source_url: str | None = None
    pdf_origin: str | None = None
    fetch_state: str
    parse_state: str
    embed_state: str
    extract_state: str
    references_state: str


class SectionInfo(BaseModel):
    section_id: str
    heading: str
    level: int
    char_start: int
    char_end: int


class ReferenceInfo(BaseModel):
    ref_id: str
    raw: str
    title: str | None = None
    # The resolved stub/full `papers.id` this reference points to (Phase
    # 6.3) — `None` until `trace_references_job` has resolved it, or when
    # resolution found nothing.
    paper_id: uuid.UUID | None = None


class PaperContentView(BaseModel):
    full_text: str
    sections: list[SectionInfo]
    references: list[ReferenceInfo]
    datasets: list[dict]
    code_links: list[dict]
    parsed_at: datetime


class PaperCardField(BaseModel):
    field_key: str
    value: str
    anchor_id: uuid.UUID
    section_heading: str | None = None
    char_start: int
    char_end: int
