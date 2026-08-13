"""`turn_traces` writer (HarnessPlan H1, §3.10) — the harness's only source
of observability. A row is created `running` at turn start and finalized at
the end; nothing in the harness reads it back mid-turn, so a failure here
must never fail the turn it is describing (`start`/`finish` swallow nothing
themselves — `loop.py` runs them fire-and-forget-adjacent, in their own
`db.session()`, outside the turn's own transactions)."""

import uuid
from typing import Literal

import db
from db.models import TurnTraces


async def start(turn_id: uuid.UUID, conversation_id: uuid.UUID, project_id: uuid.UUID, parent_turn_id: uuid.UUID | None = None) -> None:
    async with db.session() as session:
        session.add(
            TurnTraces(turn_id=turn_id, conversation_id=conversation_id, project_id=project_id, parent_turn_id=parent_turn_id)
        )


async def finish(
    turn_id: uuid.UUID,
    *,
    status: Literal["complete", "interrupted", "failed"],
    iterations: int,
    total_ms: int,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    model: str | None = None,
    context_blocks: dict[str, int] | None = None,
    tool_calls: list[dict] | None = None,
    retrieval: list[dict] | None = None,
    citations: dict | None = None,
    skill: str | None = None,
    error_code: str | None = None,
) -> None:
    async with db.session() as session:
        row = await session.get(TurnTraces, turn_id)
        if row is None:
            return  # `start` never ran for this turn_id — nothing to finalize.
        row.status = status
        row.iterations = iterations
        row.total_ms = total_ms
        row.prompt_tokens = prompt_tokens
        row.completion_tokens = completion_tokens
        row.model = model
        row.context_blocks = context_blocks or {}
        row.tool_calls = tool_calls or []
        row.retrieval = retrieval or []
        row.citations = citations or {}
        row.skill = skill
        row.error_code = error_code
