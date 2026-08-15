"""Orphaned-turn reconstruction (HarnessPlan H11, §3.11) — runs once per
session connect (`ws.handle_connect`) and reconciles every `turn_traces` row
still `status='running'` for that project so a crashed process never leaves
the status pill stuck.

**Scope, deliberately narrower than "resume the loop":** `run_turn`
(`harness/loop.py`) is deeply coupled to a live `cancel_flag`, a
`live_ui_state` callback and a WebSocket session actively streaming events
to a connected client. A turn orphaned by a crash has none of that — its
original caller is gone, and there is no client waiting for
`TextDeltaEvent`s from a completion that started sessions ago. Resurrecting
a live generator to stream to nobody is not "resume", it is a fiction. What
this module actually does, matching §3.11's own words — "a turn that cannot
be rebuilt is marked `interrupted` ... so the status pill unsticks either
way" — is:

1. **Always** close the turn out: write a real `assistant` `messages` row
   (honest, `interrupted=True`, empty citations) and flip `turn_traces.status`
   to `interrupted`. This is the guaranteed outcome — it is what "unsticks
   the pill" for every orphan, rebuildable or not.
2. **Where cheap and safe**, improve on that before closing: an `auto`-tier
   tool call with a `tool_call` row but no `tool_result` row is re-dispatched
   first, because `registry.dispatch` runs on the *same* `db_session` as the
   `tool_result` row it writes (`loop.py`) — no `tool_result` row means that
   whole transaction, including whatever the tool itself mutated, already
   rolled back, so running it again is genuinely safe, not a guess. The
   turn still ends here; a second real completion call to produce an actual
   final answer is out of scope (no live loop exists to keep going with one).

A `confirm`-tier tool call is never auto-replayed (D31 — the consent gate is
never bypassed). The plan's pseudocode says a confirm-tier orphan should
"re-emit approval_request", but that event only ever exists inside an active
`run_turn` generator with somewhere to go — `sweep_orphans` runs at connect
time, before the human is even attached to a turn, so there is no generator
to yield it through and no one to receive it. The resolution here is to
close the turn out with a note naming the pending action and asking the user
to re-ask for it if they still want it done.
"""

import json
import uuid
from typing import Literal

from sqlalchemy import select

import db
from db.models import Messages, TurnTraces
from harness import registry, trace

__all__ = ["sweep_orphans"]

_INTERRUPTED_NOTE = "This response was interrupted before it finished — the conversation continues from here."

_PENDING_CONFIRM_NOTE = (
    'This response was interrupted before finishing, and a pending action ("{tool_name}") that needed your '
    "approval was not completed — re-ask if you still want it done."
)


def _last_tool_call_and_result(messages: list) -> tuple:
    """Pure: given a turn's `messages` rows in `seq` order, returns
    `(last_tool_call_row, tool_result_row)` — the last `tool_call` row and
    the `tool_result` row that resolves it (a later-`seq` `tool_result` row
    for the same turn), or `None` for either that is absent. Takes anything
    with `.role`/`.seq` attributes (a `Messages` row, or a stand-in for
    tests) — it never touches the database, which is the whole reason this
    is split out of `_resolve_orphan`."""
    tool_calls = [row for row in messages if row.role == "tool_call"]
    if not tool_calls:
        return None, None
    last_call = tool_calls[-1]
    result = next((row for row in messages if row.role == "tool_result" and row.seq > last_call.seq), None)
    return last_call, result


def _resolution_for(tool_call_row, tool_result_row, tool_spec) -> Literal["redispatch", "close_interrupted"]:
    """Pure decision at the heart of the sweep: given the turn's last
    `tool_call` row (or `None`), whether a `tool_result` row already resolves
    it, and that tool's registry spec (or `None` for an unrecognised name),
    decides how `_resolve_orphan` finishes this turn. Never touches the
    database or the registry's mutable state — takes the spec as a plain
    argument — which is what makes it directly unit-testable."""
    if tool_call_row is None or tool_result_row is not None:
        # No unresolved tool_call: either none was ever made, or the last
        # one already has a persisted result. Either way the model's own
        # completion is what never finished — not safely re-driveable
        # without a live LLM call, which is out of scope here.
        return "close_interrupted"
    if tool_spec is not None and tool_spec.tier == "auto":
        return "redispatch"
    # tier == "confirm", or a tool name that no longer resolves in the
    # registry — never auto-replayed either way (D31).
    return "close_interrupted"


async def _next_seq(session, conversation_id: uuid.UUID) -> int:
    from sqlalchemy import func

    last = await session.scalar(select(func.max(Messages.seq)).where(Messages.conversation_id == conversation_id))
    return (last or 0) + 1


async def _redispatch(turn_id: uuid.UUID, conversation_id: uuid.UUID, project_id: uuid.UUID, tool_call_row) -> str:
    """Re-runs an `auto`-tier tool call that has a persisted `tool_call` row
    but no `tool_result` row, and writes the real `tool_result` row for it —
    in the *same* `db.session()` block, exactly the atomicity `loop.py`'s
    own tool-dispatch loop relies on. Returns the close-out note for the
    assistant message `_resolve_orphan` writes next."""
    try:
        parsed = json.loads(tool_call_row.content)
    except json.JSONDecodeError:
        parsed = {}
    args = parsed.get("arguments", {}) if isinstance(parsed, dict) else {}
    tool_name = tool_call_row.tool_name or ""
    async with db.session() as session:
        ctx = registry.ToolContext(session=session, project_id=project_id)
        result = await registry.dispatch(ctx, tool_name, args)
        seq = await _next_seq(session, conversation_id)
        session.add(
            Messages(
                conversation_id=conversation_id,
                seq=seq,
                turn_id=turn_id,
                role="tool_result",
                content=result.model_view,
                tool_name=tool_name,
                result_id=result.ui_view_result_id,
            )
        )
    return (
        f'"{tool_name}" finished after an interruption caused the original request to end early: '
        f"{result.model_view} The conversation continues from here."
    )


async def _resolve_orphan(turn_id: uuid.UUID) -> None:
    """Closes out one orphaned turn: reads its `turn_traces` row and
    transcript, decides via `_resolution_for`, re-dispatches when that's
    `redispatch`, then always writes the closing `assistant` message and
    flips `turn_traces.status` to `interrupted`."""
    async with db.session() as session:
        trace_row = await session.get(TurnTraces, turn_id)
        if trace_row is None or trace_row.status != "running":
            return  # already resolved — a real completion landed, or a concurrent sweep got here first
        conversation_id = trace_row.conversation_id
        project_id = trace_row.project_id
        rows = (await session.scalars(select(Messages).where(Messages.turn_id == turn_id).order_by(Messages.seq))).all()

    tool_call_row, tool_result_row = _last_tool_call_and_result(rows)
    tool_spec = registry.get(tool_call_row.tool_name) if tool_call_row is not None else None
    resolution = _resolution_for(tool_call_row, tool_result_row, tool_spec)

    if resolution == "redispatch":
        note = await _redispatch(turn_id, conversation_id, project_id, tool_call_row)
    elif tool_call_row is not None and tool_result_row is None:
        # confirm-tier, or an unrecognised tool name — never auto-replayed.
        note = _PENDING_CONFIRM_NOTE.format(tool_name=tool_call_row.tool_name)
    else:
        note = _INTERRUPTED_NOTE

    async with db.session() as session:
        seq = await _next_seq(session, conversation_id)
        session.add(
            Messages(
                conversation_id=conversation_id,
                seq=seq,
                turn_id=turn_id,
                role="assistant",
                content=note,
                citations=[],
                interrupted=True,
            )
        )
    await trace.finish(turn_id, status="interrupted", iterations=0, total_ms=0, tool_calls=[], skill=None)


async def sweep_orphans(project_id: uuid.UUID) -> None:
    """Called once per session connect (`ws.handle_connect`), scoped to that
    connect's `project_id` (HarnessPlan H11, §3.11's "on session connect" —
    a stuck turn in a project nobody has reconnected to yet stays stuck
    until someone does, an acceptable trade-off the plan's own wording
    accepts literally). Finds every `turn_traces` row still `status='running'`
    for this project with no `assistant`-role `messages` row sharing its
    `turn_id`, and resolves each one via `_resolve_orphan`."""
    async with db.session() as session:
        has_assistant_row = (
            select(Messages.id).where(Messages.turn_id == TurnTraces.turn_id, Messages.role == "assistant").exists()
        )
        turn_ids = (
            await session.scalars(
                select(TurnTraces.turn_id).where(
                    TurnTraces.project_id == project_id,
                    TurnTraces.status == "running",
                    ~has_assistant_row,
                )
            )
        ).all()
    for turn_id in turn_ids:
        await _resolve_orphan(turn_id)
