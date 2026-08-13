"""Deterministic scenarios for context assembly's budget and eviction order
(HarnessPlan H1/H3, §3.2/§3.10). No DB, no LLM call — `context.assemble`
and `context.build_blocks` are pure over their arguments, so these run in
CI on every commit like the plan's "deterministic" tier intends.

The full run_turn scenarios this tier also calls for (loop mechanics, tool
dispatch, citation validation against a fixture project) need a live
Postgres fixture project and are not in this file — building and verifying
those requires the compose stack, which was unavailable in the environment
that wrote this suite. Flagged, not silently skipped: see the harness
build's final report.
"""

from harness import context
from harness.models import Ref, UIState
from llm import Message


def _history(n: int, content_len: int = 2000) -> list[Message]:
    return [Message(role="user" if i % 2 == 0 else "assistant", content="x" * content_len) for i in range(n)]


def test_everything_fits_under_the_real_budget():
    """A modest turn — short history, no retrieval — assembles with nothing evicted under the real 24k budget."""
    blocks = context.build_blocks(UIState(), [], [], [], _history(4, content_len=50), [], "what does this paper claim?")
    messages, totals = context.assemble(blocks, budget=context.CONTEXT_BUDGET_TOKENS)
    assert len(messages) == len(blocks)
    assert set(totals) == {b.name for b in blocks}


def test_band_1_never_evicts_even_under_an_impossible_budget():
    """System prompt, paper evidence, and the current message are band 1 — they survive even a budget too small for anything else."""
    blocks = context.build_blocks(UIState(), ["evidence"], [], [], _history(30), [Ref(kind="paper", id="p1", title="t")], "q")
    messages, totals = context.assemble(blocks, budget=1)
    kept_names = set(totals)
    assert "system_prompt" in kept_names
    assert "paper_evidence" in kept_names
    assert "current_message" in kept_names
    assert not any(name.startswith("history:") for name in kept_names)


def test_retrieval_evicts_before_history():
    """Band 4 (retrieval) is dropped before band 3 (history) when both are present and the budget is tight."""
    from memory.models import CitedRow
    import uuid

    text = "a retrieved note" * 50
    memory_rows = [
        CitedRow(id=uuid.uuid4(), source_type="note", source_id="s1", paper_id=None, text=text, section_heading=None, char_start=0, char_end=len(text))
    ]
    blocks = context.build_blocks(UIState(), [], [], memory_rows, _history(2, content_len=20), [], "q")
    # Budget large enough for band 1 + both short history turns, too small to also keep the long memory block.
    messages, totals = context.assemble(blocks, budget=150)
    assert "memory" not in totals
    assert any(name.startswith("history:") for name in totals)


def test_history_evicts_oldest_first():
    """Within band 3, the oldest turn is the first one dropped."""
    blocks = context.build_blocks(UIState(), [], [], [], _history(6, content_len=500), [], "q")
    messages, totals = context.assemble(blocks, budget=400)
    kept_history_indices = sorted(int(name.split(":")[1]) for name in totals if name.startswith("history:"))
    if kept_history_indices:
        assert kept_history_indices == list(range(kept_history_indices[0], 6))  # a contiguous suffix — the newest turns


def test_registry_dispatch_reports_unknown_tool_without_raising():
    """A hallucinated tool name never raises — it returns a corrective `model_view` (H1, §3.4)."""
    import asyncio

    from harness import registry

    async def _run():
        return await registry.dispatch(registry.ToolContext(session=None, project_id=None), "not_a_real_tool", {})

    result = asyncio.run(_run())
    assert "Unknown tool" in result.model_view
    assert "not_a_real_tool" in result.model_view
