"""Wire/value shapes for Provenance (Rules.md: names match the wire shape)."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class CharSpan(BaseModel):
    start: int
    end: int


class QuoteAnchor(BaseModel):
    """The shared quote-anchor object (D33) — one shape for a card field, a
    highlight, a matrix cell and a Companion citation."""

    id: uuid.UUID
    paper_id: uuid.UUID
    quote: str
    prefix: str
    suffix: str
    char_start: int
    char_end: int
    section_heading: str | None = None
    page_hint: int | None = None
    validated_at: datetime
