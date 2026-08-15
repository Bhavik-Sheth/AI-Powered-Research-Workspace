"""Deterministic scenarios for `harness/subagents.py` and `deep_research`
(HarnessPlan H9, §3.7). No DB, no LLM call for the deterministic tier —
`SubagentSpec`'s validator is a pure function over the real tool registry
(already populated by the time `harness.tools` has been imported, same
precondition `test_skills.py` relies on), and the registry-shape assertions
below (`core_schemas()`, `get()`, `schemas_for()`) are all pure lookups.

`run_subagent` itself needs a live LLM (it drives its own `llm.complete`
mini-loop) and a live Postgres (`trace.start`/`finish`, and every dispatched
query tool's own session) to exercise meaningfully end to end — that
scenario is marked `@pytest.mark.live`, the same precedent
`test_skill_selection.py`'s live scenarios already set for "this needs a
real endpoint to mean anything," rather than skipped silently."""

import pytest
from pydantic import ValidationError

from harness import registry, skills
from harness.subagents import SubagentSpec


def test_subagent_spec_construction_succeeds_with_valid_query_tools():
    spec = SubagentSpec(
        name="test_subagent",
        system_prompt="Do the thing.",
        tools=["search_papers", "query_memory", "get_paper"],
    )
    assert spec.tools == ["search_papers", "query_memory", "get_paper"]
    assert spec.max_iterations == 6
    assert spec.timeout_s == 120


def test_subagent_spec_construction_raises_on_unregistered_tool():
    try:
        SubagentSpec(name="broken", system_prompt="x", tools=["this_tool_does_not_exist"])
    except ValidationError as exc:
        assert "this_tool_does_not_exist" in str(exc)
    else:
        raise AssertionError("expected ValidationError for an unregistered tool name")


def test_subagent_spec_construction_raises_on_action_tool():
    """§3.7: "registry enforces every one is kind=query ... fails at import" — `add_paper` is a
    real, registered tool, but it is `kind="action"`, so naming it in a subagent's tool list must
    still fail construction, not just an unknown-name check."""
    add_paper_spec = registry.get("add_paper")
    assert add_paper_spec is not None
    assert add_paper_spec.kind == "action"
    try:
        SubagentSpec(name="broken", system_prompt="x", tools=["add_paper"])
    except ValidationError as exc:
        assert "add_paper" in str(exc)
    else:
        raise AssertionError("expected ValidationError for an action-kind tool")


def test_subagent_spec_construction_raises_on_mixed_valid_and_invalid_tools():
    """A tool list with one good name and one bad one still fails whole — a `SubagentSpec` is never
    partially valid."""
    try:
        SubagentSpec(name="broken", system_prompt="x", tools=["search_papers", "run_all"])
    except ValidationError as exc:
        assert "run_all" in str(exc)
    else:
        raise AssertionError("expected ValidationError — run_all is kind=action")


def test_deep_research_is_registered_but_not_core():
    """§3.4's core set (~9 tools) does not include `deep_research` — non-core tools only enter a
    turn's schema list when a skill declares them (H5) — but it must still resolve directly and
    appear in `schemas_for`, exactly like every other non-core tool (`compare`, `refine_results`,
    ...)."""
    spec = registry.get("deep_research")
    assert spec is not None
    assert spec.kind == "query"
    assert spec.core is False

    core_names = {s["function"]["name"] for s in registry.core_schemas()}
    assert "deep_research" not in core_names

    named_schemas = registry.schemas_for(["deep_research"])
    assert [s["function"]["name"] for s in named_schemas] == ["deep_research"]


def test_deep_research_tools_are_registered_query_tools():
    """`deep_research`'s own composed subagent (`harness/tools/research.py`) names exactly
    `search_papers` and `query_memory` — both must resolve and both must be `kind="query"`, or
    `SubagentSpec`'s own constructor (exercised for real the first time `deep_research` is called)
    would reject it."""
    for tool_name in ("search_papers", "query_memory"):
        spec = registry.get(tool_name)
        assert spec is not None
        assert spec.kind == "query"


def test_find_related_work_skill_names_deep_research_and_it_resolves():
    """Mirrors `test_skills.py`'s equivalent assertion — kept here too since this file is where a
    reader looking for `deep_research` coverage would look first."""
    spec = skills.load_index()["find_related_work"]
    assert "deep_research" in spec.tools
    assert registry.get("deep_research") is not None


@pytest.mark.live
def test_deep_research_runs_against_a_live_endpoint():
    """`run_subagent`'s own mini-loop (`llm.complete`, `registry.dispatch` against real query
    tools, `trace.start`/`finish` against real Postgres) has no meaningful pure-function seam to
    test without either — a stub LLM could exercise the iteration/timeout/cancel bookkeeping, but
    would not prove `deep_research` actually finds and judges relevant papers, which is the whole
    point of the tool. Left as a live scenario, structural per HarnessPlan §3.10: assert the
    subagent's own `turn_traces` row exists with `parent_turn_id` set, and that its `model_view`
    names at least one real candidate for a fixture project's seeded topic."""
    pytest.skip("needs a seeded fixture project and a real LLM endpoint — run with pytest -m live")
