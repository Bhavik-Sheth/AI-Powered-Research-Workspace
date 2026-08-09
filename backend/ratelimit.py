"""Shared rate-limiting mechanism (Bug Fix Plan Phase 6.13) — the one place
`RateLimiter` and its two call wrappers live, so every outbound call this
app makes to a rate-limited free-tier API (literature search sources *and*
LLM providers) shares one proven mechanism instead of two divergent ones.

Not a domain module (see MODULES.md), same category as `config.py`:
infra plumbing every domain reaches for, owned by none of them.

Two call shapes exist because the two kinds of client raise failure
differently: `httpx` returns a `Response` with a status code you inspect;
`litellm`/most SDK clients raise an exception instead. `call_with_backoff`
covers the first (search's literature-API clients); `call_with_retry`
covers the second (the LLM Gateway). Both share the same `RateLimiter` and
the same backoff shape — provider-stated wait time when available,
exponential-with-jitter otherwise — so tuning one policy tunes both.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

_T = TypeVar("_T")


class RateLimiter:
    """A single-bucket async rate limiter: at most one call *starts* every
    `min_interval_s`, shared across every caller that holds this instance
    (one process-wide instance per external source/provider) — concurrent
    callers queue behind the same bucket rather than bursting past it.
    Once a call has started, it runs to completion independently; this
    only paces *start* times, so it costs nothing when a source is idle
    and only adds latency exactly when a burst would otherwise happen."""

    def __init__(self, min_interval_s: float):
        self._min_interval_s = min_interval_s
        self._lock = asyncio.Lock()
        self._next_allowed_at = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_allowed_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_allowed_at = now + self._min_interval_s


# Shared backoff shape for both wrappers below. Deliberately bounded
# (Rules.md: never an unbounded retry loop) — a caller with a longer job
# timeout to spend (e.g. `extract_card_job`'s 180s) passes a higher
# `max_retries`/`max_backoff_s` explicitly rather than this changing
# global defaults that every other caller would also inherit.
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BASE_BACKOFF_S = 1.0
_DEFAULT_MAX_BACKOFF_S = 20.0


def _backoff_delay(attempt: int, base_backoff_s: float, max_backoff_s: float) -> float:
    delay = min(base_backoff_s * (2**attempt), max_backoff_s)
    return delay + random.uniform(0, delay * 0.25)


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


def _retry_after_seconds(response: httpx.Response) -> float | None:
    header = response.headers.get("retry-after")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None


async def call_with_backoff(
    limiter: RateLimiter,
    make_request: Callable[[], Awaitable[httpx.Response]],
    *,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    base_backoff_s: float = _DEFAULT_BASE_BACKOFF_S,
    max_backoff_s: float = _DEFAULT_MAX_BACKOFF_S,
) -> httpx.Response:
    """Runs one rate-limited HTTP call, retrying up to `max_retries` times
    on a 429 or 5xx — the provider's own `Retry-After` when it sends one,
    otherwise exponential backoff with jitter. Any other status (including
    a real 4xx like 404) is returned on the first try, unretried — this
    wrapper only ever retries the transient cases; the caller's own
    `raise_for_status()` still decides what counts as a failure."""
    response: httpx.Response | None = None
    for attempt in range(max_retries + 1):
        await limiter.acquire()
        response = await make_request()
        if not _is_retryable_status(response.status_code) or attempt == max_retries:
            return response
        delay = _retry_after_seconds(response)
        if delay is None:
            delay = _backoff_delay(attempt, base_backoff_s, max_backoff_s)
        await asyncio.sleep(delay)
    assert response is not None  # loop always runs at least once (max_retries >= 0)
    return response


async def call_with_retry(
    limiter: RateLimiter,
    make_call: Callable[[], Awaitable[_T]],
    *,
    retryable_exceptions: tuple[type[BaseException], ...],
    parse_wait_seconds: Callable[[BaseException], float | None] | None = None,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    base_backoff_s: float = _DEFAULT_BASE_BACKOFF_S,
    max_backoff_s: float = _DEFAULT_MAX_BACKOFF_S,
) -> _T:
    """The exception-raising counterpart of `call_with_backoff`, for clients
    (like `litellm`) that signal a transient failure by raising rather than
    returning a status code. Retries up to `max_retries` times on any
    exception matching `retryable_exceptions` — `parse_wait_seconds` reads
    the provider's own stated wait time out of the exception when it names
    one (e.g. Groq's 429 body: "Please try again in 22.29s"), falling back
    to exponential backoff with jitter when it doesn't or isn't given.
    Any other exception propagates immediately, unretried — same shape as
    `call_with_backoff`'s "only the transient cases get retried"."""
    for attempt in range(max_retries + 1):
        await limiter.acquire()
        try:
            return await make_call()
        except retryable_exceptions as exc:
            if attempt == max_retries:
                raise
            delay = parse_wait_seconds(exc) if parse_wait_seconds else None
            if delay is None:
                delay = _backoff_delay(attempt, base_backoff_s, max_backoff_s)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable: loop always returns or raises on its last iteration")
