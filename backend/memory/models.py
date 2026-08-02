"""Wire/value shapes for Memory Index (Rules.md: names match the wire shape)."""

import uuid
from typing import Literal

from pydantic import BaseModel

SourceType = Literal["paper_section", "abstract", "note", "experiment", "conversation_summary"]


class CitedRow(BaseModel):
    """One retrieval hit, always traceable back to the row it came from
    (MODULES.md `query_memory`) — the paper it's from when it has one, the
    verbatim text, and the span within the source artifact."""

    id: uuid.UUID
    source_type: SourceType
    source_id: str
    paper_id: uuid.UUID | None
    text: str
    section_heading: str | None
    char_start: int
    char_end: int
