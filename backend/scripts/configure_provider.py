"""Saves a provider from `.env` through Settings Store's normal path
(`save_provider`) — the same validated-then-encrypted call the onboarding
wizard makes (D13). A dev/testing convenience for skipping the wizard UI,
not a second way into the app: an invalid key still fails exactly as it
would there, and nothing is written on failure.

Usage: `python scripts/configure_provider.py` (reads GROQ_API_KEY /
GROQ_MODEL from `.env`), or `python scripts/configure_provider.py openai
gpt-4.1-mini` for another key-based provider already configured in `.env`
as `<PROVIDER>_API_KEY`.

**Local providers (Ollama / vLLM, D11/D12) take no key.** They read
`<PROVIDER>_BASE_URL` (default `http://localhost:11434` for ollama,
`http://localhost:8000` for vllm) and `<PROVIDER>_MODEL` instead —
e.g. `OLLAMA_BASE_URL=http://localhost:11434` and
`OLLAMA_MODEL=llama3.1:8b` in `.env`, then `python
scripts/configure_provider.py ollama`. Omit `<PROVIDER>_MODEL` to have
the script discover available models from the endpoint and use the first
one, exactly like the onboarding wizard's model dropdown.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")

import db  # noqa: E402
import settings  # noqa: E402
from settings.models import LOCAL_PROVIDERS, ProviderCredentials  # noqa: E402

_LOCAL_DEFAULT_BASE_URL = {"ollama": "http://localhost:11434", "vllm": "http://localhost:8000"}


async def _resolve_local(provider: str, model: str | None) -> tuple[str, str]:
    """Returns `(base_url, model)`, discovering the model from the endpoint
    when `<PROVIDER>_MODEL` was not set — the same no-typed-model-string
    path the onboarding wizard's dropdown uses (D11)."""
    base_url = os.environ.get(f"{provider.upper()}_BASE_URL", _LOCAL_DEFAULT_BASE_URL[provider])
    if model:
        return base_url, model
    try:
        available = await settings.discover_models(provider, base_url)
    except Exception as exc:  # noqa: BLE001 — surfaced as a plain CLI error below
        print(f"could not reach {provider} at {base_url}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if not available:
        print(f"{provider} at {base_url} has no models pulled/loaded yet", file=sys.stderr)
        raise SystemExit(1)
    return base_url, available[0]


async def main() -> None:
    provider = sys.argv[1] if len(sys.argv) > 1 else "groq"
    model = sys.argv[2] if len(sys.argv) > 2 else os.environ.get(f"{provider.upper()}_MODEL")

    if provider in LOCAL_PROVIDERS:
        base_url, model = await _resolve_local(provider, model)
        credentials = ProviderCredentials(base_url=base_url, model=model, tier="primary")
    else:
        api_key = os.environ.get(f"{provider.upper()}_API_KEY")
        if not api_key:
            print(f"no {provider.upper()}_API_KEY set in backend/.env", file=sys.stderr)
            raise SystemExit(1)
        model = model or "llama-3.3-70b-versatile"
        credentials = ProviderCredentials(api_key=api_key, model=model, tier="primary")

    async with db.session() as session:
        try:
            result = await settings.save_provider(session, provider, credentials)
        except ValueError as exc:
            print(f"validation failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    print(f"saved — primary_model is now {result.primary_model!r}")


if __name__ == "__main__":
    asyncio.run(main())
