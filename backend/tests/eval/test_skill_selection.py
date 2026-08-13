"""Live scenarios for skill selection (HarnessPlan H8, §3.6: "Skill
descriptions are load-bearing prompt engineering ... every skill gets an
eval scenario asserting it is chosen for its trigger phrasing"; §3.10's
live tier: "the expected skill was loaded for its trigger phrasing").

These need a real 20-30B-class endpoint (H1's model floor) — `llm.complete`
is called for real, no `ScriptedLLM`, and `run_turn` needs a live Postgres
fixture project. There is no confirmed working model endpoint (nor a
fixture project) in this sandbox, so this file is marked `live` and excluded
from the default suite (`pytest -m live` opts in; see `pyproject.toml`'s
`addopts`). **Not run, not claimed to pass, in the session that wrote it.**

Each scenario feeds one realistic user message for one of the plan's four
starting skills through a real `run_turn`, and asserts the *first* tool call
the model makes is `load_skill` with that skill's name — structural, no LLM
judge, matching §3.10's rule. No `pytest-asyncio` plugin is installed in
this project (grep found none in `pyproject.toml`), so — matching every
other async test in this suite (`test_approval.py`, `test_skills.py`) —
each scenario wraps its coroutine in a plain `asyncio.run` inside an
ordinary `def test_...`, rather than depending on a plugin nobody else here
uses.
"""

import asyncio
import uuid

import pytest

from harness import loop
from harness.models import SessionRef, ToolCallEvent, UIState

pytestmark = pytest.mark.live


async def _first_tool_call(message: str) -> ToolCallEvent | None:
    session_ref = SessionRef(project_id=uuid.uuid4(), conversation_id=uuid.uuid4())
    async for event in loop.run_turn(session_ref, message, UIState()):
        if isinstance(event, ToolCallEvent):
            return event
    return None


def test_literature_review_trigger_loads_literature_review_skill():
    event = asyncio.run(
        _first_tool_call("Can you do a literature review on retrieval-augmented generation for me and build a reading list?")
    )
    assert event is not None
    assert event.tool_name == "load_skill"
    assert event.args.get("name") == "literature_review"


def test_compare_papers_trigger_loads_compare_papers_skill():
    event = asyncio.run(_first_tool_call("How do these two open papers differ in their evaluation setup — which one is stronger?"))
    assert event is not None
    assert event.tool_name == "load_skill"
    assert event.args.get("name") == "compare_papers"


def test_summarize_to_note_trigger_loads_summarize_to_note_skill():
    event = asyncio.run(_first_tool_call("Summarize this paper and save it as a note."))
    assert event is not None
    assert event.tool_name == "load_skill"
    assert event.args.get("name") == "summarize_to_note"


def test_find_related_work_trigger_loads_find_related_work_skill():
    event = asyncio.run(_first_tool_call("Find related work for this paper — what else has been written on this topic?"))
    assert event is not None
    assert event.tool_name == "load_skill"
    assert event.args.get("name") == "find_related_work"
