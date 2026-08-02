"""Wire-shape models for Vault Writer (Rules.md: Pydantic model names match the wire shape).

`NoteInput`/`Note` are the exact types MODULES.md's `write_note(project_id, note:
NoteInput) -> Note` names, and the same shapes TRD.md §4.2 sends and returns
over `GET/POST/PATCH /api/projects/:id/notes` — one pair of types for the
service call and the wire, not a duplicated request/response DTO.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel

from db.models import Notes


class NoteInput(BaseModel):
    """What a caller submits to create or update a note.

    `frontmatter_id` is absent to create (Vault Writer assigns one) and
    present to update — the id carried in the note's own YAML frontmatter,
    never the file path (D4).
    """

    frontmatter_id: uuid.UUID | None = None
    title: str
    body: str


class Note(BaseModel):
    """The `notes` row (Schema.md), returned by every notes endpoint."""

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    file_path: str
    body: str
    frontmatter: dict
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Notes) -> "Note":
        return cls(
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            file_path=row.file_path,
            body=row.body,
            frontmatter=row.frontmatter,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class AnchorHint(BaseModel):
    """A cached rendering hint only — never identity (TRD §4.1)."""

    page: int
    bbox: tuple[float, float, float, float]


class QuoteAnchorInput(BaseModel):
    """The wire shape of `QuoteAnchor` in TRD §4.1 — what a caller submits
    to be validated and anchored fresh (D24), same as an extractive card
    field."""

    quote: str
    prefix: str
    suffix: str
    hint: AnchorHint | None = None


class HighlightInput(BaseModel):
    """`POST /api/projects/:id/highlights` request body (TRD §4.2)."""

    paper_id: uuid.UUID
    anchor: QuoteAnchorInput
    comment: str | None = None
    color: str | None = None
    note_id: uuid.UUID | None = None


class Highlight(BaseModel):
    """The `highlights` row (Schema.md), returned by the highlights endpoint."""

    id: uuid.UUID
    project_id: uuid.UUID
    paper_id: uuid.UUID
    anchor_id: uuid.UUID
    quote: str
    char_start: int
    char_end: int
    section_heading: str | None
    comment: str | None
    color: str | None
    note_id: uuid.UUID | None
    created_at: datetime


class RunArtifacts(BaseModel):
    """What a completed run hands `write_experiment_files` to persist under
    `experiments/<exp>/runs/` (MODULES.md). Unused this slice — Phase 2.3's
    Execution Sandbox is the first caller to construct one; the shape here
    is only what the signature needs to type-check against, not a built-out
    contract yet.
    """

    stdout: bytes
    artifacts: dict[str, bytes] = {}
