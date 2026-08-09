"""Shared rate limiting + retry-with-backoff for every external literature
API this app calls on a free/unauthenticated tier (Bug Fix Plan Phase
6.12). Each source gets its own conservative token-bucket ceiling (tuned to
that provider's documented or observed free-tier limit — see the
per-source comments below and `search/sources.py`'s own notes) plus
bounded exponential-backoff-with-jitter retries that honour a
`Retry-After` header when the provider sends one. Never an unbounded
retry loop (Rules.md) — `_MAX_RETRIES` bounds every call, and a still-429
after exhausting retries is returned to the caller exactly as a first-try
429 always was, so existing `raise_for_status()`/`httpx.HTTPError` handling
downstream is unchanged.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

import httpx


class RateLimiter:
    """A single-bucket async rate limiter: at most one call every
    `min_interval_s`, shared across every caller that holds this instance
    (one module-level instance per external source below) — concurrent
    callers queue behind the same bucket rather than bursting past it."""

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


# Conservative per-source ceilings, one call every N seconds. Deliberately
# cautious: queuing a call for an extra second costs nothing here, burning
# a free-tier quota on a burst costs the rest of the day.
ARXIV_LIMITER = RateLimiter(3.0)  # arXiv's own published courtesy guideline
OPENALEX_LIMITER = RateLimiter(0.15)  # ~6-7/s, comfortably under the ~10/s "polite pool" ceiling once `mailto` is set (see search_openalex)
S2_LIMITER_WITH_KEY = RateLimiter(1.0)  # S2's documented 1 req/s per API key
S2_LIMITER_NO_KEY = RateLimiter(6.0)  # a conservative slice of S2's shared unauthenticated pool (5,000 req/5min shared across every unauthenticated user on the internet)
FIRECRAWL_LIMITER = RateLimiter(2.0)

_MAX_RETRIES = 3
_BASE_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 20.0


def _is_retryable(status_code: int) -> bool:
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
    limiter: RateLimiter, make_request: Callable[[], Awaitable[httpx.Response]]
) -> httpx.Response:
    """Runs one rate-limited HTTP call, retrying up to `_MAX_RETRIES` times
    on a 429 or 5xx — the provider's own `Retry-After` when it sends one,
    otherwise exponential backoff with jitter. Any other status (including
    a real 4xx like 404) is returned on the first try, unretried — this
    wrapper only ever retries the transient cases; the caller's own
    `raise_for_status()` still decides what counts as a failure."""
    response: httpx.Response | None = None
    for attempt in range(_MAX_RETRIES + 1):
        await limiter.acquire()
        response = await make_request()
        if not _is_retryable(response.status_code) or attempt == _MAX_RETRIES:
            return response
        delay = _retry_after_seconds(response)
        if delay is None:
            delay = min(_BASE_BACKOFF_S * (2**attempt), _MAX_BACKOFF_S)
            delay += random.uniform(0, delay * 0.25)
        await asyncio.sleep(delay)
    assert response is not None  # loop always runs at least once (_MAX_RETRIES >= 0)
    return response
