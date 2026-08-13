"""Agent Harness — assembles context, calls the LLM, streams typed turn
events, and persists the transcript for one agent turn (MODULES.md, D18).

Public entry point only (HarnessPlan H1): the control loop lives in
`loop.py`, context assembly in `context.py`, the tool catalog in
`registry.py` and `tools/`, and turn observability in `trace.py`. Nothing
outside this package imports those internals directly (D18 node 7) —
`harness.tools` is imported here once, at package load, purely for its
`@tool` registration side effect, so the catalog is populated before the
first `run_turn`.
"""

from harness import skills as _skills
from harness import tools as _tools  # noqa: F401  — registers the catalog
from harness.loop import begin_turn, interrupt, run_turn

# HarnessPlan H8, §3.6: parses and validates every `harness/skills/*.md`
# file against the tool catalog above — must run after `_tools` has
# registered every tool, and here (package load, once) rather than lazily on
# first use, so a broken skill file surfaces at process startup, not the
# first time a turn tries to select it.
_skills.load_index()

__all__ = ["begin_turn", "run_turn", "interrupt"]
