"""Deterministic scenarios for `harness/approval.py` (HarnessPlan H7, §3.9).
Pure asyncio, no DB, no LLM call — `register`/`resolve`/`discard` are the
whole mechanism, so this is the "must-have" tier the task calls for; the
three-way race in `loop.py` that calls into this is exercised structurally
through these entry points rather than a live turn (no fixture project /
compose stack was available while writing this suite, same caveat
`test_context_budget.py` already flags for `run_turn` itself)."""

import asyncio

from harness import approval


def test_register_then_resolve_sets_the_future_result():
    async def _run():
        future = approval.register("req-1")
        approval.resolve("req-1", True)
        return await future

    assert asyncio.run(_run()) is True


def test_resolve_with_no_pending_future_is_a_silent_no_op():
    # No `register("ghost")` call ever happened — this must not raise.
    approval.resolve("ghost", True)


def test_resolve_after_discard_is_a_silent_no_op():
    async def _run():
        approval.register("req-2")
        approval.discard("req-2")
        # A late response arriving after the race already gave up.
        approval.resolve("req-2", False)

    asyncio.run(_run())  # must not raise


def test_resolve_twice_only_the_first_answer_sticks():
    async def _run():
        future = approval.register("req-3")
        approval.resolve("req-3", False)
        approval.resolve("req-3", True)  # a duplicate/stale response — no-op
        return await future

    assert asyncio.run(_run()) is False


def test_each_request_id_gets_its_own_future():
    async def _run():
        first = approval.register("req-4")
        second = approval.register("req-5")
        approval.resolve("req-5", True)
        approval.resolve("req-4", False)
        return await first, await second

    assert asyncio.run(_run()) == (False, True)
