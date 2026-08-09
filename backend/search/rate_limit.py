"""Per-source rate-limiter instances for every external literature API this
app calls on a free/unauthenticated tier (Bug Fix Plan Phase 6.12) — the
mechanism itself (`RateLimiter`, `call_with_backoff`) lives in the
top-level `ratelimit` module, shared with the LLM Gateway (Phase 6.13);
this file only tunes it per source.
"""

from ratelimit import RateLimiter, call_with_backoff

__all__ = ["RateLimiter", "call_with_backoff", "ARXIV_LIMITER", "OPENALEX_LIMITER", "S2_LIMITER_WITH_KEY", "S2_LIMITER_NO_KEY", "FIRECRAWL_LIMITER"]

# Conservative per-source ceilings, one call every N seconds. Deliberately
# cautious: queuing a call for an extra second costs nothing here, burning
# a free-tier quota on a burst costs the rest of the day.
ARXIV_LIMITER = RateLimiter(3.0)  # arXiv's own published courtesy guideline
OPENALEX_LIMITER = RateLimiter(0.15)  # ~6-7/s, comfortably under the ~10/s "polite pool" ceiling once `mailto` is set (see search_openalex)
S2_LIMITER_WITH_KEY = RateLimiter(1.0)  # S2's documented 1 req/s per API key
S2_LIMITER_NO_KEY = RateLimiter(6.0)  # a conservative slice of S2's shared unauthenticated pool (5,000 req/5min shared across every unauthenticated user on the internet)
FIRECRAWL_LIMITER = RateLimiter(2.0)
