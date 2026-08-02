"""`GET /api/projects/:id/conversation` — the Companion transcript, verbatim
(TRD §4.1). Route glue only: history lookup lives in `conversations/`.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

import conversations
import db

router = APIRouter()


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    citations: list
    interrupted: bool
    input_modality: str
    created_at: datetime


class ConversationResponse(BaseModel):
    messages: list[MessageOut]


@router.get("/api/projects/{project_id}/conversation", response_model=ConversationResponse)
async def get_conversation(project_id: uuid.UUID) -> ConversationResponse:
    async with db.session() as session:
        rows = await conversations.list_messages(session, project_id)
    return ConversationResponse(
        messages=[
            MessageOut(
                id=row.id,
                role=row.role,
                content=row.content,
                citations=row.citations,
                interrupted=row.interrupted,
                input_modality=row.input_modality,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )
