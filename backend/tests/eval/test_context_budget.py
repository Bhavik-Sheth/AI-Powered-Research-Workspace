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
    """A modest turn — short history — assembles with nothing evicted under the real 24k budget."""
    blocks = context.build_blocks(UIState(), [], [], _history(4, content_len=50), [], "what does this paper claim?")
    messages, totals = context.assemble(blocks, budget=context.CONTEXT_BUDGET_TOKENS)
    assert len(messages) == len(blocks)
    assert set(totals) == {b.name for b in blocks}


def test_band_1_never_evicts_even_under_an_impossible_budget():
    """System prompt, paper evidence, and the current message are band 1 — they survive even a budget too small for anything else."""
    blocks = context.build_blocks(UIState(), ["evidence"], [], _history(30), [Ref(kind="paper", id="p1", title="t")], "q")
    messages, totals = context.assemble(blocks, budget=1)
    kept_names = set(totals)
    assert "system_prompt" in kept_names
    assert "paper_evidence" in kept_names
    assert "current_message" in kept_names
    assert not any(name.startswith("history:") for name in kept_names)


def test_history_evicts_before_working_set():
    """Band 3 (history) is dropped before band 2 (working set) when both are present and the budget is tight
    (HarnessPlan H5, §3.5: band 4 — a dedicated retrieval block — is gone now that `query_memory` is
    tool-mediated, so band 3 is the highest-numbered — first-evicted — surviving band)."""
    working_set_refs = [Ref(kind="paper", id=f"p{i}", title="a fairly long working-set title" * 5) for i in range(5)]
    blocks = context.build_blocks(UIState(), [], [], _history(2, content_len=20), working_set_refs, "q")
    # Budget large enough for band 1 + the working-set block, too small to also keep either history turn.
    messages, totals = context.assemble(blocks, budget=290)
    assert "working_set" in totals
    assert not any(name.startswith("history:") for name in totals)


def test_history_evicts_oldest_first():
    """Within band 3, the oldest turn is the first one dropped."""
    blocks = context.build_blocks(UIState(), [], [], _history(6, content_len=500), [], "q")
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
