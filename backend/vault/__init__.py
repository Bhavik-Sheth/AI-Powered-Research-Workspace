"""Vault Writer — the sole writer of the vault (MODULES.md, D3/D4).

Phase 1.1 ships only the startup layout check; `write_note` / `write_highlight`
/ etc. land with the phases that need them. The vault root itself is owned by
Settings Store — this module only knows the folder shapes inside it.
"""

from pathlib import Path

from settings import get_vault_path

_LAYOUT = ("library/papers", "projects", ".research-os")


def ensure_layout() -> Path:
    """Create the vault root and its top-level folders if missing.

    Raises OSError if the resolved path exists but is not writable — callers
    treat that as the `vault` readiness capability failing, not a crash
    (Rules.md: an unwritable vault path fails only that capability).
    """
    root = get_vault_path()
    for subpath in _LAYOUT:
        (root / subpath).mkdir(parents=True, exist_ok=True)
    probe = root / ".research-os" / ".write-check"
    probe.write_text("")
    probe.unlink()
    return root
