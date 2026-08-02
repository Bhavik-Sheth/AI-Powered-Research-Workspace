"""Session Transport — one Companion WebSocket session per project, carrying
typed events between the renderer and the Agent Harness (MODULES.md, D18
node 5). A pure event pipe: it knows nothing about tools or context
assembly, only how to authenticate the upgrade, keep one session per
project, and shuttle events to and from `harness.run_turn`.

Phase 2.2 adds a second, unrelated user of this same socket: Execution
Sandbox's run-log/status streaming (`sandbox.RunLogEvent`/`RunStatusEvent`).
A run is not a Companion turn, so those event models are **not** added to
`harness.models.TurnEvent`'s union — folding them in would make every
`TurnEvent` consumer reason about a case that has nothing to do with a
turn. Instead, `get_session` below exposes the same per-project registry
`sandbox` broadcasts through directly, and `broadcast`'s type hint is
widened from `TurnEvent` to any Pydantic model — the function body only
ever needed `.model_dump(mode="json")`, so nothing about its behavior
changes for the harness's own callers. The frontend discriminates by each
message's own `event` field either way.
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
from harness.models import ErrorEvent, SelectionState, SessionRef, TurnEvent, UIState

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
    input_modality: Literal["text", "voice"] = "text"


class UIStateEvent(BaseModel):
    event: Literal["ui_state"] = "ui_state"
    selection: SelectionState | None = None


class InterruptEvent(BaseModel):
    event: Literal["interrupt"] = "interrupt"
    # Optional: `harness.interrupt` cancels by session, not by turn (only one
    # turn per session may ever be in flight — MODULES.md), so this isn't
    # read yet. Required-but-unfillable was a real bug: the frontend had no
    # real turn_id to send between turns and sent "" as a placeholder, which
    # fails UUID validation and gets silently dropped by the receive loop's
    # error handler — the Stop button never actually reached the backend.
    turn_id: uuid.UUID | None = None


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


def get_session(project_id: uuid.UUID) -> Session | None:
    """The same per-project registry `handle_connect` populates — exposed
    so a non-harness broadcaster (Execution Sandbox's run-log streaming)
    can find a project's live socket without reaching into `_sessions`
    directly."""
    return _sessions.get(project_id)


async def broadcast(session: Session, event: TurnEvent | BaseModel) -> None:
    try:
        await session.websocket.send_json(event.model_dump(mode="json"))
    except RuntimeError:
        # The socket closed mid-turn (TRD §4.1: a dropped socket leaves the
        # session live, transcript persistence doesn't depend on it) — the
        # turn keeps running and persisting; there's just nobody to tell.
        logger.info("event=ws_broadcast_after_close project_id=%s", session.project_id)


async def _run_turn(
    session: Session, session_ref: SessionRef, text: str, ui_state: UIState, input_modality: str, cancel_flag: asyncio.Event
) -> None:
    async for turn_event in harness.run_turn(session_ref, text, ui_state, input_modality, cancel_flag):
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
    # Reserved synchronously, in this same call, before the task is even
    # scheduled — an `interrupt` arriving right after this returns must see
    # the reservation already in place (harness.begin_turn's docstring).
    cancel_flag = harness.begin_turn(session_ref)
    if cancel_flag is None:
        await broadcast(
            session,
            ErrorEvent(
                code="turn_in_progress",
                message="A turn is already running for this session — interrupt it first.",
                recoverable=True,
                what_still_worked="the turn already in progress",
            ),
        )
        return
    # Spawned, not awaited: the receive loop must keep running so a
    # follow-up `interrupt` for *this* turn can actually be received while
    # it streams (D18 node 7 — a cancellable task bound to the session).
    session.turn_task = asyncio.create_task(
        _run_turn(session, session_ref, event.text, event.ui_state, event.input_modality, cancel_flag)
    )


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
