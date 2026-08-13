"""Skill loader (HarnessPlan H8, §3.6) — parses `harness/skills/*.md` into
an always-injected index and, on demand, a `load_skill`-able body.

**H2 [table: H8] — model picks from an always-injected index, no
classifier** (D16: no hardwired intent model). Every skill's
`name: description` line goes into band 1 every turn (`index_text`,
~200 tokens for 4 skills); the model calls `load_skill(name)` to bring the
matching body into band 1 for the rest of the turn and that skill's `tools:`
list into the turn's visible schemas — both wired in `loop.py`.

Parsing uses `yaml.safe_load` on the frontmatter block, not a hand-rolled
parser (Rules.md: never write one for a format with a standard library).
Every skill's `tools:` list is validated against `registry.py` at load
time — a skill naming a tool that does not exist is exactly the kind of
drift the registry (H1's keystone) exists to prevent, so this fails loudly
at load time rather than the first time the skill is selected."""

import re
from functools import lru_cache
from pathlib import Path

import yaml

from harness import registry
from harness.models import SkillSpec

__all__ = ["SkillSpec", "load_index", "index_text", "parse_skill"]

_SKILLS_DIR = Path(__file__).parent / "skills"

# `---\n<frontmatter>\n---\n<body>` — the same delimiter shape the plan's own
# example uses. `re.DOTALL` so `.` spans newlines inside the frontmatter
# block; the body is whatever follows the closing `---` line.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_skill(source_name: str, text: str) -> SkillSpec:
    """Parses one skill file's raw text into a `SkillSpec`. `source_name` is
    only used in error messages (the filename, normally), so this can be
    called directly on a fixture string in tests without touching disk —
    the "construct a temp fixture or mock" case the plan calls for.

    Raises `ValueError` — loudly, never silently — when the frontmatter
    block is missing or malformed, a required key is absent, or `tools:`
    names something `registry.get` cannot resolve."""
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise ValueError(f"skill {source_name!r} has no YAML frontmatter block (expected `---\\n...\\n---` at the top of the file)")

    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError(f"skill {source_name!r} has malformed YAML frontmatter: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise ValueError(f"skill {source_name!r}'s frontmatter must be a YAML mapping with name/description/tools")

    name = frontmatter.get("name")
    description = frontmatter.get("description")
    tools = frontmatter.get("tools", [])
    if not isinstance(name, str) or not name:
        raise ValueError(f"skill {source_name!r} is missing a `name` in its frontmatter")
    if not isinstance(description, str) or not description:
        raise ValueError(f"skill {source_name!r} is missing a `description` in its frontmatter")
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise ValueError(f"skill {source_name!r}'s `tools` must be a list of tool names")

    for tool_name in tools:
        if registry.get(tool_name) is None:
            raise ValueError(f'skill {name!r} ({source_name!r}) names unknown tool "{tool_name}" in its `tools:` list')

    body = text[match.end() :].strip()
    if not body:
        raise ValueError(f"skill {source_name!r} has an empty body after its frontmatter")

    return SkillSpec(name=name, description=description, tools=tools, body=body)


@lru_cache(maxsize=1)
def load_index() -> dict[str, SkillSpec]:
    """Parses every `.md` file in `harness/skills/` once, caching the result
    (`lru_cache` on a zero-argument function — the same "run once at package
    load" idiom `harness/tools/__init__.py` uses for `@tool` registration,
    just lazy instead of import-time-eager since it depends on the tool
    registry already being populated). Called from `harness/__init__.py`
    right after `harness.tools` is imported, so a broken skill file surfaces
    at process startup, not the first time a turn selects it.

    Raises `ValueError` (via `parse_skill`) on the first broken file —
    loudly, at startup, never silently skipped."""
    index: dict[str, SkillSpec] = {}
    for path in sorted(_SKILLS_DIR.glob("*.md")):
        spec = parse_skill(path.name, path.read_text())
        if spec.name in index:
            raise ValueError(f'duplicate skill name "{spec.name}" — both {index[spec.name]!r} and {path.name!r} define it')
        index[spec.name] = spec
    return index


def index_text() -> str:
    """The ~200-token 'name: description' index injected into band 1 every
    turn (§3.6) — the model's only way to discover a skill exists, since
    there is no classifier picking one for it (D16)."""
    lines = ["Skills available this project — call load_skill(name) to load one's step-by-step instructions for the rest of this turn:"]
    for spec in load_index().values():
        lines.append(f"- {spec.name}: {spec.description}")
    return "\n".join(lines)
