"""Conversation history — the read side of the Companion transcript.

Not a module MODULES.md names on its own; it's the REST-facing counterpart
to Session Transport's `_get_or_create_conversation` lookup (same query
shape, read-only here — a `GET` should not create a conversation row just
because the transcript was requested before anyone has said anything).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conversations, Messages


async def _latest_conversation_id(session: AsyncSession, project_id: uuid.UUID) -> uuid.UUID | None:
    return await session.scalar(
        select(Conversations.id).where(Conversations.project_id == project_id).order_by(Conversations.created_at.desc())
    )


async def list_messages(session: AsyncSession, project_id: uuid.UUID) -> list[Messages]:
    """Verbatim transcript for the project's most recent conversation, in
    order (TRD §4.1: "the transcript is rehydrated from `conversations`/
    `messages`, which hold it verbatim"). No conversation yet is a legal
    empty list, never an error."""
    conversation_id = await _latest_conversation_id(session, project_id)
    if conversation_id is None:
        return []
    rows = await session.scalars(select(Messages).where(Messages.conversation_id == conversation_id).order_by(Messages.seq))
    return list(rows.all())
