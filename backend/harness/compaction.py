"""Rolling conversation summary (HarnessPlan H4, §3.3) — the mechanism that
keeps `conversations.summary`/`summarised_through_seq` current, so band 3
(history) does not grow without bound. This is a **window** operation, never
a forgetting one: every verbatim row this compacts stays in `messages`
forever, untouched; only what `loop.py` chooses to pass into
`context.build_blocks` as "history" changes, by way of `_history()` in
`loop.py` preferring the summary plus the un-summarised tail over the full
verbatim transcript once a summary exists. `messages` remains the only
source of truth Resume (H11) and the transcript view read from.

Runs **between** iterations of the control loop (`loop.py`'s top of the
`while` loop), never mid-stream — compacting while a completion is
in-flight would summarise a turn that has not finished yet.
"""

import logging
import uuid

from pydantic import BaseModel
from sqlalchemy import select

import db
import jobs
from db.models import Conversations, Messages
from llm import LLMError, Message, complete_structured, count_tokens

__all__ = ["maybe_compact"]

logger = logging.getLogger(__name__)

# HarnessPlan H1 §3.2 fixes the total budget at 24 000 tokens and notes
# "~3 000 [band 1] never evicted", implying most of the remaining ~21 000 is
# available to bands 2-4 combined — but gives band 3 (history) no explicit
# sub-budget of its own. History is normally the largest of the evictable
# bands (band 2's working set and band 4's retrieval rows are both small,
# bounded lists), so treating its implicit allowance as roughly half the
# *total* budget is a defensible middle ground: generous enough that
# compaction does not fire on every other turn of a normal conversation,
# conservative enough that band 3 alone cannot exhaust the budget before
# bands 2 and 4 get a look-in.
_HISTORY_BAND_TOKENS = 12_000

# HarnessPlan H4 §3.3: "when history tokens > 60% of the history band".
_COMPACTION_THRESHOLD_TOKENS = int(_HISTORY_BAND_TOKENS * 0.6)

# HarnessPlan H4 §3.3: "up to the last K (K = 6) kept verbatim".
_KEEP_VERBATIM_TURNS = 6

# Auxiliary-tier call, off the interactive completion path but still inside
# a loop iteration a user is waiting on — generous enough for a summary over
# a handful of turns, bounded so a slow provider cannot stall the turn (a
# failed/late summary just skips this iteration's compaction, per §3.3).
_SUMMARIZE_TIMEOUT_S = 30

_SUMMARIZE_PROMPT = (
    "Write an updated rolling summary of this conversation, for future recall. Merge the existing "
    "summary (if any) with the new turns below into one concise summary that preserves every "
    "concrete fact, decision, and named entity mentioned. Plain prose, no <cite> tags."
)


class _RollingSummary(BaseModel):
    summary: str


async def _summarize(existing_summary: str | None, turns_text: str) -> str:
    messages = [
        Message(role="system", content=_SUMMARIZE_PROMPT),
        Message(role="user", content=f"Existing summary:\n{existing_summary or '(none yet)'}\n\nNew turns:\n{turns_text}"),
    ]
    result = await complete_structured(messages, _RollingSummary, tier="auxiliary", timeout=_SUMMARIZE_TIMEOUT_S)
    return result.summary


async def maybe_compact(conversation_id: uuid.UUID, history: list[Message]) -> bool:
    """Compacts `conversation_id`'s history into `conversations.summary` when
    `history` (the same list `loop.py` assembles via `_history()`) is over
    the compaction threshold. Returns `True` if a new summary was written —
    the caller should then re-read history from the DB, since the messages
    themselves are unchanged but what `_history()` returns for this
    conversation now differs. Returns `False` on every other outcome,
    including "summarisation failed this time": a failed summary must never
    fail a turn, so the fallback is simply to skip compaction and let the
    oldest turns keep occupying the history band for this turn — the next
    iteration or turn tries again.
    """
    if count_tokens(history) <= _COMPACTION_THRESHOLD_TOKENS:
        return False

    async with db.session() as session:
        conversation = await session.get(Conversations, conversation_id)
        if conversation is None:
            return False

        watermark = conversation.summarised_through_seq or 0
        rows = (
            await session.scalars(
                select(Messages)
                .where(
                    Messages.conversation_id == conversation_id,
                    Messages.seq > watermark,
                    Messages.role.in_(("user", "assistant")),
                )
                .order_by(Messages.seq)
            )
        ).all()
        if len(rows) <= _KEEP_VERBATIM_TURNS:
            return False  # not enough un-summarised history yet to leave K verbatim behind

        to_summarize = rows[:-_KEEP_VERBATIM_TURNS]
        turns_text = "\n\n".join(f"{row.role}: {row.content}" for row in to_summarize)
        try:
            new_summary = await _summarize(conversation.summary, turns_text)
        except (*LLMError, RuntimeError) as exc:
            logger.warning("event=compaction_failed conversation_id=%s error=%s", conversation_id, exc)
            return False

        conversation.summary = new_summary
        conversation.summarised_through_seq = to_summarize[-1].seq

    await jobs.enqueue("chunk_and_embed_job", source_type="conversation_summary", source_id=str(conversation_id))
    return True
