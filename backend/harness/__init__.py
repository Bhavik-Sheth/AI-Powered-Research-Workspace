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

from harness import tools as _tools  # noqa: F401  — registers the catalog
from harness.loop import begin_turn, interrupt, run_turn

__all__ = ["begin_turn", "run_turn", "interrupt"]
