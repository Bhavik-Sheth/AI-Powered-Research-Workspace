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
