"""Agent Harness — assembles context, calls the LLM, streams typed turn
events, and persists the transcript for one agent turn (MODULES.md, D18).

Phase 1.5 shipped the reader-Q&A path (D19: "Reader Q&A is not a tool — it
is the core agent loop answering from ambient UI-state"). Phase 1.7 added
`query_memory` (D19 Memory) as a lightweight decide-then-retrieve step via
`complete_structured` rather than a full function-calling loop. Bug Fix
Plan Phase 2.3 adds the real thing: a single-agent tool-calling loop
(D18 node 1) with a hard iteration cap and a graceful stop, dispatching
through `harness.tools` (D19's Discovery/Navigation/Mutations groups).

Citations are enforced structurally either way (D24): the model wraps every
quoted claim in `<cite>` tags, and every tag is independently re-verified
before the turn completes — against Provenance for the open paper's text,
or by checking it is a substring of a row `query_memory` actually returned
(D24: "memory recall cites source row ids; verification is trivial — the
row exists"). A tag that resolves neither way is relabelled `<unverified>`,
never trusted from the model's own say-so.

Bug Fix Plan Phase 3.4 adds turn de-duplication against `messages.turn_id`
as defense-in-depth behind Session Transport's connection-layer fix (which
now closes a session's socket the moment it's evicted, so a stray send
from a stale connection can no longer reach `handle_message` at all): if
the *same* user text somehow starts a second turn within
`_DUPLICATE_TURN_WINDOW_S` of the first — e.g. a duplicate `user_message`
that was already in flight over the socket at the instant it was
evicted — `run_turn` recognizes the immediately preceding row as that turn
and returns without persisting a second copy or calling the LLM again.

Bug Fix Plan Phase 1.1 closes the other gap the completion timeout above
never covered: the tool-dispatch branch had neither a timeout nor a
`cancel_flag` check of its own, so a tool call that never returned hung the
turn forever and, because the in-flight slot was only ever released after
that same await, every later turn on the session too. `_dispatch_tool_bounded`
races the dispatch against both a timeout and `cancel_flag`; either ends the
turn with an `error` event and `turn_complete(interrupted=true)` rather than
leaving the status pill stuck, and the slot-releasing `finally` at the
bottom of `run_turn` runs on that path exactly as it does on every other.

Bug Fix Plan Phase 3.1 closes a gap in the citation validator above: a
doubled or quote-wrapped `<cite>` tag can make `_CITE_OR_QUOTE_PATTERN`'s
lazy match pair an opening tag with the wrong closing tag, leaving literal
tag markup stranded inside a capture group or in the untouched text between
matches. `_validate_citations` now strips that markup — from the captured
span and from the text around it — before re-wrapping, so `⚠ unverified`
always renders as clean text, never a real tag nested or stranded inside
another.
"""

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import func, select

import db
import papers
from db.models import Messages
from harness.models import (
    ErrorEvent,
    SessionRef,
    StatusEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    TurnCompleteEvent,
    TurnEvent,
    UIActionEvent,
    UIState,
)
from harness.tools import TOOL_SCHEMAS
from harness.tools import dispatch as dispatch_tool
from llm import LLMError, Message, ToolCall, complete, complete_structured
from memory import query_memory
from memory.models import CitedRow
from provenance import validate_and_anchor

__all__ = ["begin_turn", "run_turn", "interrupt"]

# D18 node 1: "Hard iteration cap (~8-10) with a graceful stop."
_MAX_ITERATIONS = 8

# Matches an explicit `<cite>` span, or a quotation-marked span the model
# left untagged in plain prose — a 20B model routinely writes a fabricated
# "quote" instead of wrapping it, and the prompt's citation discipline is
# advisory only (Bug Fix Plan Phase 2.2), so both are validated the same
# way rather than letting the untagged case pass through untouched. The
# length floor keeps this to sentence-like spans, not every quoted term.
_MIN_UNTAGGED_QUOTE_CHARS = 20
_CITE_OR_QUOTE_PATTERN = re.compile(
    r"<cite>(?P<cite>.*?)</cite>"
    # `[^<>]`, not just excluding the quote char: a model that wraps its
    # own `<cite>` tag in quote marks (`"<cite>...</cite>"`) must not have
    # the quote-span alternative swallow the tag markup as "quoted" text.
    rf'|"(?P<straight>[^"<>]{{{_MIN_UNTAGGED_QUOTE_CHARS},}}?)"'
    rf"|“(?P<curly>[^”<>]{{{_MIN_UNTAGGED_QUOTE_CHARS},}}?)”",
    re.DOTALL,
)

# Every other LLM call in this codebase passes an explicit timeout
# (memory-decision, extraction, scoped-column queries); the primary
# completion is the one exception, and that gap is exactly what let a
# stalled provider connection hang a turn forever, un-cancellable, since
# the cooperative cancel-flag check below only runs *between* yielded
# chunks — a stream that never yields even once can never be interrupted.
# LiteLLM applies this per read, not as a hard cap on total stream
# duration, so a genuinely long reasoning response that keeps producing
# chunks is unaffected.
_COMPLETION_TIMEOUT_S = 90

# Bounds a single tool dispatch (Bug Fix Plan Phase 1.1) — a search API call,
# an LLM extraction, or any other awaited step inside `harness.tools.dispatch`
# that never yields back to `run_turn`'s own cancel-flag check is otherwise
# unbounded, unlike the completion call above which at least streams. Set
# generously above the slowest legitimate tool (a federated literature
# search) so it only fires on a genuinely stuck call.
_TOOL_DISPATCH_TIMEOUT_S = 60

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


# Bounded excerpt from `paper_content.full_text` when a paper has no
# extracted card yet — guaranteed substring-matchable against full_text,
# unlike the separate `papers.abstract` metadata column, so a verbatim
# copy the model makes still validates (D24).
_PAPER_EXCERPT_CHARS = 4000


async def _paper_evidence(session, paper_id: uuid.UUID, title: str) -> str:
    """Deterministic evidence for one paper the turn can cite from — the
    selected paper and every open reader tab always get this, rather than
    depending on `_maybe_retrieve`'s LLM-gated decision (Bug Fix Plan
    Phase 2.1). Prefers the extractive card's validated quotes; falls back
    to a bounded excerpt of the parsed text when extraction has not
    produced one yet."""
    card = await papers.get_paper_card(session, paper_id)
    if card:
        lines = [f'Extracted fields for "{title}" (validated verbatim quotes — cite these exactly to make a claim about this paper):']
        for field in card:
            heading = f" (§{field.section_heading})" if field.section_heading else ""
            lines.append(f'- {field.field_key}{heading}: "{field.value}"')
        return "\n".join(lines)

    content = await papers.get_paper_content(session, paper_id)
    if content is not None and content.full_text:
        excerpt = content.full_text[:_PAPER_EXCERPT_CHARS]
        return f'Excerpt from "{title}" (no extracted fields yet — quote verbatim from this excerpt to cite):\n{excerpt}'

    return f'No extracted text is available yet for "{title}".'


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


# Generous enough to catch a duplicate send that straddled a socket
# eviction (the whole round trip is normally well under a second) without
# ever mistaking a deliberate identical follow-up message — "yes" sent
# twice minutes apart — for a resubmission.
_DUPLICATE_TURN_WINDOW_S = 5.0


async def _duplicate_turn_id(session, conversation_id: uuid.UUID, text: str) -> uuid.UUID | None:
    """The `turn_id` of an already-persisted turn to fold this call into,
    if the conversation's most recent message is a `user` row with this
    exact text, persisted within `_DUPLICATE_TURN_WINDOW_S` — i.e. this
    call is very likely the same turn arriving a second time, not a new
    one. `None` means this is a genuinely new turn."""
    last = (
        await session.scalars(
            select(Messages).where(Messages.conversation_id == conversation_id).order_by(Messages.seq.desc()).limit(1)
        )
    ).first()
    if last is None or last.role != "user" or last.content != text:
        return None
    age_s = (datetime.now(UTC) - last.created_at).total_seconds()
    if age_s > _DUPLICATE_TURN_WINDOW_S:
        return None
    return last.turn_id


def _matching_memory_row(quote: str, memory_rows: list[CitedRow]) -> CitedRow | None:
    return next((row for row in memory_rows if quote in row.text), None)


# A doubled or quote-wrapped `<cite>` tag (Bug Fix Plan Phase 3.1) can make
# `_CITE_OR_QUOTE_PATTERN`'s lazy `.*?` pair an opening tag with the wrong
# closing tag, leaving literal `<cite>`/`</cite>`/`<unverified>`/`</unverified>`
# markup stranded either inside a capture group or in the untouched text
# between matches. Neither location is allowed to reach `messages.citations`
# still carrying that markup — `renderAssistantContent` (Companion Pane)
# splits on the outermost match only and does not recurse into one, so a
# leftover tag prints as visible raw characters instead of being masked
# behind `⚠ unverified`.
_TAG_MARKUP_PATTERN = re.compile(r"</?(?:cite|unverified)>")


def _strip_tag_markup(text: str) -> str:
    """Removes any literal citation-tag markup from `text` — applied to a
    captured span before it is re-wrapped, and to the untouched text between
    matches, so no shape or depth of malformed tag the model emits can leave
    a real `<cite>`/`<unverified>` tag nested or stranded inside the
    validated result."""
    return _TAG_MARKUP_PATTERN.sub("", text)


async def _validate_citations(
    session, paper_ids: list[uuid.UUID], memory_rows: list[CitedRow], text: str
) -> tuple[str, list[dict]]:
    """D24's substring validator, applied to every `<cite>` span *and* every
    untagged quotation-marked span the model produced — against any paper in
    `paper_ids` (the selected paper plus any open reader tabs, so a
    cross-paper claim can resolve against either paper — US4) via
    Provenance, or against a row `query_memory` actually retrieved. Returns
    the text with every span relabelled `<cite>` or `<unverified>` as
    appropriate, plus the structured citation list `messages.citations`
    stores."""
    citations: list[dict] = []
    pieces: list[str] = []
    cursor = 0
    for match in _CITE_OR_QUOTE_PATTERN.finditer(text):
        pieces.append(_strip_tag_markup(text[cursor : match.start()]))
        quote = _strip_tag_markup(match.group("cite") or match.group("straight") or match.group("curly"))
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
    pieces.append(_strip_tag_markup(text[cursor:]))
    return "".join(pieces), citations


class _ToolDispatchInterrupted(Exception):
    """Raised by `_dispatch_tool_bounded` when `cancel_flag` fires before the
    dispatch finishes. Caught only in `run_turn`, right where it's raised —
    it never crosses that boundary, so it adds no second `CancelledError`
    catch site (Rules.md's cancellation-in-one-place rule): the turn's own
    task is never cancelled, only the tool-dispatch coroutine this function
    itself spawned."""


async def _dispatch_tool_bounded(
    session, project_id: uuid.UUID, tool_name: str, args: dict, cancel_flag: asyncio.Event
):
    """Runs `dispatch_tool`, but returns no later than
    `_TOOL_DISPATCH_TIMEOUT_S` after starting, and no later than
    `cancel_flag` firing — whichever comes first. Raises `TimeoutError` on
    the former, `_ToolDispatchInterrupted` on the latter; on either, the
    still-running dispatch coroutine is cancelled and awaited out so it
    never keeps running unobserved after this returns."""
    dispatch_task = asyncio.ensure_future(dispatch_tool(session, project_id, tool_name, args))
    cancel_task = asyncio.ensure_future(cancel_flag.wait())
    try:
        done, _ = await asyncio.wait(
            {dispatch_task, cancel_task}, timeout=_TOOL_DISPATCH_TIMEOUT_S, return_when=asyncio.FIRST_COMPLETED
        )
        if dispatch_task in done:
            return dispatch_task.result()
        if cancel_task in done:
            raise _ToolDispatchInterrupted(f"{tool_name} interrupted")
        raise TimeoutError(f'"{tool_name}" did not respond within {_TOOL_DISPATCH_TIMEOUT_S}s')
    finally:
        for task in (dispatch_task, cancel_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(dispatch_task, cancel_task, return_exceptions=True)


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
            duplicate_turn_id = await _duplicate_turn_id(db_session, session_ref.conversation_id, message)
            if duplicate_turn_id is not None:
                # Defense-in-depth (Bug Fix Plan Phase 3.4): Session
                # Transport now closes a socket the instant it's evicted,
                # which is the primary fix for the double-send this guards
                # against — this is the fallback for a send that got far
                # enough to reach `handle_message` before that close took
                # effect. Neither the user message nor an answer gets
                # persisted a second time; the already-completed turn owns
                # this text.
                yield TurnCompleteEvent(turn_id=duplicate_turn_id, interrupted=False, iterations=0)
                return

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

            # The selected paper plus every open reader tab — the candidate
            # set a `<cite>` span is checked against (US4 cross-paper
            # compare), and the set each `_paper_evidence` block is built
            # for. De-duplicated, order preserved, selected paper first.
            evidence_papers: list[tuple[uuid.UUID, str]] = []
            if paper_id is not None:
                selected_paper = await papers.get_paper(db_session, paper_id)
                if selected_paper is not None:
                    evidence_papers.append((selected_paper.id, selected_paper.title))
            for pid, title in open_papers:
                if pid not in {p[0] for p in evidence_papers}:
                    evidence_papers.append((pid, title))

            evidence_blocks = [await _paper_evidence(db_session, pid, title) for pid, title in evidence_papers]

        citation_paper_ids = [pid for pid, _ in evidence_papers]

        memory_rows = await _maybe_retrieve(session_ref.project_id, message)
        if memory_rows:
            yield StatusEvent(text="searching your project…")

        llm_messages = [Message(role="system", content=_SYSTEM_PROMPT)]
        if ui_state.selection is not None:
            llm_messages.append(
                Message(role="system", content=f'The user has highlighted this passage from the paper:\n"{ui_state.selection.anchor.quote}"')
            )
        if evidence_blocks:
            llm_messages.append(Message(role="system", content="\n\n".join(evidence_blocks)))
        if len(open_papers) > 1:
            llm_messages.append(Message(role="system", content=_format_open_papers(open_papers)))
        if memory_rows:
            llm_messages.append(Message(role="system", content=_format_memory_rows(memory_rows)))
        llm_messages += history
        llm_messages.append(Message(role="user", content=message))

        # D18 node 1: single-agent tool-calling loop, hard iteration cap
        # with a graceful stop — each pass either produces a final answer
        # (no tool call) or dispatches every tool call the model made and
        # feeds the results back for another pass.
        full_text = ""
        interrupted = False
        iterations = 0
        while iterations < _MAX_ITERATIONS:
            iterations += 1
            yield StatusEvent(text="thinking…")
            full_text = ""
            tool_call_acc: dict[int, dict] = {}
            try:
                async for chunk in complete(llm_messages, tools=TOOL_SCHEMAS, tier="primary", timeout=_COMPLETION_TIMEOUT_S):
                    full_text += chunk.delta
                    for tc_delta in chunk.tool_calls:
                        acc = tool_call_acc.setdefault(tc_delta.index, {"id": None, "name": None, "arguments": ""})
                        acc["id"] = tc_delta.id or acc["id"]
                        acc["name"] = tc_delta.name or acc["name"]
                        acc["arguments"] += tc_delta.arguments
                    if cancel_flag.is_set():
                        interrupted = True
                        break
            except LLMError as exc:
                yield ErrorEvent(code=type(exc).__name__, message=str(exc), recoverable=True, what_still_worked="nothing — the turn produced no answer")
                yield TurnCompleteEvent(turn_id=turn_id, interrupted=False, iterations=iterations)
                return
            except RuntimeError as exc:
                yield ErrorEvent(code="not_configured", message=str(exc), recoverable=True, what_still_worked=None)
                yield TurnCompleteEvent(turn_id=turn_id, interrupted=False, iterations=iterations)
                return

            if interrupted or not tool_call_acc:
                break  # a final answer, an interruption, or no tool call requested

            llm_messages.append(
                Message(
                    role="assistant",
                    content=full_text,
                    tool_calls=[
                        ToolCall(id=tc["id"] or str(uuid.uuid4()), name=tc["name"] or "", arguments=tc["arguments"])
                        for tc in tool_call_acc.values()
                    ],
                )
            )
            for tc in tool_call_acc.values():
                tool_name = tc["name"] or ""
                tool_call_id = tc["id"] or str(uuid.uuid4())
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                yield ToolCallEvent(tool_name=tool_name, args=args)
                try:
                    async with db.session() as db_session:
                        seq = await _next_seq(db_session, session_ref.conversation_id)
                        db_session.add(
                            Messages(
                                conversation_id=session_ref.conversation_id,
                                seq=seq,
                                turn_id=turn_id,
                                role="tool_call",
                                content=json.dumps({"name": tool_name, "arguments": args}),
                                tool_name=tool_name,
                            )
                        )
                        result = await _dispatch_tool_bounded(db_session, session_ref.project_id, tool_name, args, cancel_flag)
                        seq = await _next_seq(db_session, session_ref.conversation_id)
                        db_session.add(
                            Messages(
                                conversation_id=session_ref.conversation_id,
                                seq=seq,
                                turn_id=turn_id,
                                role="tool_result",
                                content=result.model_view,
                                tool_name=tool_name,
                                result_id=result.ui_view_result_id,
                            )
                        )
                except TimeoutError as exc:
                    # The whole tool_call/tool_result transaction rolled
                    # back with the timeout (db.session()'s own exception
                    # handling) — the turn ends the same way an interrupt
                    # does, rather than leaving the status pill stuck.
                    yield ErrorEvent(
                        code="tool_timeout", message=str(exc), recoverable=True, what_still_worked="the conversation so far"
                    )
                    yield TurnCompleteEvent(turn_id=turn_id, interrupted=True, iterations=iterations)
                    return
                except _ToolDispatchInterrupted:
                    yield ErrorEvent(
                        code="turn_interrupted",
                        message=f'"{tool_name}" was interrupted before it returned.',
                        recoverable=True,
                        what_still_worked="the conversation so far",
                    )
                    yield TurnCompleteEvent(turn_id=turn_id, interrupted=True, iterations=iterations)
                    return
                yield ToolResultEvent(tool_name=tool_name, model_view=result.model_view, result_id=result.ui_view_result_id)
                for action in result.ui_actions:
                    yield UIActionEvent(action=action.get("action", ""), payload=action)
                llm_messages.append(Message(role="tool", content=result.model_view, tool_call_id=tool_call_id))
        else:
            # Hit the iteration cap without a final answer — a graceful
            # stop (D18 node 1), never an exception.
            if not full_text:
                full_text = "I reached my step limit before finishing this — try asking again, more narrowly."

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

        yield TurnCompleteEvent(turn_id=turn_id, interrupted=interrupted, iterations=iterations, citations=citations)
    finally:
        _in_flight.pop(key, None)


async def interrupt(session_ref: SessionRef) -> None:
    """Sets the in-flight turn's cancel flag, if one is running (D18 node 5:
    first-class interrupt, partial results retained)."""
    flag = _in_flight.get(_turn_key(session_ref))
    if flag is not None:
        flag.set()
