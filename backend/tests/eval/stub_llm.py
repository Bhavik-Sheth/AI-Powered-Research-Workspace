"""A scripted stand-in for `llm.complete()` (HarnessPlan H1, §3.10) —
deterministic tests script exactly what the model does on each loop
iteration instead of driving a real, non-deterministic model. Matches
`complete`'s call signature exactly so a test can monkeypatch
`harness.loop.complete` with a `ScriptedLLM` instance and nothing else in
the loop needs to know the difference.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

from llm import LLMChunk, Message, ProviderOverride, ToolCallDelta


@dataclass
class ScriptedStep:
    """One loop iteration's scripted response: either a final answer
    (`text`, no `tool_calls`) or one or more tool calls the harness should
    dispatch before the script's next step runs."""

    text: str = ""
    tool_calls: list[tuple[str, str, dict]] = field(default_factory=list)  # (id, name, args)


class ScriptedLLM:
    """Replays `steps` in order, one per call — the Nth call to the
    harness's completion loop gets the Nth step. A call past the end of the
    script is a test-authoring bug, not a runtime condition to degrade
    from, so it raises rather than looping or returning something empty."""

    def __init__(self, steps: list[ScriptedStep]) -> None:
        self._steps = steps
        self._calls = 0

    async def __call__(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        tier: Literal["primary", "auxiliary"] = "primary",
        override: ProviderOverride | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> AsyncIterator[LLMChunk]:
        if self._calls >= len(self._steps):
            raise AssertionError(f"ScriptedLLM exhausted after {len(self._steps)} step(s) — the scenario called complete() again")
        step = self._steps[self._calls]
        self._calls += 1
        yield LLMChunk(
            delta=step.text,
            tool_calls=[
                ToolCallDelta(index=i, id=call_id, name=name, arguments=json.dumps(args))
                for i, (call_id, name, args) in enumerate(step.tool_calls)
            ],
        )

    @property
    def calls_made(self) -> int:
        return self._calls
