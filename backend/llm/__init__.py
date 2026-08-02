"""LLM Gateway — the only place LiteLLM is imported (MODULES.md, Rules.md).

Phase 1.2 ships `complete` with the `override` parameter Settings Store
needs to validate a provider *before* it is saved (D13) — `tier` alone
cannot express "not-yet-saved" credentials, so this is a deliberate, narrow
widening of MODULES.md's stated signature, called out here rather than
added silently. `complete_structured` and the tier-resolved path below land
with Search Federation (Phase 1.3), the first caller that needs them.
"""

from collections.abc import AsyncIterator
from typing import Literal

import litellm
from pydantic import BaseModel

# Re-exported so callers can catch a call failure without importing litellm
# themselves — this module is the only place that import is allowed. Not
# `litellm.exceptions.OpenAIError`: despite the name, that class sits
# outside the MRO litellm actually raises through (verified empirically —
# its runtime exceptions subclass `openai`'s hierarchy directly), so it
# silently never matches. This tuple is every exception a live completion
# call can realistically raise.
LLMError = (
    litellm.AuthenticationError,
    litellm.BadRequestError,
    litellm.RateLimitError,
    litellm.Timeout,
    litellm.APIConnectionError,
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
    litellm.NotFoundError,
    litellm.ContextWindowExceededError,
)

PROVIDER_PREFIX: dict[str, str] = {
    "google": "gemini",
    "groq": "groq",
    "openai": "openai",
    "anthropic": "anthropic",
    "openrouter": "openrouter",
    "deepseek": "deepseek",
    "custom": "openai",
    "ollama": "ollama",
    "vllm": "hosted_vllm",
}


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class LLMChunk(BaseModel):
    delta: str


class ProviderOverride(BaseModel):
    """An explicit, not-yet-saved provider + model, bypassing Settings Store."""

    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None


async def _resolve_tier(tier: Literal["primary", "auxiliary"]) -> tuple[str, str | None, str | None]:
    # Phase 1.3 (Search Federation is the first tier-based caller): read
    # `api_keys.primary_model` / `auxiliary_model`, stored as "<our-provider-
    # name>/<model>" (e.g. "google/gemini-2.5-flash" — see settings.save_provider),
    # split on the first "/", map through PROVIDER_PREFIX for the real litellm
    # model string, and decrypt that provider's key via settings.crypto.
    # Import settings locally, not at module top — settings/ imports this
    # module for the override path, so a top-level import here would cycle.
    raise NotImplementedError("tier-resolved completion is wired in Phase 1.3 by Search Federation")


async def complete(
    messages: list[Message],
    *,
    tools: list[dict] | None = None,
    tier: Literal["primary", "auxiliary"] = "primary",
    override: ProviderOverride | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> AsyncIterator[LLMChunk]:
    """Streaming completion; auxiliary falls back to primary when unset (D11)."""
    if override is not None:
        model = f"{PROVIDER_PREFIX[override.provider]}/{override.model}"
        api_key, base_url = override.api_key, override.base_url
    else:
        model, api_key, base_url = await _resolve_tier(tier)

    response = await litellm.acompletion(
        model=model,
        messages=[m.model_dump() for m in messages],
        api_key=api_key,
        base_url=base_url,
        tools=tools,
        max_tokens=max_tokens,
        timeout=timeout,
        stream=True,
    )
    async for chunk in response:
        delta = chunk.choices[0].delta.content
        if delta:
            yield LLMChunk(delta=delta)
