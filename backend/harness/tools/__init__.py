"""Tool groups (D19) — one module per group, `harness/registry.py`'s `@tool`
decorator registering each on import. Importing this package is what makes
the catalog non-empty; nothing outside `harness/` imports these modules
directly (MODULES.md: "harness/'s internal files ... are not separate
modules")."""

from harness.tools import discovery, experiments, memory, navigation, notes, papers, skills  # noqa: F401
