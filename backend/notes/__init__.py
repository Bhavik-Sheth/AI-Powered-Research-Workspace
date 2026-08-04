"""Notes read path — lists/fetches the `notes` index Vault Writer maintains.

MODULES.md's Vault Writer interface names `write_note` only; reading the
index is a plain select, not a vault write, so it does not belong on that
module's public surface. Mirrors how `backend/projects/` already fills the
same kind of gap for Project Record CRUD (Phase 1.2) — flagged here for the
same reason, not added silently: routes must never touch SQL directly
(Rules.md), and this is the module that spares them from it for notes.
"""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import IdeaEdges, Notes
from vault.models import Note


async def list_notes(session: AsyncSession, project_id: uuid.UUID) -> list[Note]:
    """Newest-edited first — backs the notes list (Schema.md `notes (project_id, updated_at DESC)`)."""
    rows = await session.scalars(
        select(Notes).where(Notes.project_id == project_id).order_by(Notes.updated_at.desc())
    )
    return [Note.from_row(row) for row in rows]


async def list_unlinked_note_ids(session: AsyncSession, project_id: uuid.UUID) -> list[uuid.UUID]:
    """Notes with no `idea_edges` row referencing them as source or
    destination — backs Dashboard's "N unlinked" tile qualifier (Bug Fix
    Plan Phase 4.3). A plain select over an existing table, same kind of gap
    `list_notes` already fills for this module."""
    note_ids = list((await session.scalars(select(Notes.id).where(Notes.project_id == project_id))).all())
    if not note_ids:
        return []
    note_id_strs = {str(note_id) for note_id in note_ids}
    edge_rows = (
        await session.execute(
            select(IdeaEdges.src_id, IdeaEdges.dst_id).where(
                IdeaEdges.project_id == project_id,
                or_(
                    IdeaEdges.src_type == "note",
                    IdeaEdges.dst_type == "note",
                ),
            )
        )
    ).all()
    linked = {row_id for src_id, dst_id in edge_rows for row_id in (src_id, dst_id)} & note_id_strs
    return [note_id for note_id in note_ids if str(note_id) not in linked]
