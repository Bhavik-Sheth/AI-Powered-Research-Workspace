"""Settings Store — the single-row local configuration (MODULES.md).

Architecture note (flagged, not silent): MODULES.md lists LLM Gateway as
depending on Settings Store, not the reverse. `save_provider` below breaks
that direction on purpose — D13 requires validating a provider with a live
call *before* it is saved, and `tier` (LLM Gateway's normal, Settings-Store-
resolved path) cannot address credentials that are not saved yet. LLM
Gateway's `override` parameter exists for exactly this call. This is a
narrow, explicit exception to the general dependency order, not a general
license for Settings Store to depend on LLM Gateway elsewhere.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ApiKeys
from llm import LLMError, Message, ProviderOverride, complete
from settings import crypto
from settings.models import LOCAL_PROVIDERS, ModelSettings, Provider, ProviderCredentials, ProviderInfo

DEFAULT_VAULT_PATH = Path.home() / "ResearchOS"

# Bootstrap pointer, outside the vault: Postgres lives *inside* the vault
# (D8), so the vault path must be resolvable before the database exists.
# Not vault content (D3 is unaffected) — this is process bootstrap plumbing,
# the same category of thing as the per-launch token lockfile (TRD §2.2).
_BOOTSTRAP_DIR = Path.home() / ".config" / "research-companion-os"
_BOOTSTRAP_FILE = _BOOTSTRAP_DIR / "vault-path"


def get_vault_path() -> Path:
    """The resolved vault root, read once at startup (MODULES.md)."""
    if _BOOTSTRAP_FILE.exists():
        return Path(_BOOTSTRAP_FILE.read_text().strip()).expanduser()
    return DEFAULT_VAULT_PATH


def set_vault_path(path: Path) -> None:
    """Records a chosen vault path for the *next* launch (onboarding step 2).

    The running process already has Postgres up against whatever
    `get_vault_path` returned at its own startup (Vault Writer: "read once
    ... held for the process lifetime") — applying a new path takes a
    restart, which is why this only writes the bootstrap pointer.
    """
    _BOOTSTRAP_DIR.mkdir(parents=True, exist_ok=True)
    _BOOTSTRAP_FILE.write_text(str(path.expanduser()))


async def _get_or_create_row(session: AsyncSession) -> ApiKeys:
    row = await session.get(ApiKeys, 1)
    if row is None:
        row = ApiKeys(id=1)
        session.add(row)
        await session.flush()
    return row


async def get_settings(session: AsyncSession) -> ModelSettings:
    """Provider config with keys redacted to `…last4`."""
    row = await _get_or_create_row(session)
    providers = {
        name: ProviderInfo(
            last4=info.get("last4"),
            base_url=info.get("base_url"),
            validated_at=info.get("validated_at"),
            model_budgets=info.get("model_budgets", {}),
        )
        for name, info in row.providers.items()
    }
    return ModelSettings(
        providers=providers,
        primary_model=row.primary_model,
        auxiliary_model=row.auxiliary_model,
        onboarding_completed_at=row.onboarding_completed_at,
    )


async def complete_onboarding(session: AsyncSession) -> None:
    """Marks the gated wizard finished (Schema.md `api_keys.onboarding_completed_at`)."""
    row = await _get_or_create_row(session)
    row.onboarding_completed_at = datetime.now(timezone.utc)
    await session.flush()


async def save_provider(session: AsyncSession, provider: Provider, credentials: ProviderCredentials) -> ModelSettings:
    """Encrypts and validates with a live test call before persisting.

    An invalid key or unreachable endpoint raises `ValueError` and nothing
    is written — the row is only mutated after the test call succeeds.
    """
    if provider not in LOCAL_PROVIDERS and not credentials.api_key:
        raise ValueError(f"{provider} requires an API key")
    if provider in LOCAL_PROVIDERS and not credentials.base_url:
        raise ValueError(f"{provider} requires a base URL")

    override = ProviderOverride(
        provider=provider, model=credentials.model, api_key=credentials.api_key, base_url=credentials.base_url
    )
    try:
        async for _ in complete(
            messages=[Message(role="user", content="Say OK.")], override=override, max_tokens=5, timeout=15
        ):
            pass
    except (*LLMError, httpx.HTTPError) as exc:
        raise ValueError(f"could not validate {provider}: {exc}") from exc

    row = await _get_or_create_row(session)
    entry: dict = {"validated_at": datetime.now(timezone.utc).isoformat()}
    if credentials.api_key:
        ciphertext, nonce = crypto.encrypt(credentials.api_key)
        entry.update(ciphertext=ciphertext, nonce=nonce, last4=credentials.api_key[-4:])
    if credentials.base_url:
        entry["base_url"] = credentials.base_url
    if credentials.model_budgets:
        entry["model_budgets"] = dict(credentials.model_budgets)
    row.providers = {**row.providers, provider: entry}
    model_string = f"{override.provider}/{override.model}"
    if credentials.tier == "primary":
        row.primary_model = model_string
    else:
        row.auxiliary_model = model_string
    await session.flush()
    return await get_settings(session)


async def save_model_budget(session: AsyncSession, provider: Provider, model: str, budget: int) -> None:
    """Persists a learned per-model token ceiling without `save_provider`'s
    live validation call — the caller (LLM Gateway's rate-limit self-heal
    path, Phase 2.1) already has proof of the ceiling from the provider's
    own 429 body, so re-validating would just spend another request.

    Keyed by the full `provider/model` string (e.g. `"groq/llama-3.1-8b-instant"`),
    matching `primary_model`/`auxiliary_model` and exactly what `_resolve_tier`
    (`backend/llm/__init__.py`) looks up — not the bare `model` argument, which
    would silently never be found by a real request."""
    row = await _get_or_create_row(session)
    entry = dict(row.providers.get(provider, {}))
    model_key = f"{provider}/{model}"
    entry["model_budgets"] = {**entry.get("model_budgets", {}), model_key: budget}
    row.providers = {**row.providers, provider: entry}
    await session.flush()


async def discover_models(provider: Literal["ollama", "vllm"], base_url: str) -> list[str]:
    """Queries a local endpoint for its available models — never a typed model string."""
    async with httpx.AsyncClient(timeout=10) as client:
        if provider == "ollama":
            response = await client.get(f"{base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            return [entry["name"] for entry in response.json().get("models", [])]
        response = await client.get(f"{base_url.rstrip('/')}/v1/models")
        response.raise_for_status()
        return [entry["id"] for entry in response.json().get("data", [])]


async def get_mcp_servers(session: AsyncSession) -> list[dict]:
    """Configured MCP servers (HarnessPlan H10, §3.11) — read once at startup
    by `harness/mcp/__init__.py:connect_configured_servers`, never in a
    request path. Each entry is a plain dict; `harness/mcp/bridge.py`
    validates shape (`transport`/`command`/`url`) per-server and degrades
    that one entry to no tools rather than failing the others."""
    row = await _get_or_create_row(session)
    return list(row.mcp_servers)


async def set_voice_engine(session: AsyncSession, engine: str) -> None:
    """Persists the selected `backend/voice/` registry engine."""
    row = await _get_or_create_row(session)
    row.voice_engine = engine
    await session.flush()


async def get_voice_engine(session: AsyncSession) -> str:
    row = await _get_or_create_row(session)
    return row.voice_engine


async def set_ptt_binding(session: AsyncSession, binding: str) -> None:
    """Persists the rebound push-to-talk chord (Voice Layer Plan V12)."""
    row = await _get_or_create_row(session)
    row.voice_ptt_binding = binding
    await session.flush()


async def get_ptt_binding(session: AsyncSession) -> str:
    row = await _get_or_create_row(session)
    return row.voice_ptt_binding
