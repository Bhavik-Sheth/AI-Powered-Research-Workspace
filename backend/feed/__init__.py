"""Research Feed — surfaces new papers matching a project's interest
profile, each stating why it surfaced (MODULES.md, D28).

Phase 5.1 ships the fetch half: an interest profile inspectable and
user-editable as `{categories, keywords}`, lazily seeded from the project's
focus seed the first time it's read; and `poll_feed_job`, the catch-up-on-
launch job that fetches broadly by category since the last poll and dedupes
against the seen set. Phase 5.2 completes the ranking (centroid cosine +
cross-encoder rerank) and adds `save_item`/`dismiss_item`.
"""

import asyncio
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import db
from db.models import FeedItems, Project as ProjectRow, SeenSet
from feed.models import InterestProfile, RawFeedHit, WhyRelevant
from feed.sources import fetch_arxiv_by_category, fetch_openalex_by_category, fetch_s2_by_category
from llm import LLMError, Message, complete_structured
from papers import resolve_canonical_id

_MAX_RESULTS_PER_CATEGORY = 30

_SEED_PROMPT = (
    "A researcher described their project's focus in their own words. Extract a broad interest "
    "profile for surfacing new papers on it.\n\n"
    "`categories`: arXiv subject-class codes (e.g. cs.CL, cs.LG, cs.AI, cs.CV, stat.ML) that broadly "
    "cover this focus — for a broad-recall category fetch, not a narrow keyword search. Prefer a "
    "few broad categories over many narrow ones.\n\n"
    "`keywords`: the focus's own terms plus their common synonyms and abbreviation expansions (e.g. "
    "RAG -> retrieval-augmented generation), for matching against fetched titles and abstracts."
)


class _ProfileExtraction(BaseModel):
    categories: list[str] = []
    keywords: list[str] = []


async def _seed_from_focus_seed(focus_seed: str) -> InterestProfile:
    try:
        extraction = await complete_structured(
            messages=[Message(role="system", content=_SEED_PROMPT), Message(role="user", content=focus_seed)],
            schema=_ProfileExtraction,
            tier="auxiliary",
            timeout=20,
        )
    except (*LLMError, RuntimeError):
        return InterestProfile()  # No profile yet beats failing the whole read over an optional seed.
    return InterestProfile(categories=extraction.categories, keywords=extraction.keywords)


async def get_interest_profile(session: AsyncSession, project_id: uuid.UUID) -> InterestProfile:
    """Seeds the profile from `focus_seed` on first read if it is still the
    untouched default and a focus seed exists (PRD US13); a no-op on every
    later read once real content is stored."""
    project = await session.get(ProjectRow, project_id)
    if project is None:
        raise ValueError(f"no project {project_id}")

    profile = InterestProfile(**project.interest_profile)
    if not profile.categories and not profile.keywords and project.focus_seed:
        profile = await _seed_from_focus_seed(project.focus_seed)
        project.interest_profile = profile.model_dump()
        await session.flush()
    return profile


async def update_interest_profile(session: AsyncSession, project_id: uuid.UUID, profile: InterestProfile) -> InterestProfile:
    project = await session.get(ProjectRow, project_id)
    if project is None:
        raise ValueError(f"no project {project_id}")
    project.interest_profile = profile.model_dump()
    await session.flush()
    return profile


async def _fetch_all_categories(categories: list[str], since: datetime) -> list[RawFeedHit]:
    """Fans out arXiv/OpenAlex/S2 per category, in parallel; a failing
    source/category combination degrades silently — this is a scheduled
    background job, never a live request a failure needs to be reported to."""
    tasks = [
        fetcher(category, since, _MAX_RESULTS_PER_CATEGORY)
        for category in categories
        for fetcher in (fetch_arxiv_by_category, fetch_openalex_by_category, fetch_s2_by_category)
    ]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    return [hit for outcome in outcomes if not isinstance(outcome, BaseException) for hit in outcome]


def _dedupe(hits: list[RawFeedHit]) -> dict[str, RawFeedHit]:
    """Keeps the first hit seen per canonical id (D25); a hit with no
    resolvable source id is dropped rather than corrupting the dedup key."""
    seen: dict[str, RawFeedHit] = {}
    for hit in hits:
        try:
            canonical_id = resolve_canonical_id(hit.source_ids)
        except ValueError:
            continue
        seen.setdefault(canonical_id, hit)
    return seen


def score_candidate(hit: RawFeedHit, profile: InterestProfile, similarity: float = 0.0) -> tuple[float, WhyRelevant]:
    """The deterministic rank (D28) — no LLM anywhere in this path. Phase 5.1
    contributes the synonym-keyword-match and category-match terms;
    `similarity` (embedding centroid cosine) defaults to 0 until Phase 5.2
    wires the corpus centroid and the cross-encoder rerank of the top N in
    on top of it."""
    text = f"{hit.title}\n{hit.abstract or ''}".lower()
    matched_keywords = [keyword for keyword in profile.keywords if keyword.lower() in text]
    matched_categories = [hit.category] if hit.category in profile.categories else []
    why_relevant = WhyRelevant(matched_keywords=matched_keywords, matched_categories=matched_categories, similarity=similarity)
    score = float(len(matched_keywords) + len(matched_categories)) + similarity
    return score, why_relevant


def _metadata(hit: RawFeedHit) -> dict:
    return {
        "abstract": hit.abstract,
        "venue": hit.venue,
        "source_url": hit.source_url,
        "pdf_url": hit.pdf_url,
        "published_at": hit.published_at.isoformat() if hit.published_at else None,
    }


async def poll_feed_job(_ctx: dict, *, project_id: str, since: str) -> None:
    """Catch-up-on-launch: fetches since `since`, ranks, dedupes, writes
    `feed_items`. Never runs in a live request path (D9/D28)."""
    pid = uuid.UUID(project_id)
    since_dt = datetime.fromisoformat(since)

    async with db.session() as session:
        profile = await get_interest_profile(session, pid)
    if not profile.categories:
        return

    candidates = _dedupe(await _fetch_all_categories(profile.categories, since_dt))
    if not candidates:
        return

    async with db.session() as session:
        unseen_ids = set(await db.filter_unseen(session, pid, list(candidates.keys())))
        polled_at = datetime.now(timezone.utc)
        for canonical_id, hit in candidates.items():
            if canonical_id not in unseen_ids:
                continue
            score, why_relevant = score_candidate(hit, profile)
            if not why_relevant.matched_keywords and not why_relevant.matched_categories:
                continue  # Never rendered without a reason, by construction (MODULES.md).
            session.add(
                FeedItems(
                    project_id=pid,
                    canonical_id=canonical_id,
                    title=hit.title,
                    metadata_=_metadata(hit),
                    score=score,
                    why_relevant=why_relevant.model_dump(),
                    state="new",
                    polled_at=polled_at,
                )
            )
            session.add(SeenSet(project_id=pid, canonical_id=canonical_id, reason="surfaced"))
