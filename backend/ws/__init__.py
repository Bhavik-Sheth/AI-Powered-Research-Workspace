"""Session Transport — one Companion WebSocket session per project, carrying
typed events between the renderer and the Agent Harness (MODULES.md, D18
node 5). A pure event pipe: it knows nothing about tools or context
assembly, only how to authenticate the upgrade, keep one session per
project, and shuttle events to and from `harness.run_turn`.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

import db
import harness
from config import get_config
from db.models import Conversations
from harness.models import SelectionState, SessionRef, TurnEvent, UIState

logger = logging.getLogger(__name__)

router = APIRouter()

_sessions: dict[uuid.UUID, "Session"] = {}


@dataclass
class Session:
    project_id: uuid.UUID
    websocket: WebSocket
    conversation_id: uuid.UUID
    ui_state: UIState = field(default_factory=UIState)
    turn_task: asyncio.Task | None = None


class UserMessageEvent(BaseModel):
    event: Literal["user_message"] = "user_message"
    text: str
    ui_state: UIState


class UIStateEvent(BaseModel):
    event: Literal["ui_state"] = "ui_state"
    selection: SelectionState | None = None


class InterruptEvent(BaseModel):
    event: Literal["interrupt"] = "interrupt"
    turn_id: uuid.UUID


UpstreamEvent = UserMessageEvent | UIStateEvent | InterruptEvent

_UPSTREAM_EVENT_TYPES: dict[str, type[BaseModel]] = {
    "user_message": UserMessageEvent,
    "ui_state": UIStateEvent,
    "interrupt": InterruptEvent,
}


async def _get_or_create_conversation(project_id: uuid.UUID) -> uuid.UUID:
    async with db.session() as session:
        conversation_id = await session.scalar(
            select(Conversations.id).where(Conversations.project_id == project_id).order_by(Conversations.created_at.desc())
        )
        if conversation_id is not None:
            return conversation_id
        row = Conversations(project_id=project_id)
        session.add(row)
        await session.flush()
        return row.id


async def handle_connect(project_id: uuid.UUID, token: str, websocket: WebSocket) -> Session | None:
    """`401`s (via WS close code 4401) before a session exists if the token
    is missing or wrong."""
    if token != get_config().bearer_token:
        await websocket.close(code=4401)
        return None

    await websocket.accept()
    conversation_id = await _get_or_create_conversation(project_id)
    session = Session(project_id=project_id, websocket=websocket, conversation_id=conversation_id)
    _sessions[project_id] = session
    return session


async def broadcast(session: Session, event: TurnEvent) -> None:
    try:
        await session.websocket.send_json(event.model_dump(mode="json"))
    except RuntimeError:
        # The socket closed mid-turn (TRD §4.1: a dropped socket leaves the
        # session live, transcript persistence doesn't depend on it) — the
        # turn keeps running and persisting; there's just nobody to tell.
        logger.info("event=ws_broadcast_after_close project_id=%s", session.project_id)


async def _run_turn(session: Session, session_ref: SessionRef, text: str, ui_state: UIState) -> None:
    async for turn_event in harness.run_turn(session_ref, text, ui_state):
        await broadcast(session, turn_event)


async def handle_message(session: Session, event: UpstreamEvent) -> None:
    if isinstance(event, UIStateEvent):
        session.ui_state = UIState(selection=event.selection)
        return

    session_ref = SessionRef(project_id=session.project_id, conversation_id=session.conversation_id)

    if isinstance(event, InterruptEvent):
        await harness.interrupt(session_ref)
        return

    session.ui_state = event.ui_state
    # Spawned, not awaited: the receive loop must keep running so a
    # follow-up `interrupt` for *this* turn can actually be received while
    # it streams (D18 node 7 — a cancellable task bound to the session).
    session.turn_task = asyncio.create_task(_run_turn(session, session_ref, event.text, event.ui_state))


def _parse_upstream(raw: dict) -> UpstreamEvent:
    model = _UPSTREAM_EVENT_TYPES.get(raw.get("event"))
    if model is None:
        raise ValueError(f"unknown event {raw.get('event')!r}")
    return model.model_validate(raw)


@router.websocket("/ws/session/{project_id}")
async def session_endpoint(websocket: WebSocket, project_id: uuid.UUID, token: str) -> None:
    session = await handle_connect(project_id, token, websocket)
    if session is None:
        return

    try:
        while True:
            raw = await websocket.receive_json()
            try:
                event = _parse_upstream(raw)
            except (ValidationError, ValueError) as exc:
                logger.warning("event=ws_bad_message project_id=%s error=%s", project_id, exc)
                continue
            await handle_message(session, event)
    except WebSocketDisconnect:
        logger.info("event=ws_session_disconnected project_id=%s", project_id)
    finally:
        _sessions.pop(project_id, None)
