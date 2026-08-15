"""Subagents (HarnessPlan H9, §3.7) — a query-only, fresh-context mini-loop
a tool handler can delegate to, distinct from `loop.py`'s `run_turn` on
purpose: a subagent never streams prose to the user, never validates
`<cite>` spans (it returns rows, not prose claims — D24's validator has
nothing to check against text nobody will render), and never persists to
`messages` (it is not a turn in the conversation's own transcript). Reusing
`run_turn` itself would mean threading "skip all of that" flags through code
that is already deeply coupled to WS streaming and citation validation —
cheaper, and clearer to read, to give a subagent its own small loop that
only does what it needs.

**Fresh context, always.** `run_subagent` builds exactly two messages — a
system message from `spec.system_prompt`, a user message that is the task
string handed in — and nothing else. It does not see the parent
conversation's history, on purpose (§3.7: "does not inherit conversation
history — that is the whole point").

**Cannot mutate.** `SubagentSpec.tools` is validated at construction, not at
call time: every name must resolve via `registry.get()` to a `kind="query"`
tool, or the `BaseModel` never comes into existence. This is the mechanism
the plan calls for ("registry enforces every one is kind=query... fails at
import") — a `field_validator` is Pydantic's own "fails at construction"
hook, and construction is as early as a dynamically-built spec (built fresh
per `deep_research` call, since `max_sources` varies per call) can be
checked. The dispatch loop below re-checks `kind` a second time anyway,
defensively — a spec's own tool list is trustworthy, but a model calling a
tool name it invented from context is not, the same "never trust the
model's own say-so" reasoning `registry.dispatch` already applies to every
other tool call.

**Status upward only, tagged.** `status_callback`, when given, is wrapped so
every call — whether made by this loop directly or by a dispatched tool via
its own `ToolContext.status_callback` — is prefixed with `spec.name`, so a
parent turn juggling several subagent calls (today, at most one at a time,
but the mechanism does not assume that) can tell which one is talking.

**Traced like a turn, off the parent's own trace row.** `run_subagent`'s
signature carries `parent_turn_id`, not `conversation_id` — the plan fixes
that signature (§3.7), and a subagent's `turn_traces` row still needs a
`conversation_id` (the column is `NOT NULL`, `db/models.py`'s `TurnTraces`).
The parent turn's own row already carries it, written by `trace.start` at
the top of `run_turn` before the tool-dispatch loop that can ever reach a
subagent call runs — so this reads it back off that row rather than
widening the call signature the plan already specified. `trace.py`'s own
docstring says "nothing in the harness reads it back mid-turn"; that was
true of every caller that existed when it was written, and remains true of
`run_turn` itself — this is the first caller for which it no longer holds,
because it is the only place a `parent_turn_id` needs turning back into a
`conversation_id` at all. If that lookup somehow comes back empty (the
parent's `trace.start` never ran — should not happen once `loop.py` is
wired, but this function must never crash on it), the subagent still runs;
it simply has no `turn_traces` row of its own to write.
"""

import asyncio
import json
import time
import uuid
from collections.abc import Callable

from pydantic import BaseModel, field_validator

import db
from db.models import TurnTraces
from harness import registry, trace
from harness.models import Ref, ToolResult
from llm import LLMError, Message, ToolCall, complete

__all__ = ["SubagentSpec", "run_subagent"]


class SubagentSpec(BaseModel):
    """A subagent's whole contract (§3.7) — name, its own system prompt, the
    query tools it may call, and its two caps. `tools` is validated at
    construction (see module docstring); nothing else about a
    `SubagentSpec` needs enforcing beyond what Pydantic's own field types
    already give it."""

    name: str
    system_prompt: str
    tools: list[str]
    max_iterations: int = 6
    timeout_s: int = 120

    @field_validator("tools")
    @classmethod
    def _tools_are_registered_query_tools(cls, tools: list[str]) -> list[str]:
        for tool_name in tools:
            spec = registry.get(tool_name)
            if spec is None:
                raise ValueError(f'subagent names unregistered tool "{tool_name}" in its tools list')
            if spec.kind != "query":
                raise ValueError(
                    f'subagent names tool "{tool_name}" in its tools list, but it is kind="{spec.kind}" — '
                    'a subagent may only hold kind="query" tools (§3.7: "subagents are tools ... never mutations")'
                )
        return tools


def _dedupe_refs(refs: list[Ref]) -> list[Ref]:
    """Refs a subagent's tool calls collected, in first-seen order, with
    duplicates (the same `(kind, id)` handle turning up from more than one
    query variant — expected, since the whole point of running several is
    overlapping recall) dropped."""
    seen: set[tuple[str, str]] = set()
    deduped: list[Ref] = []
    for ref in refs:
        key = (ref.kind, ref.id)
        if key not in seen:
            seen.add(key)
            deduped.append(ref)
    return deduped


async def _run_subagent_loop(
    spec: SubagentSpec,
    task: str,
    turn_id: uuid.UUID,
    project_id: uuid.UUID,
    cancel_flag: asyncio.Event,
    tagged_status: Callable[[str], None],
) -> tuple[ToolResult, int]:
    """The actual mini-loop: fresh two-message context, up to
    `spec.max_iterations` completions, dispatching every tool call in
    between. Returns `(result, iterations_run)` — never raises; an LLM
    failure mid-loop (`LLMError`, or `RuntimeError` for "no model
    configured") ends the loop the same way the iteration cap does, with an
    honest `model_view` rather than a crash reaching `run_subagent`'s
    caller."""
    messages: list[Message] = [
        Message(role="system", content=spec.system_prompt),
        Message(role="user", content=task),
    ]
    schemas = registry.schemas_for(spec.tools)
    seen_refs: list[Ref] = []
    iterations = 0

    while iterations < spec.max_iterations:
        iterations += 1
        tagged_status(f"iteration {iterations} of {spec.max_iterations}")

        full_text = ""
        tool_call_acc: dict[int, dict] = {}
        try:
            async for chunk in complete(messages, tools=schemas, tier="primary"):
                full_text += chunk.delta
                for tc_delta in chunk.tool_calls:
                    acc = tool_call_acc.setdefault(tc_delta.index, {"id": None, "name": None, "arguments": ""})
                    acc["id"] = tc_delta.id or acc["id"]
                    acc["name"] = tc_delta.name or acc["name"]
                    acc["arguments"] += tc_delta.arguments
        except LLMError as exc:
            return ToolResult(model_view=f'"{spec.name}" could not complete: {exc}', refs=_dedupe_refs(seen_refs)), iterations
        except RuntimeError as exc:
            return ToolResult(model_view=f'"{spec.name}" is not available: {exc}', refs=_dedupe_refs(seen_refs)), iterations

        if not tool_call_acc:
            # A final answer — no further tool call requested.
            final_text = full_text.strip() or f'"{spec.name}" finished without producing a final answer.'
            return ToolResult(model_view=final_text, refs=_dedupe_refs(seen_refs)), iterations

        messages.append(
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

            tool_spec = registry.get(tool_name)
            if tool_spec is None or tool_spec.kind != "query":
                # Defensive re-check (see module docstring): `schemas` above
                # only ever advertises `spec.tools`, which the constructor
                # already restricted to kind="query", so a well-behaved
                # provider never proposes anything else. Never trust the
                # model's own say-so regardless.
                result = ToolResult(model_view=f'"{tool_name}" is not available to this subagent.')
            else:
                tagged_status(f"calling {tool_name}")
                async with db.session() as session:
                    ctx = registry.ToolContext(
                        session=session,
                        project_id=project_id,
                        turn_id=turn_id,
                        cancel_flag=cancel_flag,
                        status_callback=tagged_status,
                    )
                    result = await registry.dispatch(ctx, tool_name, args)
            seen_refs.extend(result.refs)
            messages.append(Message(role="tool", content=result.model_view, tool_call_id=tool_call_id))

    return (
        ToolResult(
            model_view=f'"{spec.name}" reached its step limit ({spec.max_iterations} iterations) before finishing.',
            refs=_dedupe_refs(seen_refs),
        ),
        iterations,
    )


async def run_subagent(
    spec: SubagentSpec,
    task: str,
    parent_turn_id: uuid.UUID,
    project_id: uuid.UUID,
    cancel_flag: asyncio.Event,
    status_callback: Callable[[str], None] | None = None,
) -> ToolResult:
    """Runs `spec` against `task` in a fresh sub-loop, raced against
    `spec.timeout_s` and the parent turn's own `cancel_flag` — the same
    two-way-race shape `loop.py`'s `_dispatch_tool_bounded` already uses for
    a single tool call, applied here to a whole subagent run. Never raises:
    a timeout, an interruption, or an unexpected failure inside the loop all
    come back as a `ToolResult` whose `model_view` says plainly what
    happened, the same model-floor philosophy `registry.dispatch` already
    applies to a single call."""
    turn_id = uuid.uuid4()
    started_at = time.monotonic()

    def _tagged(text: str) -> None:
        if status_callback is not None:
            status_callback(f"[{spec.name}] {text}")

    async with db.session() as session:
        parent_trace = await session.get(TurnTraces, parent_turn_id)
    conversation_id = parent_trace.conversation_id if parent_trace is not None else None
    if conversation_id is not None:
        await trace.start(turn_id, conversation_id, project_id, parent_turn_id=parent_turn_id)

    run_task = asyncio.ensure_future(_run_subagent_loop(spec, task, turn_id, project_id, cancel_flag, _tagged))
    cancel_task = asyncio.ensure_future(cancel_flag.wait())
    error_code: str | None = None
    try:
        done, _ = await asyncio.wait({run_task, cancel_task}, timeout=spec.timeout_s, return_when=asyncio.FIRST_COMPLETED)
        if run_task in done:
            try:
                result, iterations = run_task.result()
                status = "complete"
            except Exception as exc:  # noqa: BLE001 — the job-worker-style boundary this function promises never to cross (Rules.md's exception carve-out); an actual crash inside the mini-loop still has to come back as a `ToolResult`, not propagate to a tool handler that in turn must never raise.
                result = ToolResult(model_view=f'"{spec.name}" failed unexpectedly: {exc}')
                iterations = 0
                status = "failed"
                error_code = type(exc).__name__
        elif cancel_task in done:
            result = ToolResult(model_view=f'"{spec.name}" was interrupted before it finished — no results to report.')
            iterations = 0
            status = "interrupted"
        else:
            result = ToolResult(model_view=f'"{spec.name}" timed out after {spec.timeout_s}s without finishing — try a narrower question.')
            iterations = 0
            status = "failed"
            error_code = "timeout"
    finally:
        for t in (run_task, cancel_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(run_task, cancel_task, return_exceptions=True)

    if conversation_id is not None:
        await trace.finish(
            turn_id,
            status=status,
            iterations=iterations,
            total_ms=int((time.monotonic() - started_at) * 1000),
            error_code=error_code,
            skill=None,
        )
    return result
