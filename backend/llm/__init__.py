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

# RateLimitError/Timeout/connection-level failures are retried by the
# provider client under the hood (litellm's `num_retries` sets its
# `max_retries`, honouring a `Retry-After` header when the provider sends
# one) rather than surfacing on the first transient failure.
_NUM_RETRIES = 3

# Reserved out of a tier's request-token budget for the completion itself,
# when the caller does not pass an explicit `max_tokens`.
_COMPLETION_TOKEN_MARGIN = 512

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


class ToolCall(BaseModel):
    """One complete tool call an assistant message carries, matching the
    provider wire shape the harness re-sends on the next turn of the loop."""

    id: str
    name: str
    arguments: str  # JSON-encoded, per the wire protocol — the harness decodes it


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


def _to_wire_message(message: Message) -> dict:
    """Per-provider tool-call wire shaping — the Gateway's job (MODULES.md:
    "Hides: ... per-provider request shaping"), not something a caller
    building a `Message` should have to know."""
    wire: dict = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
            for tc in message.tool_calls
        ]
    if message.tool_call_id is not None:
        wire["tool_call_id"] = message.tool_call_id
    return wire


class ToolCallDelta(BaseModel):
    """One streamed fragment of a tool call — `index` identifies which
    call across fragments; `name`/`arguments` arrive whole or in pieces
    depending on the provider, so the caller accumulates by index."""

    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""


class LLMChunk(BaseModel):
    delta: str = ""
    tool_calls: list[ToolCallDelta] = []


class ProviderOverride(BaseModel):
    """An explicit, not-yet-saved provider + model, bypassing Settings Store."""

    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None


async def _resolve_tier(tier: Literal["primary", "auxiliary"]) -> tuple[str, str | None, str | None, int | None]:
    """Resolves a tier to a real litellm model string + credentials + request-token budget.

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
        request_token_budget = provider_info.get("request_token_budget")

    return f"{PROVIDER_PREFIX[our_provider]}/{model}", api_key, base_url, request_token_budget


async def _resolve(
    tier: Literal["primary", "auxiliary"], override: ProviderOverride | None
) -> tuple[str, str | None, str | None, int | None]:
    if override is not None:
        return f"{PROVIDER_PREFIX[override.provider]}/{override.model}", override.api_key, override.base_url, None
    return await _resolve_tier(tier)


def _fit_to_budget(model: str, messages: list[Message], budget: int | None, max_tokens: int | None) -> list[Message]:
    """Truncates the largest message so the request fits the tier's token budget.

    Definitive section-aware windowing is the caller's job (Bug Fix Plan
    Phase 1.2); this is the Gateway's last-resort guard so an oversized
    request degrades to a truncated one instead of a terminal
    `RateLimitError`. `budget=None` (no ceiling configured) is a no-op.
    """
    if budget is None:
        return messages
    reserve = max_tokens if max_tokens is not None else _COMPLETION_TOKEN_MARGIN
    available = budget - reserve
    dumped = [_to_wire_message(m) for m in messages]
    if litellm.token_counter(model=model, messages=dumped) <= available:
        return messages

    longest_index = max(range(len(messages)), key=lambda i: len(messages[i].content))
    longest = messages[longest_index]
    low, high = 0, len(longest.content)
    while low < high:
        mid = (low + high + 1) // 2
        candidate = [*dumped[:longest_index], {**dumped[longest_index], "content": longest.content[:mid]}, *dumped[longest_index + 1 :]]
        if litellm.token_counter(model=model, messages=candidate) <= available:
            low = mid
        else:
            high = mid - 1

    fitted = list(messages)
    fitted[longest_index] = Message(role=longest.role, content=longest.content[:low])
    return fitted


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
    model, api_key, base_url, budget = await _resolve(tier, override)
    fitted = _fit_to_budget(model, messages, budget, max_tokens=None)
    response = await litellm.acompletion(
        model=model,
        messages=[_to_wire_message(m) for m in fitted],
        api_key=api_key,
        base_url=base_url,
        response_format=schema,
        timeout=timeout,
        num_retries=_NUM_RETRIES,
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
    model, api_key, base_url, budget = await _resolve(tier, override)
    fitted = _fit_to_budget(model, messages, budget, max_tokens)

    response = await litellm.acompletion(
        model=model,
        messages=[_to_wire_message(m) for m in fitted],
        api_key=api_key,
        base_url=base_url,
        tools=tools,
        max_tokens=max_tokens,
        timeout=timeout,
        stream=True,
        num_retries=_NUM_RETRIES,
    )
    async for chunk in response:
        delta = chunk.choices[0].delta
        content = delta.content
        tool_call_deltas = [
            ToolCallDelta(
                index=tc.index,
                id=tc.id,
                name=tc.function.name if tc.function else None,
                arguments=(tc.function.arguments if tc.function and tc.function.arguments else ""),
            )
            for tc in (delta.tool_calls or [])
        ]
        if content or tool_call_deltas:
            yield LLMChunk(delta=content or "", tool_calls=tool_call_deltas)
