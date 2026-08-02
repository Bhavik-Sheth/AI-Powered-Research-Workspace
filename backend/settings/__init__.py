"""Settings Store — the single-row local configuration (MODULES.md).

Phase 1.1 ships only `get_vault_path`, resolved to the fixed default. Reading
and persisting a user-chosen override in `api_keys.vault_path` lands in
Phase 1.2 with the rest of the onboarding wizard's provider/model config.
"""

from pathlib import Path

DEFAULT_VAULT_PATH = Path.home() / "ResearchOS"


def get_vault_path() -> Path:
    """The resolved vault root. Phase 1.2 adds the `api_keys.vault_path` override."""
    return DEFAULT_VAULT_PATH
