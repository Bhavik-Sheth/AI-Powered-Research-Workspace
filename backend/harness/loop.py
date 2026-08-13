"""The control loop (HarnessPlan H1 — split out of `harness/__init__.py`,
which had grown to 617 lines doing loop, context, citations, dedup and
cancellation at once). This module owns exactly the loop: reserving the
in-flight slot, calling the LLM, dispatching tool calls through the
registry, validating citations, and persisting the transcript. Context
assembly lives in `context.py`; the tool catalog lives in `registry.py` and
`tools/`; turn observability lives in `trace.py`.

Phase 1.5 shipped the reader-Q&A path (D19: "Reader Q&A is not a tool — it
is the core agent loop answering from ambient UI-state"). Phase 1.7 added
`query_memory` (D19 Memory) as a lightweight decide-then-retrieve step via
`complete_structured` rather than a full function-calling loop. Bug Fix
Plan Phase 2.3 adds the real thing: a single-agent tool-calling loop
(D18 node 1) with a hard iteration cap and a graceful stop, dispatching
through the tool registry (D19's Discovery/Navigation/Mutations groups).

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

Bug Fix Plan Phase 3.1 closed a gap in the citation validator above: a
doubled or quote-wrapped `<cite>` tag can make the cite/quote pattern's lazy
match pair an opening tag with the wrong closing tag, leaving literal tag
markup stranded inside a capture group or in the untouched text between
matches. HarnessPlan H6 moved that whole validator — pattern, malformed-tag
stripping and all — into `streaming.py`'s `CitationStream`, ported unchanged
(§3.8): `⚠ unverified` still always renders as clean text, never a real tag
nested or stranded inside another.

HarnessPlan H6 also replaces whole-turn buffering with live streaming: the
old shape accumulated an iteration's entire response, and only after the
loop ended validated it and re-split it into `TextDeltaEvent`s — so the user
watched a status pill for the whole generation. `run_turn` now feeds every
`chunk.delta` into one `CitationStream` per turn as it arrives and yields
each ready-to-emit piece immediately; only a `<cite>`/quoted span still
buffers, for the length of its own validation. The same instance's `.text`
and `.citations` are what gets persisted, so what streamed and what is
stored can never disagree. `.flush()` runs on every exit path so a span left
open by an interrupt, the iteration cap, or an error is force-closed as
`<unverified>` rather than silently dropped (TRD §3: partial results, never
well-formed ones).

HarnessPlan H1 adds a 180s wall-clock bound alongside the existing
iteration cap (§3.1: "a wall-clock bound matters more with a local model
than an iteration bound does — eight iterations of a 30B model on CPU can
run for minutes") and a `turn_traces` row per turn (§3.10).
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

from sqlalchemy import func, select

import db
from db.models import Conversations, Messages
from harness import compaction, context, registry, trace
from harness.models import (
    ErrorEvent,
    Ref,
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
from harness.streaming import CitationStream
from llm import LLMError, Message, ToolCall, complete
from memory.models import CitedRow
from provenance import validate_and_anchor

__all__ = ["begin_turn", "run_turn", "interrupt"]

# D18 node 1: "Hard iteration cap (~8-10) with a graceful stop."
_MAX_ITERATIONS = 8

# HarnessPlan H1, §3.1: a wall-clock bound matters more with a local model
# than an iteration bound does — eight iterations of a 30B model on CPU can
# run for minutes. Same graceful-stop path as the iteration cap.
_WALL_CLOCK_LIMIT_S = 180

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
# an LLM extraction, or any other awaited step inside `registry.dispatch`
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


async def _history(session, conversation_id: uuid.UUID) -> list[Message]:
    """Verbatim `user`/`assistant` turns for `conversation_id`, prefixed
    with the rolling summary (HarnessPlan H4, §3.3) when one exists. Once
    `compaction.maybe_compact` has written `conversations.summary` /
    `summarised_through_seq`, the turns it already covers are no longer
    read back individually — they stay in `messages` untouched, but band 3
    (history) sees the summary in their place plus the un-summarised tail,
    which is what keeps history from growing without bound. This is the one
    place that decision is made; `context.build_blocks` still just receives
    a `history: list[Message]` and does not need to know a summary exists."""
    conversation = await session.get(Conversations, conversation_id)
    watermark = conversation.summarised_through_seq if conversation else None
    query = select(Messages).where(Messages.conversation_id == conversation_id)
    if watermark:
        query = query.where(Messages.seq > watermark)
    rows = (await session.scalars(query.order_by(Messages.seq))).all()
    messages = [Message(role=row.role, content=row.content) for row in rows if row.role in ("user", "assistant")]
    if conversation and conversation.summary:
        summary_message = Message(role="system", content=f"Summary of earlier conversation:\n{conversation.summary}")
        messages = [summary_message, *messages]
    return messages


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


async def _resolve_citation(
    session, paper_ids: list[uuid.UUID], memory_rows: list[CitedRow], quote: str
) -> dict | None:
    """D24's substring check, applied to one already-buffered `<cite>` or
    quoted span: against any paper in `paper_ids` (the selected paper plus
    any open reader tabs, so a cross-paper claim can resolve against either
    paper — US4) via Provenance, else against a row `query_memory` actually
    retrieved. `None` means the quote resolves neither way — the span this
    validates is *what counts as a match*; `CitationStream` owns only the
    buffering and state transitions around calling it."""
    for paper_id in paper_ids:
        anchor = await validate_and_anchor(session, paper_id, quote, "", "")
        if anchor is not None:
            return {"kind": "anchor", "anchor_id": str(anchor.id), "quote": quote}
    memory_row = _matching_memory_row(quote, memory_rows)
    if memory_row is not None:
        return {"kind": "memory", "row_id": str(memory_row.id), "source_type": memory_row.source_type, "quote": quote}
    return None


class _ToolDispatchInterrupted(Exception):
    """Raised by `_dispatch_tool_bounded` when `cancel_flag` fires before the
    dispatch finishes. Caught only in `run_turn`, right where it's raised —
    it never crosses that boundary, so it adds no second `CancelledError`
    catch site (Rules.md's cancellation-in-one-place rule): the turn's own
    task is never cancelled, only the tool-dispatch coroutine this function
    itself spawned."""


async def _dispatch_tool_bounded(ctx: registry.ToolContext, tool_name: str, args: dict, cancel_flag: asyncio.Event):
    """Runs `registry.dispatch`, but returns no later than
    `_TOOL_DISPATCH_TIMEOUT_S` after starting, and no later than
    `cancel_flag` firing — whichever comes first. Raises `TimeoutError` on
    the former, `_ToolDispatchInterrupted` on the latter; on either, the
    still-running dispatch coroutine is cancelled and awaited out so it
    never keeps running unobserved after this returns."""
    dispatch_task = asyncio.ensure_future(registry.dispatch(ctx, tool_name, args))
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
    live_ui_state: Callable[[], UIState] | None = None,
) -> AsyncIterator[TurnEvent]:
    """`cancel_flag` is normally the `Event` `begin_turn` already reserved
    (the caller closes the race against `interrupt`); passed as `None` only
    by tests/direct callers, in which case this reserves its own.

    `live_ui_state`, when given, is called at the top of every loop
    iteration to re-read the session's current UI state (HarnessPlan H3,
    §3.2) — a running turn otherwise captured its own copy at the start and
    a `ui_state` update sent mid-turn only ever reached the *next* turn.
    `None` (tests/direct callers) means the turn's `ui_state` stays fixed
    for its duration, exactly as before H3."""
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

    turn_started_at = time.monotonic()
    trace_tool_calls: list[dict] = []
    trace_model: str | None = None
    trace_prompt_tokens: int | None = None
    trace_completion_tokens: int | None = None

    try:
        turn_id = uuid.uuid4()

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
            open_papers, evidence_blocks_text, citation_paper_ids = await context.evidence_for(db_session, ui_state)

        # The signature of the paper set the current `evidence_blocks_text`
        # was built for — recomputed only when this changes (HarnessPlan
        # H3, §3.2: "opening a tab mid-turn does not cost a re-read of
        # every paper").
        evidence_signature = frozenset(citation_paper_ids)

        await trace.start(turn_id, session_ref.conversation_id, session_ref.project_id)

        memory_rows = await context.maybe_retrieve(session_ref.project_id, message)
        if memory_rows:
            yield StatusEvent(text="searching your project…")

        # `Ref`s a tool result has surfaced this turn, merged with whatever
        # the frontend already had in `ui_state.working_set` — band 2
        # (§3.2), rebuilt fresh each iteration alongside everything else.
        seen_refs: list[Ref] = []
        # Assistant/tool messages this turn's loop has produced so far —
        # appended after the freshly-assembled context each iteration,
        # never itself evicted (it is the in-progress tool exchange the
        # next completion call must see in full).
        turn_exchange: list[Message] = []
        context_totals: dict[str, int] = {}

        async def _validate_span(quote: str) -> dict | None:
            # Closes over `citation_paper_ids`/`memory_rows` by reference,
            # not value — both are reassigned in place as the turn
            # progresses (open-paper set changes, retrieval happens once
            # up front), and Python's late-binding closures see whatever
            # the enclosing scope holds at call time, so this always
            # validates against the turn's current state.
            async with db.session() as validate_session:
                return await _resolve_citation(validate_session, citation_paper_ids, memory_rows, quote)

        # HarnessPlan H6 (§3.8): one `CitationStream` for the whole turn,
        # not one per iteration — every iteration's deltas feed the same
        # machine and stream live as they arrive, so `.text`/`.citations`
        # at the end is exactly what was shown, whichever iteration turned
        # out to be the final answer.
        citation_stream = CitationStream(_validate_span)

        # D18 node 1: single-agent tool-calling loop, hard iteration cap
        # with a graceful stop — each pass either produces a final answer
        # (no tool call) or dispatches every tool call the model made and
        # feeds the results back for another pass. HarnessPlan H1 adds a
        # wall-clock bound alongside the iteration cap (§3.1) — both share
        # the same graceful-stop message below.
        full_text = ""
        interrupted = False
        iterations = 0
        while iterations < _MAX_ITERATIONS and (time.monotonic() - turn_started_at) < _WALL_CLOCK_LIMIT_S:
            iterations += 1

            if live_ui_state is not None:
                ui_state = live_ui_state()

            # HarnessPlan H4, §3.3: compaction is a window operation that
            # only ever runs between iterations, never mid-stream — right
            # here, alongside the other per-iteration re-reads, before this
            # iteration's context is assembled. `messages` rows are never
            # touched; only what `_history` returns for this conversation
            # changes once a new summary lands, hence the re-read.
            if await compaction.maybe_compact(session_ref.conversation_id, history):
                async with db.session() as db_session:
                    history = await _history(db_session, session_ref.conversation_id)

            selected_id = ui_state.selection.paper_id if ui_state.selection is not None else None
            current_signature = frozenset([selected_id, *ui_state.open_paper_ids]) - {None}
            if current_signature != evidence_signature:
                async with db.session() as db_session:
                    open_papers, evidence_blocks_text, citation_paper_ids = await context.evidence_for(db_session, ui_state)
                evidence_signature = frozenset(citation_paper_ids)

            working_set_refs = [*ui_state.working_set, *seen_refs]
            blocks = context.build_blocks(ui_state, evidence_blocks_text, open_papers, memory_rows, history, working_set_refs, message)
            assembled_messages, context_totals = context.assemble(blocks)
            llm_messages = assembled_messages + turn_exchange

            yield StatusEvent(text="thinking…")
            full_text = ""
            tool_call_acc: dict[int, dict] = {}
            try:
                async for chunk in complete(llm_messages, tools=registry.core_schemas(), tier="primary", timeout=_COMPLETION_TIMEOUT_S):
                    full_text += chunk.delta
                    # HarnessPlan H6: streamed live, not buffered until the
                    # iteration ends — the state machine only holds back a
                    # `<cite>`/quoted span, for the length of its own
                    # validation.
                    for piece in await citation_stream.feed(chunk.delta):
                        yield TextDeltaEvent(delta=piece)
                    for tc_delta in chunk.tool_calls:
                        acc = tool_call_acc.setdefault(tc_delta.index, {"id": None, "name": None, "arguments": ""})
                        acc["id"] = tc_delta.id or acc["id"]
                        acc["name"] = tc_delta.name or acc["name"]
                        acc["arguments"] += tc_delta.arguments
                    if chunk.usage is not None:
                        trace_prompt_tokens = chunk.usage.prompt_tokens
                        trace_completion_tokens = chunk.usage.completion_tokens
                        trace_model = chunk.model
                    if cancel_flag.is_set():
                        interrupted = True
                        break
            except LLMError as exc:
                # Whatever text this iteration streamed before the error is
                # not rolled back (Rules.md) — flush any span it left open
                # so the last thing on screen is `<unverified>`, never a
                # dangling delimiter.
                tail = await citation_stream.flush()
                if tail:
                    yield TextDeltaEvent(delta=tail)
                yield ErrorEvent(code=type(exc).__name__, message=str(exc), recoverable=True, what_still_worked="nothing — the turn produced no answer")
                yield TurnCompleteEvent(turn_id=turn_id, interrupted=False, iterations=iterations)
                await trace.finish(
                    turn_id, status="failed", iterations=iterations, total_ms=int((time.monotonic() - turn_started_at) * 1000),
                    error_code=type(exc).__name__, tool_calls=trace_tool_calls,
                )
                return
            except RuntimeError as exc:
                tail = await citation_stream.flush()
                if tail:
                    yield TextDeltaEvent(delta=tail)
                yield ErrorEvent(code="not_configured", message=str(exc), recoverable=True, what_still_worked=None)
                yield TurnCompleteEvent(turn_id=turn_id, interrupted=False, iterations=iterations)
                await trace.finish(
                    turn_id, status="failed", iterations=iterations, total_ms=int((time.monotonic() - turn_started_at) * 1000),
                    error_code="not_configured", tool_calls=trace_tool_calls,
                )
                return

            if interrupted or not tool_call_acc:
                break  # a final answer, an interruption, or no tool call requested

            turn_exchange.append(
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
                tool_started_at = time.monotonic()
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
                        ctx = registry.ToolContext(session=db_session, project_id=session_ref.project_id)
                        result = await _dispatch_tool_bounded(ctx, tool_name, args, cancel_flag)
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
                    trace_tool_calls.append({"name": tool_name, "ms": int((time.monotonic() - tool_started_at) * 1000), "ok": False, "denied": False, "budget_blocked": False})
                    tail = await citation_stream.flush()
                    if tail:
                        yield TextDeltaEvent(delta=tail)
                    yield ErrorEvent(
                        code="tool_timeout", message=str(exc), recoverable=True, what_still_worked="the conversation so far"
                    )
                    yield TurnCompleteEvent(turn_id=turn_id, interrupted=True, iterations=iterations)
                    await trace.finish(
                        turn_id, status="failed", iterations=iterations, total_ms=int((time.monotonic() - turn_started_at) * 1000),
                        error_code="tool_timeout", tool_calls=trace_tool_calls,
                    )
                    return
                except _ToolDispatchInterrupted:
                    trace_tool_calls.append({"name": tool_name, "ms": int((time.monotonic() - tool_started_at) * 1000), "ok": False, "denied": False, "budget_blocked": False})
                    tail = await citation_stream.flush()
                    if tail:
                        yield TextDeltaEvent(delta=tail)
                    yield ErrorEvent(
                        code="turn_interrupted",
                        message=f'"{tool_name}" was interrupted before it returned.',
                        recoverable=True,
                        what_still_worked="the conversation so far",
                    )
                    yield TurnCompleteEvent(turn_id=turn_id, interrupted=True, iterations=iterations)
                    await trace.finish(
                        turn_id, status="interrupted", iterations=iterations, total_ms=int((time.monotonic() - turn_started_at) * 1000),
                        tool_calls=trace_tool_calls,
                    )
                    return
                trace_tool_calls.append({"name": tool_name, "ms": int((time.monotonic() - tool_started_at) * 1000), "ok": True, "denied": False, "budget_blocked": False})
                yield ToolResultEvent(tool_name=tool_name, model_view=result.model_view, result_id=result.ui_view_result_id)
                for action in result.ui_actions:
                    yield UIActionEvent(action=action.get("action", ""), payload=action)
                seen_refs.extend(result.refs)
                turn_exchange.append(Message(role="tool", content=result.model_view, tool_call_id=tool_call_id))
        else:
            # Hit the iteration cap or the wall-clock bound without a final
            # answer — a graceful stop (D18 node 1), never an exception.
            if not full_text:
                full_text = "I reached my step limit before finishing this — try asking again, more narrowly."
                for piece in await citation_stream.feed(full_text):
                    yield TextDeltaEvent(delta=piece)

        # HarnessPlan H6 (§3.8): force-close whatever span the final
        # iteration left open — normal completion, an interrupt, or the
        # iteration cap all land here the same way. Partial results, never
        # well-formed ones (TRD §3).
        tail = await citation_stream.flush()
        if tail:
            yield TextDeltaEvent(delta=tail)

        cleaned_text, citations = citation_stream.text, citation_stream.citations

        async with db.session() as db_session:
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

        trace_citations = {"validated": len(citations), "unverified": cleaned_text.count("<unverified>")}

        yield TurnCompleteEvent(turn_id=turn_id, interrupted=interrupted, iterations=iterations, citations=citations)
        await trace.finish(
            turn_id,
            status="interrupted" if interrupted else "complete",
            iterations=iterations,
            total_ms=int((time.monotonic() - turn_started_at) * 1000),
            prompt_tokens=trace_prompt_tokens,
            completion_tokens=trace_completion_tokens,
            model=trace_model,
            tool_calls=trace_tool_calls,
            citations=trace_citations,
            context_blocks=context_totals,
        )
    finally:
        _in_flight.pop(key, None)


async def interrupt(session_ref: SessionRef) -> None:
    """Sets the in-flight turn's cancel flag, if one is running (D18 node 5:
    first-class interrupt, partial results retained)."""
    flag = _in_flight.get(_turn_key(session_ref))
    if flag is not None:
        flag.set()
