"""Wire/value shapes for Paper Pipeline (Rules.md: Pydantic model names match the wire shape)."""

from pydantic import BaseModel


class SourceIds(BaseModel):
    """Whatever source ids a literature API returned for one paper."""

    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None
