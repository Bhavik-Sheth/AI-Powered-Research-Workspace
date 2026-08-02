"""Saves a provider from `.env` through Settings Store's normal path
(`save_provider`) — the same validated-then-encrypted call the onboarding
wizard makes (D13). A dev/testing convenience for skipping the wizard UI,
not a second way into the app: an invalid key still fails exactly as it
would there, and nothing is written on failure.

Usage: `python scripts/configure_provider.py` (reads GROQ_API_KEY /
GROQ_MODEL from `.env`), or `python scripts/configure_provider.py openai
gpt-4.1-mini` for another provider already configured in `.env` as
`<PROVIDER>_API_KEY`.
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
from settings.models import ProviderCredentials  # noqa: E402


async def main() -> None:
    provider = sys.argv[1] if len(sys.argv) > 1 else "groq"
    model = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    api_key = os.environ.get(f"{provider.upper()}_API_KEY")
    if not api_key:
        print(f"no {provider.upper()}_API_KEY set in backend/.env", file=sys.stderr)
        raise SystemExit(1)

    async with db.session() as session:
        try:
            result = await settings.save_provider(
                session, provider, ProviderCredentials(api_key=api_key, model=model, tier="primary")
            )
        except ValueError as exc:
            print(f"validation failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    print(f"saved — primary_model is now {result.primary_model!r}")


if __name__ == "__main__":
    asyncio.run(main())
