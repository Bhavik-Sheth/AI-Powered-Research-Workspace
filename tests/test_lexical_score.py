"""Deterministic lexical-score fallback ranking (Phase 6.1/D21 amendment) —
fixture-based, no network/DB/LLM. Not one of the four canonical D24/D25/
D33/D29 suites (Rules.md); a small, separate file colocated with them.
"""

from papers.models import SourceIds
from search.lexical_score import lexical_score, title_overlap
from search.models import RawHit


def _hit(title: str, citation_count: int | None = None) -> RawHit:
    return RawHit(source_ids=SourceIds(arxiv_id="1706.03762"), title=title, citation_count=citation_count)


class TestTitleOverlap:
    def test_identical_titles_overlap_fully(self):
        assert title_overlap("Attention Is All You Need", "attention is all you need") == 1.0

    def test_disjoint_titles_have_no_overlap(self):
        assert title_overlap("Attention Is All You Need", "Deep Residual Learning") == 0.0

    def test_partial_overlap_is_between_zero_and_one(self):
        score = title_overlap("Attention Is All You Need", "Attention Is Not All You Need Sometimes")
        assert 0.0 < score < 1.0

    def test_empty_string_has_no_overlap(self):
        assert title_overlap("Attention Is All You Need", "") == 0.0


class TestLexicalScore:
    def test_exact_title_match_outranks_partial_overlap_and_citations(self):
        exact = _hit("Attention Is All You Need", citation_count=0)
        partial_high_citations = _hit("A Survey of Attention Mechanisms", citation_count=100_000)
        assert lexical_score("attention is all you need", exact) > lexical_score(
            "attention is all you need", partial_high_citations
        )

    def test_exact_match_is_case_insensitive(self):
        hit = _hit("ATTENTION IS ALL YOU NEED")
        assert lexical_score("attention is all you need", hit) == lexical_score("Attention Is All You Need", hit)

    def test_higher_overlap_outranks_lower_overlap_at_equal_citations(self):
        closer = _hit("Attention Is All You Need", citation_count=10)
        further = _hit("A Study of Neural Networks", citation_count=10)
        assert lexical_score("attention is all you need", closer) > lexical_score("attention is all you need", further)

    def test_citation_count_breaks_ties_between_equal_overlap(self):
        low = _hit("Some Unrelated Paper Title", citation_count=1)
        high = _hit("Some Unrelated Paper Title", citation_count=10_000)
        assert lexical_score("query terms not present", high) > lexical_score("query terms not present", low)

    def test_missing_citation_count_is_treated_as_zero(self):
        hit = _hit("Some Unrelated Paper Title", citation_count=None)
        # Must not raise, and must sit at the floor of the citation component.
        assert lexical_score("query terms not present", hit) >= 0.0

    def test_deterministic_across_calls(self):
        hit = _hit("Attention Is All You Need", citation_count=50_000)
        assert lexical_score("attention", hit) == lexical_score("attention", hit)
