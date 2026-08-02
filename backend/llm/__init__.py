"""LLM Gateway — the only place LiteLLM is imported (MODULES.md, Rules.md).

`complete` carries the `override` parameter Settings Store needs to
validate a provider *before* it is saved (D13, added Phase 1.2) — `tier`
alone cannot express "not-yet-saved" credentials, so this is a deliberate,
narrow widening of MODULES.md's stated signature, called out here rather
than added silently.
"""

from collections.abc import AsyncIterator
from typing import Literal, TypeVar

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
    """Resolves a tier to a real litellm model string + credentials.

    Imports `db`/`settings` locally, not at module top: `settings/` imports
    this module for the override path, so a top-level import here would
    cycle (see settings/__init__.py's architecture note).
    """
    import db
    from db.models import ApiKeys
    from settings import crypto

    async with db.session() as session:
        row = await session.get(ApiKeys, 1)
        model_string = row.primary_model if row else None
        if row and tier == "auxiliary" and row.auxiliary_model:
            model_string = row.auxiliary_model
        if not model_string:
            reason = "no primary model configured" if not (row and row.primary_model) else f"no {tier} model configured"
            raise RuntimeError(reason)

        our_provider, _, model = model_string.partition("/")
        provider_info = (row.providers if row else {}).get(our_provider)
        if provider_info is None:
            raise RuntimeError(f"no credentials stored for provider {our_provider}")

        api_key = None
        if "ciphertext" in provider_info:
            api_key = crypto.decrypt(provider_info["ciphertext"], provider_info["nonce"])
        base_url = provider_info.get("base_url")

    return f"{PROVIDER_PREFIX[our_provider]}/{model}", api_key, base_url


async def _resolve(
    tier: Literal["primary", "auxiliary"], override: ProviderOverride | None
) -> tuple[str, str | None, str | None]:
    if override is not None:
        return f"{PROVIDER_PREFIX[override.provider]}/{override.model}", override.api_key, override.base_url
    return await _resolve_tier(tier)


ResponseSchema = TypeVar("ResponseSchema", bound=BaseModel)


async def complete_structured(
    messages: list[Message],
    schema: type[ResponseSchema],
    *,
    tier: Literal["primary", "auxiliary"] = "primary",
    override: ProviderOverride | None = None,
    timeout: float | None = None,
) -> ResponseSchema:
    """Structured extraction; the prompted-JSON fallback for models without
    native structured output is LiteLLM's own concern, not this wrapper's."""
    model, api_key, base_url = await _resolve(tier, override)
    response = await litellm.acompletion(
        model=model,
        messages=[m.model_dump() for m in messages],
        api_key=api_key,
        base_url=base_url,
        response_format=schema,
        timeout=timeout,
    )
    return schema.model_validate_json(response.choices[0].message.content)


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
    model, api_key, base_url = await _resolve(tier, override)

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
