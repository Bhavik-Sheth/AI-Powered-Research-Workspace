"""LLM Gateway — the only place LiteLLM is imported (MODULES.md, Rules.md).

`complete` carries the `override` parameter Settings Store needs to
validate a provider *before* it is saved (D13, added Phase 1.2) — `tier`
alone cannot express "not-yet-saved" credentials, so this is a deliberate,
narrow widening of MODULES.md's stated signature, called out here rather
than added silently.
"""

import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal, NamedTuple, TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from ratelimit import RateLimiter, call_with_retry

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

# Every `litellm.acompletion` call below passes `num_retries=0` (Bug Fix
# Plan Phase 6.13): retries used to be litellm's own opaque internal
# backoff, invisible to this module and unpaced against the per-provider
# `RateLimiter` below — verified live to still exhaust a free-tier ceiling
# (Groq's 6000 TPM, then Mistral's undisclosed one) even with it enabled.
# `call_with_retry` (bottom of this file, wrapping every real call site) is
# now the sole retry authority: it paces call *starts* through a shared
# per-provider `RateLimiter` and backs off on the exact transient
# exceptions below, reading the provider's own stated wait time
# (`_parse_wait_seconds`) when one is given.
_LLM_RETRYABLE_EXCEPTIONS = (
    litellm.RateLimitError,
    litellm.Timeout,
    litellm.APIConnectionError,
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
)

# Reserved out of a tier's request-token budget for the completion itself,
# when the caller does not pass an explicit `max_tokens`.
_COMPLETION_TOKEN_MARGIN = 512

PROVIDER_PREFIX: dict[str, str] = {
    "google": "gemini",
    "groq": "groq",
    "openai": "openai",
    "anthropic": "anthropic",
    "mistral": "mistral",
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


class Usage(BaseModel):
    """Token accounting for one completion — carried on the final streamed
    chunk only (HarnessPlan H1: `turn_traces.prompt_tokens`/`completion_tokens`
    need a real source; nothing in this module captured usage before)."""

    prompt_tokens: int
    completion_tokens: int


class LLMChunk(BaseModel):
    delta: str = ""
    tool_calls: list[ToolCallDelta] = []
    usage: Usage | None = None
    model: str | None = None  # set only on the trailing usage chunk — the resolved `provider/model` string


class ProviderOverride(BaseModel):
    """An explicit, not-yet-saved provider + model, bypassing Settings Store."""

    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None


class _Resolved(NamedTuple):
    """A tier or override resolved to one concrete request.

    `provider`/`model_key` are the self-heal write-back coordinates —
    `providers[provider].model_budgets[model_key]` in `api_keys` — present
    only for a saved tier. An override has no saved row to write back to,
    so both are `None` there and the self-heal path is skipped for it.
    """

    model: str
    api_key: str | None
    base_url: str | None
    budget: int | None
    provider: str | None
    model_key: str | None


async def _resolve_tier(tier: Literal["primary", "auxiliary"]) -> _Resolved:
    """Resolves a tier to a real litellm model string + credentials + per-model budget.

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
        # Keyed by the full stored model string (`model_string`, e.g.
        # "groq/llama-3.1-8b-instant"), not the litellm-prefixed form below —
        # one provider can host models with different TPM ceilings.
        budget = provider_info.get("model_budgets", {}).get(model_string)

    return _Resolved(
        model=f"{PROVIDER_PREFIX[our_provider]}/{model}",
        api_key=api_key,
        base_url=base_url,
        budget=budget,
        provider=our_provider,
        model_key=model_string,
    )


async def _resolve(tier: Literal["primary", "auxiliary"], override: ProviderOverride | None) -> _Resolved:
    if override is not None:
        return _Resolved(
            model=f"{PROVIDER_PREFIX[override.provider]}/{override.model}",
            api_key=override.api_key,
            base_url=override.base_url,
            budget=None,
            provider=None,
            model_key=None,
        )
    return await _resolve_tier(tier)


_logger = logging.getLogger(__name__)


def _fit_to_budget(model: str, messages: list[Message], budget: int | None, max_tokens: int | None) -> list[Message]:
    """Truncates the largest message so the request fits the tier's token budget.

    Definitive section-aware windowing is `harness/context.py`'s job
    (HarnessPlan H3): it assembles every turn's messages inside a 24k-token
    band-eviction budget before this module ever sees them, so this should
    never fire for a harness-originated call. This stays as the Gateway's
    last-resort guard for every other caller (extraction jobs, structured
    completions) and logs when it fires so a harness call tripping it is
    visible as the bug it would be — degrading to a mid-message truncation
    instead of a terminal `RateLimitError`. `budget=None` (no ceiling
    configured) is a no-op.
    """
    if budget is None:
        return messages
    reserve = max_tokens if max_tokens is not None else _COMPLETION_TOKEN_MARGIN
    available = budget - reserve
    dumped = [_to_wire_message(m) for m in messages]
    if litellm.token_counter(model=model, messages=dumped) <= available:
        return messages

    _logger.warning("event=llm_fit_to_budget_fired model=%s available=%d message_count=%d", model, available, len(messages))
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


# Groq's (and other openai-compatible providers') 429 body names the real
# ceiling inline, e.g. "...on tokens per minute (TPM): Limit 6000, Used 0,
# Requested 14327...". litellm folds that provider text verbatim into the
# exception's `message` (`exception_mapping_utils.py`: `message=f"...{message}"`
# where `message` is the original provider error string) — the number right
# after "Limit" is the ceiling the next attempt should fit to.
_RATE_LIMIT_CEILING_PATTERN = re.compile(r"[Ll]imit[:\s]+(\d+)")


def _parse_rate_limit_ceiling(exc: litellm.RateLimitError) -> int | None:
    """Extracts the provider's real token ceiling from a 429 body.

    Returns `None` when the message carries no recognisable ceiling, so the
    caller falls back to today's behaviour — surfacing the error untouched —
    rather than guessing a number.
    """
    message = getattr(exc, "message", None) or str(exc)
    match = _RATE_LIMIT_CEILING_PATTERN.search(message)
    return int(match.group(1)) if match else None


async def _persist_learned_budget(provider: str, model_key: str, budget: int) -> None:
    """Writes a self-healed per-model ceiling back to Settings Store so the
    next call to this model is sized correctly on the first attempt.

    `model_key` (from `_Resolved.model_key`) is the *full* `provider/model`
    string — `save_model_budget`'s `model` parameter is the *bare* name
    (the form its REST route already splits a stored string into) and
    rejoins with `provider` itself, so passing `model_key` through
    unchanged double-prefixes the stored key (`groq/groq/llama-...`),
    silently orphaning it from what `_resolve_tier` looks up. Strip the
    provider prefix back off before the call.

    Imported locally — see `_resolve_tier`'s note on the import cycle.
    """
    import db
    from settings import save_model_budget

    bare_model = model_key.removeprefix(f"{provider}/")
    async with db.session() as session:
        await save_model_budget(session, provider, bare_model, budget)


_T = TypeVar("_T")


async def _call_with_rate_limit_self_heal(
    resolved: _Resolved,
    messages: list[Message],
    max_tokens: int | None,
    make_call: Callable[[list[Message]], Awaitable[_T]],
) -> _T:
    """Fits `messages` to the tier's known budget and runs `make_call`.

    On a `RateLimitError` whose body names the provider's real ceiling for a
    saved tier (not an override — there is no row to write back to), learns
    it, persists it via Settings Store, refits once, and retries. Any other
    outcome — an unparseable message, an override, or a second failure —
    surfaces unchanged (Bug Fix Plan Phase 2.1).
    """
    fitted = _fit_to_budget(resolved.model, messages, resolved.budget, max_tokens)
    try:
        return await make_call(fitted)
    except litellm.RateLimitError as exc:
        learned = _parse_rate_limit_ceiling(exc)
        if learned is None or resolved.provider is None or resolved.model_key is None:
            raise
        await _persist_learned_budget(resolved.provider, resolved.model_key, learned)
        fitted = _fit_to_budget(resolved.model, messages, learned, max_tokens)
        return await make_call(fitted)


# Paces call *starts* to one provider (Bug Fix Plan Phase 6.13, replacing
# Phase 6.12's concurrency-only `asyncio.Semaphore`). A free-tier ceiling
# (Groq's self-healed 6000 TPM, or an undisclosed one like Mistral's) is
# shared across every caller regardless of whether they overlap in time —
# verified live: sequential (non-overlapping) `extract_card_job` retries
# fired ~3s apart still tripped Mistral's 429, which a concurrency cap
# alone can never catch since nothing was ever running simultaneously.
# 2.5s between call starts is a conservative flat default (~24/min) chosen
# to sit comfortably under typical free-tier RPM ceilings across providers
# without needing to know any one of them exactly; a caller that can't
# start yet just waits its turn, same shape as `RateLimiter` everywhere
# else in this codebase (`ratelimit.py`, `search/rate_limit.py`).
_PROVIDER_MIN_INTERVAL_S = 2.5
_provider_limiters: dict[str, RateLimiter] = {}


def _get_provider_limiter(provider: str | None) -> RateLimiter:
    """`provider` is `None` for a `ProviderOverride` call (Settings Store's
    pre-save validation) — those share one bucket of their own rather than
    skipping throttling entirely."""
    key = provider or "__override__"
    limiter = _provider_limiters.get(key)
    if limiter is None:
        limiter = RateLimiter(_PROVIDER_MIN_INTERVAL_S)
        _provider_limiters[key] = limiter
    return limiter


# Provider-stated wait time, when the 429 body names one — verified live
# against a real captured Groq rejection: "...Please try again in 22.29s.
# ...". Reusing this beats a blind exponential backoff whenever the
# provider is honest about it, same reasoning as `_parse_rate_limit_ceiling`
# above for the TPM number embedded in the same kind of message. Returns
# `None` (falls back to exponential backoff, `call_with_retry`'s default)
# for a provider that says only "rate limit exceeded" with no number, e.g.
# Mistral's — verified live not to match this pattern.
_RETRY_WAIT_PATTERN = re.compile(r"try again in ([\d.]+)\s*s", re.IGNORECASE)


def _parse_wait_seconds(exc: BaseException) -> float | None:
    message = getattr(exc, "message", None) or str(exc)
    match = _RETRY_WAIT_PATTERN.search(message)
    return float(match.group(1)) if match else None


ResponseSchema = TypeVar("ResponseSchema", bound=BaseModel)


# The two provider-error markers seen on a real, captured schema-validation
# rejection (`code: "tool_use_failed"`, and the free-text "did not match
# schema" from the same error) — verified live against
# `groq/llama-3.1-8b-instant` (Bug Fix Plan Phase 2.3). Deliberately narrow:
# a `BadRequestError` for an unrelated reason (bad message role, oversized
# request the budget guard missed) would not be helped by resending with a
# corrective note, so it is left to surface unchanged rather than wasting a
# retry on it.
_SCHEMA_VIOLATION_MARKERS = ("tool_use_failed", "tool call validation failed", "did not match schema")


def _is_schema_violation(exc: litellm.BadRequestError) -> bool:
    message = getattr(exc, "message", None) or str(exc)
    return any(marker in message for marker in _SCHEMA_VIOLATION_MARKERS)


def _extract_failed_generation(exc: litellm.BadRequestError) -> tuple[str, str]:
    """Pulls the provider's raw malformed output and validation-error text
    out of the exception.

    Empirically, `exc.body` is `None` for this failure even on a current
    litellm build whose exception-mapping code looks like it should thread
    the provider's parsed body through — verified by reproducing the real
    `groq/llama-3.1-8b-instant` `tool_use_failed` rejection live. What *is*
    reliably present is the provider's raw JSON error, embedded verbatim as
    the tail of `exc.message` (same shape `_parse_rate_limit_ceiling` above
    already relies on for the 429 case), with `failed_generation` as one of
    its keys — so it is parsed out of the message text, not off a field.
    """
    message = getattr(exc, "message", None) or str(exc)
    match = re.search(r"\{.*\}", message, re.DOTALL)
    if not match:
        return "", message
    try:
        payload = json.loads(match.group(0)).get("error", {})
    except json.JSONDecodeError:
        return "", message
    return payload.get("failed_generation", ""), payload.get("message", message)


# Groq wraps a rejected tool call as `<function=json_tool_call> {...}
# </function>`, not bare JSON. Feeding that literal wrapper back to the
# model (first tried as a synthetic `assistant` turn) measurably made
# things worse — verified live, repeatedly: the model would then mimic the
# `<function=...>` framing instead of returning JSON. Pulling out just the
# embedded object and showing it as plain data avoids teaching the model
# that odd syntax is the shape to produce.
_EMBEDDED_JSON_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_payload(text: str) -> str:
    match = _EMBEDDED_JSON_PATTERN.search(text)
    return match.group(0) if match else text


async def _repair_schema_violation(
    resolved: _Resolved,
    messages: list[Message],
    schema: type[ResponseSchema],
    malformed_output: str,
    error_detail: str,
    timeout: float | None,
) -> ResponseSchema:
    """One corrective retry: shows the model its own rejected attempt, the
    validation error, and the schema's own JSON-schema spelled out
    explicitly, then re-asks with the *same* schema unchanged — a single
    `user`-role message, not a synthetic `assistant` turn (see above).

    Chosen over a structurally simplified fallback schema after comparing
    both live against the real captured failure: a flattened schema (every
    nested field turned into a bare string, matching what the model
    produced unprompted) reproduced the identical `tool_use_failed` error
    on retry — Groq's constrained decoding for this model rejects the
    request outright before generation, so a schema change doesn't route
    around it — while this corrective follow-up repeatedly returned a
    fully valid instance (verified live, several runs). Pydantic's own
    lenient coercion doesn't help either: `Model.model_validate(data,
    strict=False)` still raises on a bare string where a nested model is
    required (verified locally), since promoting `"a quote"` into
    `{"quote": "a quote"}` needs semantic knowledge of *which* field the
    string is short for — which this module (generic over any schema)
    does not have and the model already does, which is exactly what the
    corrective retry asks it to supply.

    Content is never invented here: the corrected quote text still has to
    come from the model on this retry, and the caller still runs it through
    `validate_and_anchor` unchanged, exactly as a first-attempt extraction
    would (D24) — this only repairs *shape*, never fabricates *content*.
    """
    corrective_messages = [
        *messages,
        Message(
            role="user",
            content=(
                "Your previous attempt was rejected for not matching the required "
                f"schema. Validation error: {error_detail}\n"
                f"Your rejected attempt was: {_extract_json_payload(malformed_output)}\n"
                "Return a corrected result matching this JSON schema exactly — every "
                "non-null field the schema types as an object must be an object, "
                f"never a bare string: {json.dumps(schema.model_json_schema())}"
            ),
        ),
    ]
    fitted = _fit_to_budget(resolved.model, corrective_messages, resolved.budget, max_tokens=None)

    async def make_repair_call():
        return await litellm.acompletion(
            model=resolved.model,
            messages=[_to_wire_message(m) for m in fitted],
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            response_format=schema,
            timeout=timeout,
            num_retries=0,  # call_with_retry below owns retry/backoff (Phase 6.13)
        )

    response = await call_with_retry(
        _get_provider_limiter(resolved.provider),
        make_repair_call,
        retryable_exceptions=_LLM_RETRYABLE_EXCEPTIONS,
        parse_wait_seconds=_parse_wait_seconds,
    )
    content = response.choices[0].message.content
    try:
        return schema.model_validate_json(content)
    except ValidationError as exc:
        # Still part of `LLMError`'s vocabulary (via `RuntimeError`) so a
        # caller catching that tuple — e.g. `extract_card_job`'s per-window
        # skip — needs no change to handle "the repair also failed": one
        # retry was spent, and this is the second and last failure.
        raise RuntimeError(f"schema repair failed: response still does not match {schema.__name__}: {exc}") from exc


async def complete_structured(
    messages: list[Message],
    schema: type[ResponseSchema],
    *,
    tier: Literal["primary", "auxiliary"] = "primary",
    override: ProviderOverride | None = None,
    timeout: float | None = None,
) -> ResponseSchema:
    """Structured extraction; the prompted-JSON fallback for models without
    native structured output is LiteLLM's own concern, not this wrapper's.

    A schema-validation failure — the provider outright rejecting the call
    (`BadRequestError`/`tool_use_failed`) or, defensively, a 200 response
    whose content still fails to parse against `schema` — gets exactly one
    corrective retry (`_repair_schema_violation`) before surfacing. This is
    independent of, and composes cleanly with, `_call_with_rate_limit_self_heal`
    above: that wrapper only ever catches `RateLimitError` and retries at
    most once *inside* `make_call`; a schema violation is a different
    exception type it never touches, so it always propagates out to here
    unchanged. A single request that hit both failure modes in sequence
    (rate-limited, then — once refitted and retried — rejected for its
    shape) would see at most one rate-limit retry followed by at most one
    schema retry: three network calls total, bounded, no shared state
    between the two retry paths.
    """
    resolved = await _resolve(tier, override)

    async def make_call(fitted: list[Message]):
        return await litellm.acompletion(
            model=resolved.model,
            messages=[_to_wire_message(m) for m in fitted],
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            response_format=schema,
            timeout=timeout,
            num_retries=0,  # call_with_retry below owns retry/backoff (Phase 6.13)
        )

    async def attempt():
        return await _call_with_rate_limit_self_heal(resolved, messages, None, make_call)

    try:
        # Structured extraction runs in background jobs (`extract_card_job`,
        # `trace_references_job`, ...) with real time to spend riding out a
        # burst — a higher retry budget than the interactive `complete`
        # below, which a person is waiting on live.
        response = await call_with_retry(
            _get_provider_limiter(resolved.provider),
            attempt,
            retryable_exceptions=_LLM_RETRYABLE_EXCEPTIONS,
            parse_wait_seconds=_parse_wait_seconds,
            max_retries=4,
            base_backoff_s=2.0,
            max_backoff_s=30.0,
        )
    except litellm.BadRequestError as exc:
        if not _is_schema_violation(exc):
            raise
        malformed_output, error_detail = _extract_failed_generation(exc)
        return await _repair_schema_violation(resolved, messages, schema, malformed_output, error_detail, timeout)

    content = response.choices[0].message.content
    try:
        return schema.model_validate_json(content)
    except ValidationError as exc:
        return await _repair_schema_violation(resolved, messages, schema, content, str(exc), timeout)


# Generic fallback tokenizer for `count_tokens` when the caller has no
# resolved model string yet (context-budgeting runs before a tier is
# resolved to real credentials) — litellm's own default model for its
# tokenizer. An estimate for eviction thresholds, never for cost/billing
# math, so a tokenizer mismatch against the eventually-resolved model is
# immaterial to correctness.
_DEFAULT_TOKEN_COUNT_MODEL = "gpt-3.5-turbo"


def count_tokens(messages: list[Message], model: str | None = None) -> int:
    """Token count for `messages` — the one place outside this module
    allowed to need a token count without importing litellm directly
    (Rules.md: LiteLLM only imported here). `model` selects the tokenizer;
    omit it for a budgeting estimate made before a tier is resolved."""
    return litellm.token_counter(model=model or _DEFAULT_TOKEN_COUNT_MODEL, messages=[_to_wire_message(m) for m in messages])


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
    resolved = await _resolve(tier, override)

    async def make_call(fitted: list[Message]):
        return await litellm.acompletion(
            model=resolved.model,
            messages=[_to_wire_message(m) for m in fitted],
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            tools=tools,
            max_tokens=max_tokens,
            timeout=timeout,
            stream=True,
            stream_options={"include_usage": True},
            num_retries=0,  # call_with_retry below owns retry/backoff (Phase 6.13)
        )

    async def attempt():
        return await _call_with_rate_limit_self_heal(resolved, messages, max_tokens, make_call)

    # Retry/backoff only covers getting the stream started — once the first
    # chunk has been handed to a caller (the Companion's live turn), there
    # is no way to un-yield it, so a mid-stream failure still propagates
    # unretried exactly as before this change.
    response = await call_with_retry(
        _get_provider_limiter(resolved.provider), attempt, retryable_exceptions=_LLM_RETRYABLE_EXCEPTIONS, parse_wait_seconds=_parse_wait_seconds
    )
    async for chunk in response:
        # `stream_options.include_usage` adds one trailing chunk per the
        # OpenAI streaming convention: `choices` is empty and `usage` is
        # populated — every content/tool-call chunk before it has an empty
        # `usage`. A provider that ignores the option (some OpenAI-compatible
        # local servers) simply never sends that chunk; `usage` stays `None`
        # on every `LLMChunk` and `turn_traces` records no token counts,
        # which is a normal degrade, not an error (HarnessPlan H1, §3.10).
        usage = getattr(chunk, "usage", None)
        if not chunk.choices:
            if usage is not None:
                yield LLMChunk(
                    usage=Usage(prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens),
                    model=resolved.model,
                )
            continue
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
