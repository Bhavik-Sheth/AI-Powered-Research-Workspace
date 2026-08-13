"""Deterministic scenarios for `memory._select_top_k` (HarnessPlan H5, §3.5)
— the output-stage cut that fixes the live bug where `query_memory`
reranked 40 candidates and returned all 40. Pure function over
already-scored `CitedRow`s: no DB, no embedding call, no rerank model, so
this runs in CI on every commit like the plan's "deterministic" tier
intends.
"""

import uuid

from memory import _MAX_PER_SOURCE_TYPE, _MIN_RERANK_SCORE, _RETURN_K, _select_top_k
from memory.models import CitedRow, SourceType


def _row(source_type: SourceType = "note", source_id: str = "s", paper_id: uuid.UUID | None = None) -> CitedRow:
    return CitedRow(
        id=uuid.uuid4(),
        source_type=source_type,
        source_id=source_id,
        paper_id=paper_id,
        text="some retrieved text",
        section_heading=None,
        char_start=0,
        char_end=20,
    )


_SOURCE_TYPES: list[SourceType] = ["paper_section", "abstract", "note", "conversation_summary"]


def test_hard_cut_at_return_k():
    """40 reranked candidates, spread across source types so the per-type cap never binds and all
    comfortably above the floor, still returns at most `_RETURN_K` — the live bug this phase fixes
    (returning all 40 cost ~16k tokens from one retrieval)."""
    ranked = [
        (_row(source_type=_SOURCE_TYPES[i % len(_SOURCE_TYPES)], source_id=str(i)), 10.0 - i * 0.1) for i in range(40)
    ]
    selected = _select_top_k(ranked)
    assert len(selected) == _RETURN_K
    # highest-scoring rows first
    assert [score for _, score in selected] == sorted((score for _, score in selected), reverse=True)


def test_below_floor_returns_nothing():
    """Every candidate below `_MIN_RERANK_SCORE` is a legal empty result, not an error."""
    ranked = [(_row(source_id=str(i)), _MIN_RERANK_SCORE - 1 - i) for i in range(5)]
    assert _select_top_k(ranked) == []


def test_floor_is_a_hard_cutoff_not_a_filter():
    """Once a descending-sorted row falls below the floor, every row after it is also dropped — the
    floor check does not skip a low row and keep scanning past it."""
    ranked = [
        (_row(source_id="a"), _MIN_RERANK_SCORE + 5),
        (_row(source_id="b"), _MIN_RERANK_SCORE - 1),
        (_row(source_id="c"), _MIN_RERANK_SCORE + 3),  # would pass the floor alone, but comes after a failure
    ]
    selected = _select_top_k(ranked)
    assert [row.source_id for row, _ in selected] == ["a"]


def test_per_source_type_cap():
    """One source_type cannot fill every slot — a 12-section paper's chunks are capped at `_MAX_PER_SOURCE_TYPE`
    even though they dominate the top of the ranking, so other source types still get a slot."""
    paper_chunks = [(_row(source_type="paper_section", source_id=str(i)), 10.0 - i * 0.01) for i in range(10)]
    note_chunk = (_row(source_type="note", source_id="note-1"), 1.0)
    ranked = [*paper_chunks, note_chunk]
    selected = _select_top_k(ranked)
    types = [row.source_type for row, _ in selected]
    assert types.count("paper_section") == _MAX_PER_SOURCE_TYPE
    assert "note" in types  # not crowded out despite scoring below every paper_section candidate


def test_fewer_than_k_is_legal():
    """Fewer candidates than `_RETURN_K` above the floor returns exactly what qualified, not padded or errored."""
    ranked = [(_row(source_id="only"), _MIN_RERANK_SCORE + 1)]
    selected = _select_top_k(ranked)
    assert len(selected) == 1
