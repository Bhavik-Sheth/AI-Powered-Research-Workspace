"""Memory Index — cited rows from the query-time union of a project's paper
and project chunks (MODULES.md, D25). `chunk_and_embed_job` populates the
two memory tables; `query_memory` is `db.hybrid_retrieve` plus a
cross-encoder rerank, returning `CitedRow`s that always trace back to a
real source row.
"""

import uuid

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import Range

import db
from db.models import Conversations, Notes
from db.models import Paper as PaperRow
from db.models import PaperChunks, PaperContent, ProjectChunks
from memory.chunking import split_span
from memory.embedder import embed
from memory.models import CitedRow, SourceType
from memory.reranker import rerank

__all__ = ["chunk_and_embed_job", "query_memory", "query_memory_with_diagnostics"]

_RERANK_CANDIDATES = 40  # unchanged — recall stage (HarnessPlan H5, §3.5)

# HarnessPlan H5, §3.5: the bug fixed here was live in production — reranking
# 40 candidates and returning all 40 into a system message cost up to ~64k
# characters (~16k tokens) from a single retrieval on a 32k-context model.
# `_RETURN_K` is the hard cut after rerank; `_MIN_RERANK_SCORE` is an
# absolute floor below which nothing comes back at all.
_RETURN_K = 6

# UNCALIBRATED — flagged in the HarnessPlan H5 report. There is no live eval
# fixture with a real embedding/rerank endpoint in this environment to
# calibrate against (the plan requires calibration against the eval fixture,
# not a guess). `cross-encoder/ms-marco-MiniLM-L-6-v2` (memory/reranker.py)
# is a regression-trained cross-encoder whose `.predict()` returns raw,
# *unbounded* logits — not a 0-1 probability and not cosine similarity. The
# model's own published examples score clearly-relevant pairs positive
# (commonly +1 to +11) and clearly-irrelevant pairs negative (commonly -1 to
# -11), so 0.0 is the natural sign boundary for "this model thinks the pair
# is relevant at all" on this scale. This is a defensible placeholder, not a
# validated threshold — tune it against real retrieval before relying on it.
_MIN_RERANK_SCORE = 0.0

# HarnessPlan H5, §3.5 item 3: a single paper's many section chunks (or one
# very active note) must not fill every slot and crowd out other sources.
_MAX_PER_SOURCE_TYPE = 3


async def _chunk_abstract(paper_id: uuid.UUID) -> None:
    """The abstract chunk (Schema.md: `source_id` is literally `"abstract"`).
    Prefers the docling-parsed Abstract section (real offsets into
    `paper_content.full_text`, so the chunk can hand back a quote anchor);
    falls back to the `papers.abstract` metadata field, with a
    `char_span` that is not into `full_text`, when parsing found no such
    section — still retrievable, just without the anchor-locating property.
    """
    async with db.session() as session:
        content = await session.get(PaperContent, paper_id)
        section = next(
            (s for s in (content.sections if content else []) if s.get("heading", "").strip().lower() == "abstract"),
            None,
        )
        if section is not None:
            text = content.full_text[section["char_start"] : section["char_end"]]
            base_offset = section["char_start"]
        else:
            paper = await session.get(PaperRow, paper_id)
            if paper is None or not paper.abstract:
                return
            text, base_offset = paper.abstract, 0

        spans = split_span(text)
        if not spans:
            return
        # HarnessPlan H5, §3.5 item 4: the section heading is prefixed onto
        # the copy handed to the embedder only — matching improves when a
        # question is phrased by section ("what's their evaluation setup").
        # The stored `text_` below stays the bare original so it keeps
        # validating verbatim against the source (D24) — only the embedded
        # copy ever carries the prefix.
        vectors = await embed([f"Abstract: {text[s:e]}" for s, e in spans])
        for (start, end), vector in zip(spans, vectors):
            session.add(
                PaperChunks(
                    paper_id=paper_id,
                    source_type="abstract",
                    source_id="abstract",
                    char_span=Range(base_offset + start, base_offset + end),
                    section_heading="Abstract",
                    text_=text[start:end],
                    embedding=vector,
                )
            )


async def _chunk_paper_sections(paper_id: uuid.UUID) -> None:
    """Every non-abstract section, sub-split to the token budget (D25)."""
    async with db.session() as session:
        content = await session.get(PaperContent, paper_id)
        if content is None:
            return

        for sec in content.sections:
            if sec.get("heading", "").strip().lower() == "abstract":
                continue
            section_text = content.full_text[sec["char_start"] : sec["char_end"]]
            spans = split_span(section_text)
            if not spans:
                continue
            # HarnessPlan H5, §3.5 item 4 — see `_chunk_abstract`'s comment:
            # only the embedded copy gets the heading prefix; `text_` below
            # stores the bare original.
            heading = sec.get("heading")
            prefix = f"{heading}: " if heading else ""
            vectors = await embed([f"{prefix}{section_text[s:e]}" for s, e in spans])
            for (start, end), vector in zip(spans, vectors):
                session.add(
                    PaperChunks(
                        paper_id=paper_id,
                        source_type="paper_section",
                        source_id=sec["section_id"],
                        char_span=Range(sec["char_start"] + start, sec["char_start"] + end),
                        section_heading=sec.get("heading"),
                        text_=section_text[start:end],
                        embedding=vector,
                    )
                )


async def _chunk_note(note_id: uuid.UUID) -> None:
    async with db.session() as session:
        note = await session.get(Notes, note_id)
        if note is None or not note.body:
            return
        project_id = note.project_id
        spans = split_span(note.body)
        # Delete-then-insert (HarnessPlan H4, §3.3/§4): a note can be
        # re-chunked (an edit re-enqueues this job), and the prior chunk
        # rows for it must not survive alongside the fresh ones — same
        # transaction as the inserts below, so a mid-write failure leaves
        # either the old rows or the new ones, never a duplicate mix.
        await session.execute(delete(ProjectChunks).where(ProjectChunks.source_type == "note", ProjectChunks.source_id == note_id))
        if not spans:
            return
        vectors = await embed([note.body[s:e] for s, e in spans])
        for (start, end), vector in zip(spans, vectors):
            session.add(
                ProjectChunks(
                    project_id=project_id,
                    source_type="note",
                    source_id=note_id,
                    char_span=Range(start, end),
                    text_=note.body[start:end],
                    embedding=vector,
                )
            )


async def _chunk_conversation_summary(conversation_id: uuid.UUID) -> None:
    """A no-op until the conversation has a `summary` (D18 node 4: verbatim
    turns are truth, the summary is only what gets embedded) — legal, not
    an error, same as an empty retrieval result."""
    async with db.session() as session:
        conversation = await session.get(Conversations, conversation_id)
        # HarnessPlan H4, §3.3/§4: compaction re-enqueues this job every
        # time it writes a new rolling summary, so this is the first caller
        # that re-runs for the same `source_id` — the prior chunk rows for
        # this conversation must be replaced, not appended to, on every
        # run. Same transaction as the inserts below: delete unconditionally
        # (a conversation whose summary was somehow cleared should not keep
        # stale chunks retrievable either), then re-chunk if there is a
        # summary to chunk.
        await session.execute(
            delete(ProjectChunks).where(
                ProjectChunks.source_type == "conversation_summary", ProjectChunks.source_id == conversation_id
            )
        )
        if conversation is None or not conversation.summary:
            return
        project_id = conversation.project_id
        spans = split_span(conversation.summary)
        if not spans:
            return
        vectors = await embed([conversation.summary[s:e] for s, e in spans])
        for (start, end), vector in zip(spans, vectors):
            session.add(
                ProjectChunks(
                    project_id=project_id,
                    source_type="conversation_summary",
                    source_id=conversation_id,
                    char_span=Range(start, end),
                    text_=conversation.summary[start:end],
                    embedding=vector,
                )
            )


async def chunk_and_embed_job(_ctx: dict, *, source_type: SourceType, source_id: str) -> None:
    """Chunks and embeds one artifact with `gte-modernbert-base` (MODULES.md).
    `note` and `conversation_summary` re-index on every run (HarnessPlan H4:
    delete this artifact's existing `project_chunks` rows, then insert the
    freshly chunked ones, in one transaction) — the only two source types a
    caller re-enqueues for the same `source_id`. `abstract`/`paper_section`
    still assume one run per paper; give them the same treatment if a
    caller ever re-enqueues those too."""
    sid = uuid.UUID(source_id)
    if source_type == "abstract":
        await _chunk_abstract(sid)
    elif source_type == "paper_section":
        await _chunk_paper_sections(sid)
    elif source_type == "note":
        await _chunk_note(sid)
    elif source_type == "conversation_summary":
        await _chunk_conversation_summary(sid)
    elif source_type == "experiment":
        raise NotImplementedError("experiment chunking lands with Phase 2 — no experiments table exists yet")
    else:
        raise ValueError(f"unknown source_type {source_type!r}")


def _to_cited_row(row: dict) -> CitedRow:
    return CitedRow(
        id=row["id"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        paper_id=row["paper_id"],
        text=row["text"],
        section_heading=row["section_heading"],
        char_start=row["char_start"],
        char_end=row["char_end"],
    )


def _select_top_k(
    ranked: list[tuple[CitedRow, float]],
    *,
    k: int = _RETURN_K,
    min_score: float = _MIN_RERANK_SCORE,
    max_per_type: int = _MAX_PER_SOURCE_TYPE,
) -> list[tuple[CitedRow, float]]:
    """The output-stage cut (HarnessPlan H5, §3.5) — `ranked` is every
    reranked candidate, highest score first. Applies the absolute floor
    first (`ranked` is sorted descending, so the first row below `min_score`
    means every row after it also fails — stop there), then caps how many
    rows any one `source_type` contributes so a 12-section paper cannot
    occupy every slot, then hard-cuts to `k`. Pure function over scored rows
    — no DB, no model call — so it is directly unit-testable. Returning
    fewer than `k` rows, including zero, is a normal outcome."""
    selected: list[tuple[CitedRow, float]] = []
    per_type: dict[str, int] = {}
    for row, score in ranked:
        if score < min_score:
            break
        if per_type.get(row.source_type, 0) >= max_per_type:
            continue
        selected.append((row, score))
        per_type[row.source_type] = per_type.get(row.source_type, 0) + 1
        if len(selected) >= k:
            break
    return selected


async def _ranked_candidates(
    project_id: uuid.UUID, query: str, types: list[str] | None
) -> tuple[list[tuple[CitedRow, float]], int]:
    """Recall (`db.hybrid_retrieve`) plus cross-encoder rerank (D25), sorted
    highest score first. Returns `(ranked, candidate_count)` — the shared
    core `query_memory` and `query_memory_with_diagnostics` both build on,
    so the recall/rerank logic exists in exactly one place."""
    (query_vector,) = await embed([query])

    async with db.session() as session:
        candidates = await db.hybrid_retrieve(session, project_id, query, query_vector, types=types, limit=_RERANK_CANDIDATES)

    if not candidates:
        return [], 0

    scores = await rerank(query, [c["text"] for c in candidates])
    ranked = sorted(zip([_to_cited_row(c) for c in candidates], scores), key=lambda pair: pair[1], reverse=True)
    return ranked, len(candidates)


async def query_memory(project_id: uuid.UUID, query: str, types: list[str] | None = None) -> list[CitedRow]:
    """`paper_chunks(papers in P) ∪ project_chunks(P)`, hybrid-fused,
    cross-encoder reranked, then cut to `_RETURN_K` above `_MIN_RERANK_SCORE`
    with per-`source_type` balancing (D25; HarnessPlan H5, §3.5). No
    matching rows above the floor is a legal empty list."""
    ranked, _ = await _ranked_candidates(project_id, query, types)
    return [row for row, _ in _select_top_k(ranked)]


async def query_memory_with_diagnostics(
    project_id: uuid.UUID, query: str, types: list[str] | None = None
) -> tuple[list[CitedRow], dict]:
    """Same result as `query_memory`, plus the `turn_traces.retrieval`
    diagnostics (HarnessPlan H5, §3.10): `{query, candidates, returned,
    top_score}` — `candidates` is how many rows survived hybrid fusion
    before reranking, `top_score` is the highest-scoring returned row's
    score (`None` when nothing was returned). Only the `query_memory` tool
    needs this; the REST route (`api/memory.py`) has no `turn_traces` row to
    write it into and keeps calling the plain `query_memory`."""
    ranked, candidate_count = await _ranked_candidates(project_id, query, types)
    selected = _select_top_k(ranked)
    diagnostics = {
        "query": query,
        "candidates": candidate_count,
        "returned": len(selected),
        "top_score": selected[0][1] if selected else None,
    }
    return [row for row, _ in selected], diagnostics
