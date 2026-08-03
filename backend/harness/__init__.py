"""Agent Harness — assembles context, calls the LLM, streams typed turn
events, and persists the transcript for one agent turn (MODULES.md, D18).

Phase 1.5 shipped the reader-Q&A path (D19: "Reader Q&A is not a tool — it
is the core agent loop answering from ambient UI-state"). Phase 1.7 adds
the first real tool, `query_memory` (D19 Memory), wired as a lightweight
decide-then-retrieve step via `complete_structured` rather than a full
streamed function-calling loop — with exactly one tool and a query-only
contract, a structured yes/no-plus-query decision is the same capability
with far less machinery, and the iteration cap this would otherwise need
lands with Phase 1.8 once a second tool actually requires looping.

Citations are enforced structurally either way (D24): the model wraps every
quoted claim in `<cite>` tags, and every tag is independently re-verified
before the turn completes — against Provenance for the open paper's text,
or by checking it is a substring of a row `query_memory` actually returned
(D24: "memory recall cites source row ids; verification is trivial — the
row exists"). A tag that resolves neither way is relabelled `<unverified>`,
never trusted from the model's own say-so.
"""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator

from pydantic import BaseModel
from sqlalchemy import func, select

import db
import papers
from db.models import Messages
from harness.models import ErrorEvent, SessionRef, StatusEvent, TextDeltaEvent, TurnCompleteEvent, TurnEvent, UIState
from llm import LLMError, Message, complete, complete_structured
from memory import query_memory
from memory.models import CitedRow
from provenance import validate_and_anchor

__all__ = ["begin_turn", "run_turn", "interrupt"]

_CITE_PATTERN = re.compile(r"<cite>(.*?)</cite>", re.DOTALL)

# One in-flight turn per session (MODULES.md's Agent Harness state note).
# Cancellation is cooperative rather than a real `asyncio.Task.cancel()`
# handed across the transport boundary: `run_turn` checks the flag between
# streamed chunks, which arrive often enough that a check-and-stop reads as
# immediate to the user, without Session Transport ever needing a task
# reference (it "must not know" harness internals — MODULES.md).
_in_flight: dict[tuple[uuid.UUID, uuid.UUID], asyncio.Event] = {}


def _turn_key(session_ref: SessionRef) -> tuple[uuid.UUID, uuid.UUID]:
    return (session_ref.project_id, session_ref.conversation_id)


def begin_turn(session_ref: SessionRef) -> asyncio.Event | None:
    """Reserves the in-flight slot *synchronously*, in the same call stack
    as the `user_message` that starts a turn — before Session Transport
    schedules `run_turn` as a task. `asyncio.create_task` only schedules a
    coroutine, it does not run any of it; without this, an `interrupt`
    arriving in the same instant could reach `_in_flight` before the task
    ever gets a turn on the event loop and find nothing there, silently
    no-op. Returns `None` if a turn is already in flight for this session."""
    key = _turn_key(session_ref)
    if key in _in_flight:
        return None
    cancel_flag = asyncio.Event()
    _in_flight[key] = cancel_flag
    return cancel_flag

_SYSTEM_PROMPT = (
    "You are the Research Companion, helping a researcher with their project. "
    "Wrap every verbatim quote or specific fact drawn from the paper, a retrieved note, or a "
    "retrieved past conversation in <cite></cite> tags, copying the source's exact wording "
    "inside the tags. Put your own reasoning and commentary outside the tags. Make no factual "
    "claim from a source without a supporting quote."
)


class _MemoryDecision(BaseModel):
    use_memory: bool
    query: str = ""


_MEMORY_DECISION_PROMPT = (
    "Decide whether answering the user's message needs searching the project's own notes, "
    "papers, and past conversations (query_memory). Say yes only if the conversation so far and "
    "any highlighted passage are not already enough to answer. If yes, give a short search query."
)


async def _maybe_retrieve(project_id: uuid.UUID, message: str) -> list[CitedRow]:
    try:
        decision = await complete_structured(
            messages=[Message(role="system", content=_MEMORY_DECISION_PROMPT), Message(role="user", content=message)],
            schema=_MemoryDecision,
            tier="auxiliary",
            timeout=20,
        )
    except (*LLMError, RuntimeError):
        return []  # No memory context beats failing the whole turn over an optional step.
    if not decision.use_memory or not decision.query:
        return []
    return await query_memory(project_id, decision.query)


async def _open_papers(session, open_paper_ids: list[uuid.UUID]) -> list[tuple[uuid.UUID, str]]:
    """Titles of the papers currently open as reader tabs — the read set a
    cross-paper claim (US4) is allowed to cite. A paper id that no longer
    resolves (closed/deleted mid-turn) is silently dropped rather than
    failing the turn."""
    out: list[tuple[uuid.UUID, str]] = []
    for paper_id in open_paper_ids:
        paper = await papers.get_paper(session, paper_id)
        if paper is not None:
            out.append((paper.id, paper.title))
    return out


def _format_open_papers(open_papers: list[tuple[uuid.UUID, str]]) -> str:
    lines = ["Papers currently open in the reader (the read set for cross-paper comparison):"]
    for paper_id, title in open_papers:
        lines.append(f"- {title} (paper_id {paper_id})")
    lines.append(
        "A claim comparing two papers must cite a verbatim quote from each of them. If asked to "
        "compare against a paper that is not in this list, say explicitly that the paper is not "
        "open in this project rather than answering from training knowledge."
    )
    return "\n".join(lines)


def _format_memory_rows(rows: list[CitedRow]) -> str:
    lines = ["Retrieved from the project's memory:"]
    for row in rows:
        label = f"§{row.section_heading}" if row.section_heading else row.source_type
        lines.append(f'- ({label}) "{row.text}"')
    return "\n".join(lines)


async def _history(session, conversation_id: uuid.UUID) -> list[Message]:
    rows = (
        await session.scalars(select(Messages).where(Messages.conversation_id == conversation_id).order_by(Messages.seq))
    ).all()
    return [Message(role=row.role, content=row.content) for row in rows if row.role in ("user", "assistant")]


async def _next_seq(session, conversation_id: uuid.UUID) -> int:
    last = await session.scalar(select(func.max(Messages.seq)).where(Messages.conversation_id == conversation_id))
    return (last or 0) + 1


def _matching_memory_row(quote: str, memory_rows: list[CitedRow]) -> CitedRow | None:
    return next((row for row in memory_rows if quote in row.text), None)


async def _validate_citations(
    session, paper_ids: list[uuid.UUID], memory_rows: list[CitedRow], text: str
) -> tuple[str, list[dict]]:
    """D24's substring validator, applied to every `<cite>` span the model
    produced — against any paper in `paper_ids` (the selected paper plus any
    open reader tabs, so a cross-paper claim can resolve against either
    paper — US4) via Provenance, or against a row `query_memory` actually
    retrieved. Returns the text with failed tags relabelled `<unverified>`,
    plus the structured citation list `messages.citations` stores."""
    citations: list[dict] = []
    pieces: list[str] = []
    cursor = 0
    for match in _CITE_PATTERN.finditer(text):
        pieces.append(text[cursor : match.start()])
        quote = match.group(1)
        anchor = None
        for paper_id in paper_ids:
            anchor = await validate_and_anchor(session, paper_id, quote, "", "")
            if anchor is not None:
                break
        memory_row = None if anchor is not None else _matching_memory_row(quote, memory_rows)
        if anchor is not None:
            citations.append({"kind": "anchor", "anchor_id": str(anchor.id), "quote": quote})
            pieces.append(f"<cite>{quote}</cite>")
        elif memory_row is not None:
            citations.append({"kind": "memory", "row_id": str(memory_row.id), "source_type": memory_row.source_type, "quote": quote})
            pieces.append(f"<cite>{quote}</cite>")
        else:
            pieces.append(f"<unverified>{quote}</unverified>")
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces), citations


async def run_turn(
    session_ref: SessionRef,
    message: str,
    ui_state: UIState,
    input_modality: str = "text",
    cancel_flag: asyncio.Event | None = None,
) -> AsyncIterator[TurnEvent]:
    """`cancel_flag` is normally the `Event` `begin_turn` already reserved
    (the caller closes the race against `interrupt`); passed as `None` only
    by tests/direct callers, in which case this reserves its own."""
    key = _turn_key(session_ref)
    if cancel_flag is None:
        cancel_flag = begin_turn(session_ref)
    if cancel_flag is None:
        yield ErrorEvent(
            code="turn_in_progress",
            message="A turn is already running for this session — interrupt it first.",
            recoverable=True,
            what_still_worked="the turn already in progress",
        )
        return

    try:
        turn_id = uuid.uuid4()
        paper_id = ui_state.selection.paper_id if ui_state.selection is not None else None

        async with db.session() as db_session:
            history = await _history(db_session, session_ref.conversation_id)
            seq = await _next_seq(db_session, session_ref.conversation_id)
            db_session.add(
                Messages(
                    conversation_id=session_ref.conversation_id,
                    seq=seq,
                    turn_id=turn_id,
                    role="user",
                    content=message,
                    citations=[],
                    input_modality=input_modality,
                )
            )
            open_papers = await _open_papers(db_session, ui_state.open_paper_ids)

        # The selected paper plus every open reader tab — the candidate set
        # a `<cite>` span is checked against (US4 cross-paper compare).
        # De-duplicated, order preserved, so the selected paper is tried first.
        citation_paper_ids: list[uuid.UUID] = []
        for pid in ([paper_id] if paper_id is not None else []) + [p[0] for p in open_papers]:
            if pid not in citation_paper_ids:
                citation_paper_ids.append(pid)

        memory_rows = await _maybe_retrieve(session_ref.project_id, message)
        if memory_rows:
            yield StatusEvent(text="searching your project…")

        llm_messages = [Message(role="system", content=_SYSTEM_PROMPT)]
        if ui_state.selection is not None:
            llm_messages.append(
                Message(role="system", content=f'The user has highlighted this passage from the paper:\n"{ui_state.selection.anchor.quote}"')
            )
        if len(open_papers) > 1:
            llm_messages.append(Message(role="system", content=_format_open_papers(open_papers)))
        if memory_rows:
            llm_messages.append(Message(role="system", content=_format_memory_rows(memory_rows)))
        llm_messages += history
        llm_messages.append(Message(role="user", content=message))

        yield StatusEvent(text="thinking…")

        full_text = ""
        interrupted = False
        try:
            async for chunk in complete(llm_messages, tier="primary"):
                full_text += chunk.delta
                if cancel_flag.is_set():
                    interrupted = True
                    break
        except LLMError as exc:
            yield ErrorEvent(code=type(exc).__name__, message=str(exc), recoverable=True, what_still_worked="nothing — the turn produced no answer")
            yield TurnCompleteEvent(turn_id=turn_id, interrupted=False, iterations=1)
            return
        except RuntimeError as exc:
            yield ErrorEvent(code="not_configured", message=str(exc), recoverable=True, what_still_worked=None)
            yield TurnCompleteEvent(turn_id=turn_id, interrupted=False, iterations=1)
            return

        async with db.session() as db_session:
            cleaned_text, citations = await _validate_citations(db_session, citation_paper_ids, memory_rows, full_text)
            seq = await _next_seq(db_session, session_ref.conversation_id)
            db_session.add(
                Messages(
                    conversation_id=session_ref.conversation_id,
                    seq=seq,
                    turn_id=turn_id,
                    role="assistant",
                    content=cleaned_text,
                    citations=citations,
                    interrupted=interrupted,
                )
            )

        # Sent post-validation, never raw model output (D24) — split on the
        # tag boundaries already computed above so a chunk never splits one.
        # An interrupted turn's trailing unclosed tag simply won't match and
        # passes through as literal text — partial results, not well-formed
        # ones, is the only guarantee (TRD §3).
        for piece in re.split(r"(</?(?:cite|unverified)>)", cleaned_text):
            if piece:
                yield TextDeltaEvent(delta=piece)

        yield TurnCompleteEvent(turn_id=turn_id, interrupted=interrupted, iterations=1)
    finally:
        _in_flight.pop(key, None)


async def interrupt(session_ref: SessionRef) -> None:
    """Sets the in-flight turn's cancel flag, if one is running (D18 node 5:
    first-class interrupt, partial results retained)."""
    flag = _in_flight.get(_turn_key(session_ref))
    if flag is not None:
        flag.set()
