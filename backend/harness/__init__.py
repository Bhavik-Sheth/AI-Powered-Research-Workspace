"""Agent Harness — assembles context, calls the LLM, streams typed turn
events, and persists the transcript for one agent turn (MODULES.md, D18).

Phase 1.5 ships the reader-Q&A path only (D19: "Reader Q&A is not a tool —
it is the core agent loop answering from ambient UI-state"), so `run_turn`
is a single LLM pass with no tool-calling loop; the iteration cap and tool
dispatch land with later phases as tools are added. Citations are enforced
structurally (D24): the model is asked to wrap every quoted claim in
`<cite>` tags, and every tag is independently re-validated against the
paper's parsed text via Provenance before the turn completes — a tag that
does not resolve is relabelled `<unverified>`, never trusted from the
model's own say-so.
"""

import re
import uuid
from collections.abc import AsyncIterator

from sqlalchemy import func, select

import db
from db.models import Messages
from harness.models import ErrorEvent, SessionRef, StatusEvent, TextDeltaEvent, TurnCompleteEvent, TurnEvent, UIState
from llm import LLMError, Message, complete
from provenance import validate_and_anchor

__all__ = ["run_turn", "interrupt"]

_CITE_PATTERN = re.compile(r"<cite>(.*?)</cite>", re.DOTALL)

_SYSTEM_PROMPT = (
    "You are the Research Companion, helping a researcher who is reading a paper. "
    "Wrap every verbatim quote that supports a factual claim about the paper in <cite></cite> "
    "tags, copying the paper's exact wording inside the tags. Put your own reasoning and "
    "commentary outside the tags. Make no factual claim about the paper without a supporting quote."
)


async def _history(session, conversation_id: uuid.UUID) -> list[Message]:
    rows = (
        await session.scalars(select(Messages).where(Messages.conversation_id == conversation_id).order_by(Messages.seq))
    ).all()
    return [Message(role=row.role, content=row.content) for row in rows if row.role in ("user", "assistant")]


async def _next_seq(session, conversation_id: uuid.UUID) -> int:
    last = await session.scalar(select(func.max(Messages.seq)).where(Messages.conversation_id == conversation_id))
    return (last or 0) + 1


async def _validate_citations(session, paper_id: uuid.UUID | None, text: str) -> tuple[str, list[dict]]:
    """D24's substring validator, applied to every `<cite>` span the model
    produced. Returns the text with failed tags relabelled `<unverified>`,
    plus the structured citation list `messages.citations` stores."""
    citations: list[dict] = []
    pieces: list[str] = []
    cursor = 0
    for match in _CITE_PATTERN.finditer(text):
        pieces.append(text[cursor : match.start()])
        quote = match.group(1)
        anchor = await validate_and_anchor(session, paper_id, quote, "", "") if paper_id is not None else None
        if anchor is None:
            pieces.append(f"<unverified>{quote}</unverified>")
        else:
            citations.append({"anchor_id": str(anchor.id), "quote": quote})
            pieces.append(f"<cite>{quote}</cite>")
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces), citations


async def run_turn(session_ref: SessionRef, message: str, ui_state: UIState) -> AsyncIterator[TurnEvent]:
    turn_id = uuid.uuid4()
    paper_id = ui_state.selection.paper_id if ui_state.selection is not None else None

    async with db.session() as db_session:
        history = await _history(db_session, session_ref.conversation_id)
        seq = await _next_seq(db_session, session_ref.conversation_id)
        db_session.add(
            Messages(conversation_id=session_ref.conversation_id, seq=seq, turn_id=turn_id, role="user", content=message, citations=[])
        )

    llm_messages = [Message(role="system", content=_SYSTEM_PROMPT)]
    if ui_state.selection is not None:
        llm_messages.append(
            Message(role="system", content=f'The user has highlighted this passage from the paper:\n"{ui_state.selection.anchor.quote}"')
        )
    llm_messages += history
    llm_messages.append(Message(role="user", content=message))

    yield StatusEvent(text="thinking…")

    full_text = ""
    try:
        async for chunk in complete(llm_messages, tier="primary"):
            full_text += chunk.delta
    except LLMError as exc:
        yield ErrorEvent(code=type(exc).__name__, message=str(exc), recoverable=True, what_still_worked="nothing — the turn produced no answer")
        yield TurnCompleteEvent(turn_id=turn_id, interrupted=False, iterations=1)
        return
    except RuntimeError as exc:
        yield ErrorEvent(code="not_configured", message=str(exc), recoverable=True, what_still_worked=None)
        yield TurnCompleteEvent(turn_id=turn_id, interrupted=False, iterations=1)
        return

    async with db.session() as db_session:
        cleaned_text, citations = await _validate_citations(db_session, paper_id, full_text)
        seq = await _next_seq(db_session, session_ref.conversation_id)
        db_session.add(
            Messages(
                conversation_id=session_ref.conversation_id,
                seq=seq,
                turn_id=turn_id,
                role="assistant",
                content=cleaned_text,
                citations=citations,
            )
        )

    # Sent post-validation, never raw model output (D24) — split on the tag
    # boundaries already computed above so a chunk never splits a tag.
    for piece in re.split(r"(</?(?:cite|unverified)>)", cleaned_text):
        if piece:
            yield TextDeltaEvent(delta=piece)

    yield TurnCompleteEvent(turn_id=turn_id, interrupted=False, iterations=1)


async def interrupt(session_ref: SessionRef) -> None:
    """No-op in Phase 1.5. `run_turn` is awaited synchronously per turn, so
    there is no concurrent `asyncio.Task` yet to cancel — the cancellable
    per-turn task (D18 node 7) lands with Phase 1.8's iteration-cap graceful
    stop, the phase that needs a turn in flight while another arrives."""
