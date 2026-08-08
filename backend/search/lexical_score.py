"""Deterministic lexical scoring (Phase 6.1/D21 amendment) — no LLM, no
network, a pure function over already-fetched metadata.

Ranks the arXiv/OpenAlex/S2 fan-out when Firecrawl is unavailable (missing
key, exhausted quota, failed call), so a degraded search is still ranked
rather than falling back to raw source order. Also doubles as the
title-overlap measure `search/__init__.py` uses to match a Firecrawl hit to
its enrichment candidate from that same fan-out.
"""

import math
import re

from search.models import RawHit

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Small, bounded weights so exact title match always dominates, phrase
# overlap breaks ties among near-matches, and citation count only tips a
# near-tie — never overrides a worse title match.
_EXACT_MATCH_WEIGHT = 100.0
_OVERLAP_WEIGHT = 10.0
_CITATION_LOG_DIVISOR = 20.0


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _normalize_title(title: str) -> str:
    return " ".join(sorted(_tokenize(title)))


def title_overlap(a: str, b: str) -> float:
    """Token overlap ratio (Jaccard: intersection / union) between two
    strings' titles — a deterministic, order-independent phrase-overlap
    measure. 0.0 when either side has no tokens."""
    tokens_a, tokens_b = _tokenize(a), _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def lexical_score(query: str, hit: RawHit) -> float:
    """Higher is more relevant. An exact case-insensitive full-title match
    against `query` scores highest; otherwise ranked by token phrase
    overlap between `query` and `hit.title`, with citation count as a small
    tiebreaker component."""
    exact = 1.0 if _normalize_title(hit.title) == _normalize_title(query) else 0.0
    overlap = title_overlap(query, hit.title)
    citation_component = math.log1p(hit.citation_count or 0) / _CITATION_LOG_DIVISOR
    return exact * _EXACT_MATCH_WEIGHT + overlap * _OVERLAP_WEIGHT + citation_component
