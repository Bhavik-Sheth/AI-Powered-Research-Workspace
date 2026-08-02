"""Notes read path — lists/fetches the `notes` index Vault Writer maintains.

MODULES.md's Vault Writer interface names `write_note` only; reading the
index is a plain select, not a vault write, so it does not belong on that
module's public surface. Mirrors how `backend/projects/` already fills the
same kind of gap for Project Record CRUD (Phase 1.2) — flagged here for the
same reason, not added silently: routes must never touch SQL directly
(Rules.md), and this is the module that spares them from it for notes.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Notes
from vault.models import Note


async def list_notes(session: AsyncSession, project_id: uuid.UUID) -> list[Note]:
    """Newest-edited first — backs the notes list (Schema.md `notes (project_id, updated_at DESC)`)."""
    rows = await session.scalars(
        select(Notes).where(Notes.project_id == project_id).order_by(Notes.updated_at.desc())
    )
    return [Note.from_row(row) for row in rows]
