"""Research Feed — surfaces new papers matching a project's interest
profile, each stating why it surfaced (MODULES.md, D28).

Phase 5.1 shipped the fetch half: an interest profile inspectable and
user-editable as `{categories, keywords}`, lazily seeded from the project's
focus seed the first time it's read; and `poll_feed_job`, the catch-up-on-
launch job that fetches broadly by category since the last poll and dedupes
against the seen set. Phase 5.2 completes the deterministic rank (centroid
cosine + cross-encoder rerank of the top N, still no LLM anywhere in the
scoring path itself), adds `save_item`/`dismiss_item`, and the weekly
`interest_profile_reextract_job` that reconciles the profile with the
library the project has actually grown into.

MODULES.md's own "Depends on" line for this module names Database Layer,
Paper Pipeline and Memory Index. Two reach-throughs go slightly beyond that,
same as `backend/projects/__init__.py` already flags for itself: `save_item`
calls `projects.add_paper_to_project` (Paper Pipeline's `add_paper` is
global-scope only; project *membership* lives in Projects, the module
`POST /api/projects/:id/papers` already uses for the same two-step add), and
the centroid/rerank scoring reuses `memory.embedder.embed` directly rather
than duplicating the one place the fixed embedding model may load (Rules.md
invariant #1) — unlike the cross-encoder, which every module with a rerank
need duplicates on purpose (see `feed/reranker.py`).
"""

import asyncio
import math
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

import db
import papers
import projects
from db.models import FeedItems, Paper as PaperRow, PaperChunks, Project as ProjectRow, ProjectPapers, SeenSet
from feed.models import FeedItem, InterestProfile, RawFeedHit, WhyRelevant
from feed.reranker import rerank
from feed.sources import fetch_arxiv_by_category, fetch_openalex_by_category, fetch_s2_by_category
from llm import LLMError, Message, complete_structured
from memory.embedder import embed
from papers import resolve_canonical_id
from papers.models import PaperInput, SourceIds

_MAX_RESULTS_PER_CATEGORY = 30
_RERANK_TOP_N = 50
_MAX_REEXTRACT_CHARS = 40_000

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


def _select_unseen(candidates: dict[str, RawFeedHit], unseen_ids: set[str]) -> dict[str, RawFeedHit]:
    """Of `candidates` (already deduped within this poll's own fetch by
    `_dedupe`), only the ones `db.filter_unseen`'s seen-set anti-join didn't
    already know about. This is what makes a second catch-up poll over a
    window overlapping the first one a no-op for any item the first poll
    already surfaced, the user already read or saved to the library, or the
    user already dismissed — the seen-set row from that earlier poll (or
    action) is still there, so the item never reaches `feed_items` twice."""
    return {canonical_id: hit for canonical_id, hit in candidates.items() if canonical_id in unseen_ids}


def score_candidate(hit: RawFeedHit, profile: InterestProfile, similarity: float = 0.0) -> tuple[float, WhyRelevant]:
    """The synonym-keyword-match and category-match terms of the
    deterministic rank (D28) — no LLM anywhere in this path. `similarity`
    (embedding centroid cosine) is folded in by `_add_centroid_similarity`,
    and the cross-encoder rerank of the top N by `_rerank_top`, both below."""
    text = f"{hit.title}\n{hit.abstract or ''}".lower()
    matched_keywords = [keyword for keyword in profile.keywords if keyword.lower() in text]
    matched_categories = [hit.category] if hit.category in profile.categories else []
    why_relevant = WhyRelevant(matched_keywords=matched_keywords, matched_categories=matched_categories, similarity=similarity)
    score = float(len(matched_keywords) + len(matched_categories)) + similarity
    return score, why_relevant


_ScoredCandidate = tuple[str, RawFeedHit, float, WhyRelevant]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


async def _add_centroid_similarity(candidates: list[_ScoredCandidate], corpus_centroid: list[float] | None) -> list[_ScoredCandidate]:
    """Folds the embedding-centroid cosine term into each candidate's score
    (D28) — a no-op until the project's library is non-empty (`corpus_centroid`
    stays NULL, Schema.md)."""
    if corpus_centroid is None or not candidates:
        return candidates
    vectors = await embed([f"{hit.title}\n{hit.abstract or ''}" for _, hit, _, _ in candidates])
    updated = []
    for (canonical_id, hit, score, why_relevant), vector in zip(candidates, vectors):
        similarity = _cosine_similarity(vector, corpus_centroid)
        updated.append((canonical_id, hit, score + similarity, why_relevant.model_copy(update={"similarity": similarity})))
    return updated


async def _rerank_top(profile: InterestProfile, candidates: list[_ScoredCandidate]) -> list[_ScoredCandidate]:
    """Cross-encoder reranks the top `_RERANK_TOP_N` (by keyword+category+
    cosine score so far) against the profile's own terms as the query,
    mirroring Search Federation's `_rank` (D15) — the tail is returned
    unranked-appended, same as there."""
    query = " ".join(profile.keywords) or " ".join(profile.categories)
    if not query or not candidates:
        return candidates
    ranked = sorted(candidates, key=lambda c: c[2], reverse=True)
    head, tail = ranked[:_RERANK_TOP_N], ranked[_RERANK_TOP_N:]
    rerank_scores = await rerank(query, [f"{hit.title}\n{hit.abstract or ''}" for _, hit, _, _ in head])
    head = [(cid, hit, score + rerank_score, why) for (cid, hit, score, why), rerank_score in zip(head, rerank_scores)]
    return head + tail


def _metadata(hit: RawFeedHit) -> dict:
    return {
        "source_ids": hit.source_ids.model_dump(),
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
        project = await session.get(ProjectRow, pid)
        corpus_centroid = list(project.corpus_centroid) if project.corpus_centroid is not None else None
    if not profile.categories:
        return

    candidates = _dedupe(await _fetch_all_categories(profile.categories, since_dt))
    if not candidates:
        return

    async with db.session() as session:
        unseen_ids = set(await db.filter_unseen(session, pid, list(candidates.keys())))

    scored: list[_ScoredCandidate] = []
    for canonical_id, hit in _select_unseen(candidates, unseen_ids).items():
        score, why_relevant = score_candidate(hit, profile)
        if not why_relevant.matched_keywords and not why_relevant.matched_categories:
            continue  # Never rendered without a reason, by construction (MODULES.md).
        scored.append((canonical_id, hit, score, why_relevant))

    scored = await _add_centroid_similarity(scored, corpus_centroid)
    scored = await _rerank_top(profile, scored)

    async with db.session() as session:
        polled_at = datetime.now(timezone.utc)
        for canonical_id, hit, score, why_relevant in scored:
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


def _feed_item_from_row(row: FeedItems) -> FeedItem:
    return FeedItem(
        id=row.id,
        project_id=row.project_id,
        canonical_id=row.canonical_id,
        title=row.title,
        metadata=row.metadata_,
        score=row.score,
        why_relevant=WhyRelevant(**row.why_relevant),
        state=row.state,
        polled_at=row.polled_at,
    )


async def get_feed(session: AsyncSession, project_id: uuid.UUID) -> list[FeedItem]:
    """`state='new'` items, best score first (MODULES.md)."""
    rows = (
        await session.scalars(
            select(FeedItems).where(FeedItems.project_id == project_id, FeedItems.state == "new").order_by(FeedItems.score.desc())
        )
    ).all()
    return [_feed_item_from_row(row) for row in rows]


def _paper_input_from_item(item: FeedItems) -> PaperInput:
    metadata = item.metadata_
    return PaperInput(
        source_ids=SourceIds(**metadata.get("source_ids", {})),
        title=item.title,
        abstract=metadata.get("abstract"),
        source_url=metadata.get("source_url"),
        pdf_url=metadata.get("pdf_url"),
    )


async def _recompute_corpus_centroid(session: AsyncSession, project_id: uuid.UUID) -> list[float] | None:
    """The mean of the project library's own abstract embeddings (Schema.md
    `projects.corpus_centroid`) — one vector per paper, so a long paper's
    many section chunks don't outweigh a short one's in the mean. `NULL`
    while the library holds no paper with a chunked abstract yet (a just-
    saved paper's embed job may still be in flight)."""
    vectors = (
        await session.scalars(
            select(PaperChunks.embedding)
            .join(ProjectPapers, ProjectPapers.paper_id == PaperChunks.paper_id)
            .where(ProjectPapers.project_id == project_id, PaperChunks.source_type == "abstract")
        )
    ).all()
    if not vectors:
        return None
    dim = len(vectors[0])
    return [sum(vector[i] for vector in vectors) / len(vectors) for i in range(dim)]


async def save_item(session: AsyncSession, project_id: uuid.UUID, item_id: uuid.UUID) -> None:
    """Adds the item to the library and shifts the corpus centroid
    (MODULES.md) — idempotent, so re-saving an already-saved item is a
    harmless no-op rather than a duplicate-row error."""
    item = await session.get(FeedItems, item_id)
    if item is None or item.project_id != project_id:
        raise ValueError(f"no feed item {item_id} in project {project_id}")

    paper = await papers.add_paper(session, _paper_input_from_item(item))
    await projects.add_paper_to_project(session, project_id, paper.id)
    await session.execute(
        insert(SeenSet)
        .values(project_id=project_id, canonical_id=item.canonical_id, reason="library")
        .on_conflict_do_nothing(index_elements=["project_id", "canonical_id", "reason"])
    )
    item.state = "saved"

    project = await session.get(ProjectRow, project_id)
    project.corpus_centroid = await _recompute_corpus_centroid(session, project_id)
    await session.flush()


async def dismiss_item(session: AsyncSession, project_id: uuid.UUID, item_id: uuid.UUID) -> None:
    """Never resurfaces (MODULES.md) — idempotent for the same reason as `save_item`."""
    item = await session.get(FeedItems, item_id)
    if item is None or item.project_id != project_id:
        raise ValueError(f"no feed item {item_id} in project {project_id}")
    item.state = "dismissed"
    await session.execute(
        insert(SeenSet)
        .values(project_id=project_id, canonical_id=item.canonical_id, reason="dismissed")
        .on_conflict_do_nothing(index_elements=["project_id", "canonical_id", "reason"])
    )
    await session.flush()


_REEXTRACT_PROMPT = (
    "A researcher's project library has grown since its interest profile was last set. From these "
    "titles and abstracts, extract the arXiv categories and keywords (with common synonyms and "
    "abbreviation expansions) that broadly describe what this project is now actually about."
)


async def interest_profile_reextract_job(_ctx: dict, *, project_id: str) -> None:
    """Weekly reconciliation (D28, PRD §14): re-derives categories/keywords
    from the project's current library and unions them into the existing
    profile — additive, so a manual edit is never silently discarded."""
    pid = uuid.UUID(project_id)
    async with db.session() as session:
        rows = (
            await session.execute(
                select(PaperRow.title, PaperRow.abstract)
                .join(ProjectPapers, ProjectPapers.paper_id == PaperRow.id)
                .where(ProjectPapers.project_id == pid)
            )
        ).all()
        current = await get_interest_profile(session, pid)

    corpus_text = "\n".join(f"{title}: {abstract or ''}" for title, abstract in rows).strip()
    if not corpus_text:
        return  # Nothing to reconcile against yet.

    try:
        extraction = await complete_structured(
            messages=[
                Message(role="system", content=_REEXTRACT_PROMPT),
                Message(role="user", content=corpus_text[:_MAX_REEXTRACT_CHARS]),
            ],
            schema=_ProfileExtraction,
            tier="auxiliary",
            timeout=30,
        )
    except (*LLMError, RuntimeError):
        return  # Keep the existing profile over failing the job.

    merged = InterestProfile(
        categories=sorted(set(current.categories) | set(extraction.categories)),
        keywords=sorted(set(current.keywords) | set(extraction.keywords)),
    )
    async with db.session() as session:
        await update_interest_profile(session, pid, merged)
