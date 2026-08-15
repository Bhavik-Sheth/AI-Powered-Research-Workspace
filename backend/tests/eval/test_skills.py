"""Deterministic scenarios for `harness/skills.py` and `load_skill`
(HarnessPlan H8, §3.6). No DB, no LLM call — parsing is a pure function over
file text, and `load_skill`'s handler only touches its `ToolContext`'s
`loaded_skill_slot` — so these run in CI on every commit like the plan's
"deterministic" tier intends.
"""

import asyncio

from harness import registry, skills
from harness.tools.skills import LoadSkillArgs, load_skill

_EXPECTED_NAMES = {"literature_review", "compare_papers", "summarize_to_note", "find_related_work"}


def test_load_index_parses_all_four_real_skill_files():
    """`skills/*.md` parses without raising, and yields exactly the plan's four starting skills
    (§3.6's table) — no more, no fewer, and every one resolves against the real tool registry
    (proven by `load_index` itself not raising, since it validates `tools:` at load time)."""
    index = skills.load_index()
    assert set(index) == _EXPECTED_NAMES
    for name, spec in index.items():
        assert spec.name == name
        assert spec.description
        assert spec.body.strip()
        for tool_name in spec.tools:
            assert registry.get(tool_name) is not None, f"{name}'s tools list names unregistered tool {tool_name!r}"


def test_load_index_is_cached():
    """Repeated calls return the same object — parsed once, per the plan's "run once at package
    load" idiom, not once per call."""
    assert skills.load_index() is skills.load_index()


def test_index_text_contains_all_four_skill_names():
    """The band-1 index text (~200 tokens for 4 skills) is the model's only way to discover a
    skill exists — every skill in the index must have a line in it."""
    text = skills.index_text()
    for name in _EXPECTED_NAMES:
        assert name in text


def test_index_text_contains_each_skills_description():
    text = skills.index_text()
    for spec in skills.load_index().values():
        assert spec.description in text


def test_find_related_work_references_deep_research_in_tools():
    """HarnessPlan H9: `find_related_work` is `deep_research`'s entry point (§3.6's table), so once
    the subagent tool is registered its `tools:` frontmatter list names it — and, like every other
    tool a skill declares, it resolves against the real registry (proven the same way the other
    `load_index` test proves it: this file loading at all means `load_index`'s own startup
    validation already accepted it)."""
    spec = skills.load_index()["find_related_work"]
    assert "deep_research" in spec.tools
    assert registry.get("deep_research") is not None
    for tool_name in spec.tools:
        assert registry.get(tool_name) is not None


def test_parse_skill_raises_on_missing_frontmatter():
    try:
        skills.parse_skill("fixture.md", "just a body, no frontmatter block at all")
    except ValueError as exc:
        assert "frontmatter" in str(exc)
    else:
        raise AssertionError("expected parse_skill to raise ValueError")


def test_parse_skill_raises_on_unknown_tool():
    """A skill naming a tool the registry does not have is exactly the drift H1's registry
    (the keystone) exists to prevent — this must raise, not silently register a broken skill."""
    broken = """---
name: broken_skill
description: A skill that references a tool that does not exist.
tools: [this_tool_does_not_exist]
---

1. Do something with a tool that was never registered.
"""
    try:
        skills.parse_skill("broken.md", broken)
    except ValueError as exc:
        assert "this_tool_does_not_exist" in str(exc)
    else:
        raise AssertionError("expected parse_skill to raise ValueError for an unknown tool")


def test_parse_skill_raises_on_missing_required_frontmatter_keys():
    missing_description = """---
name: no_description
tools: []
---

Body text.
"""
    try:
        skills.parse_skill("missing.md", missing_description)
    except ValueError as exc:
        assert "description" in str(exc)
    else:
        raise AssertionError("expected parse_skill to raise ValueError for a missing description")


def test_parse_skill_raises_on_empty_body():
    empty_body = """---
name: empty_body
description: Has frontmatter but nothing after it.
tools: []
---
"""
    try:
        skills.parse_skill("empty.md", empty_body)
    except ValueError as exc:
        assert "empty" in str(exc) or "body" in str(exc)
    else:
        raise AssertionError("expected parse_skill to raise ValueError for an empty body")


def test_parse_skill_succeeds_on_a_well_formed_fixture():
    """The mirror-image of the malformed-fixture tests above — a minimal but complete skill parses
    cleanly, using only tools that are actually registered (`get_paper` — real, `core=True`)."""
    ok = """---
name: fixture_skill
description: A minimal well-formed skill for testing.
tools: [get_paper]
---

1. Do the one thing this skill does.
"""
    spec = skills.parse_skill("fixture.md", ok)
    assert spec.name == "fixture_skill"
    assert spec.tools == ["get_paper"]
    assert "Do the one thing" in spec.body


def test_load_skill_dispatch_with_unknown_name_lists_valid_names():
    """§3.6 / the phase's own verification list: an unknown skill name is not a silent no-op — the
    `model_view` names the valid skills so the model can correct itself."""

    async def _run():
        ctx = registry.ToolContext(session=None, project_id=None)
        return await load_skill(ctx, LoadSkillArgs(name="not_a_real_skill"))

    result = asyncio.run(_run())
    assert "not_a_real_skill" in result.model_view
    for name in _EXPECTED_NAMES:
        assert name in result.model_view
    assert result.refs == []


def test_load_skill_dispatch_with_known_name_sets_the_loaded_skill_slot():
    """The single-slot mechanism `loop.py` reads back after dispatch (`registry.ToolContext`'s
    docstring) — a successful `load_skill` call replaces the slot's contents with exactly the
    matched `SkillSpec`."""

    async def _run():
        slot: list = []
        ctx = registry.ToolContext(session=None, project_id=None, loaded_skill_slot=slot)
        result = await load_skill(ctx, LoadSkillArgs(name="compare_papers"))
        return result, slot

    result, slot = asyncio.run(_run())
    assert "compare_papers" in result.model_view
    assert len(slot) == 1
    assert slot[0].name == "compare_papers"


def test_load_skill_replaces_not_appends():
    """"One skill at a time. Loading a second replaces the first, and its tools with it." (§3.6)"""

    async def _run():
        slot: list = []
        ctx = registry.ToolContext(session=None, project_id=None, loaded_skill_slot=slot)
        await load_skill(ctx, LoadSkillArgs(name="literature_review"))
        await load_skill(ctx, LoadSkillArgs(name="summarize_to_note"))
        return slot

    slot = asyncio.run(_run())
    assert len(slot) == 1
    assert slot[0].name == "summarize_to_note"


def test_registry_schemas_for_returns_only_the_named_tools():
    schemas = registry.schemas_for(["get_paper", "save_note"])
    names = {s["function"]["name"] for s in schemas}
    assert names == {"get_paper", "save_note"}


def test_registry_schemas_for_skips_unknown_names_rather_than_raising():
    schemas = registry.schemas_for(["get_paper", "not_a_real_tool"])
    names = {s["function"]["name"] for s in schemas}
    assert names == {"get_paper"}


def test_load_skill_is_core_and_visible_without_any_skill_loaded():
    """`load_skill` itself must always be visible — it is how a skill gets loaded in the first
    place — so it is in `core_schemas()` unconditionally."""
    names = {s["function"]["name"] for s in registry.core_schemas()}
    assert "load_skill" in names


def test_schemas_for_turn_deduplicates_against_core():
    """Regression: several real skills (literature_review among them) list a tool that is
    already core in their `tools:` — e.g. search_papers/add_paper/get_paper/save_note. The
    combined turn schema list must show each tool once, not twice, or a completion request
    carries a duplicate function schema for no reason."""
    active_skill_tools = ["search_papers", "add_paper", "get_paper", "save_note", "mark_relevant"]
    schemas = registry.schemas_for_turn(active_skill_tools)
    names = [s["function"]["name"] for s in schemas]
    assert len(names) == len(set(names)), f"duplicate schemas: {names}"
    for tool_name in active_skill_tools:
        assert tool_name in names


def test_schemas_for_turn_with_no_active_skill_equals_core_schemas():
    core_names = {s["function"]["name"] for s in registry.core_schemas()}
    turn_names = {s["function"]["name"] for s in registry.schemas_for_turn(None)}
    assert turn_names == core_names
