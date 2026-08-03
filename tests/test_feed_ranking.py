"""D28 deterministic feed ranking suite (Rules.md) — fixture-based, no
network/DB/LLM. `score_candidate` is the synonym-keyword-match and
category-match half of the deterministic rank; the centroid-cosine and
cross-encoder-rerank halves load real embedding/rerank models and are
exercised by tracer fire instead, same reason the other four suites never
load an ML model either.
"""

from feed import score_candidate
from feed.models import InterestProfile, RawFeedHit
from papers.models import SourceIds


def _hit(category: str, title: str, abstract: str | None = None) -> RawFeedHit:
    return RawFeedHit(source_ids=SourceIds(arxiv_id="2401.00001"), category=category, title=title, abstract=abstract)


class TestKeywordMatch:
    def test_keyword_in_title_is_matched(self):
        hit = _hit("cs.CL", "A survey of retrieval-augmented generation")
        profile = InterestProfile(categories=[], keywords=["retrieval-augmented generation"])
        _, why = score_candidate(hit, profile)
        assert why.matched_keywords == ["retrieval-augmented generation"]

    def test_keyword_in_abstract_is_matched(self):
        hit = _hit("cs.CL", "TokTier", abstract="We study RAG pipelines at scale.")
        profile = InterestProfile(categories=[], keywords=["RAG"])
        _, why = score_candidate(hit, profile)
        assert why.matched_keywords == ["RAG"]

    def test_keyword_match_is_case_insensitive(self):
        hit = _hit("cs.CL", "Scaling rag pipelines")
        profile = InterestProfile(categories=[], keywords=["RAG"])
        _, why = score_candidate(hit, profile)
        assert why.matched_keywords == ["RAG"]

    def test_absent_keyword_is_not_matched(self):
        hit = _hit("cs.CL", "A survey of tokenization")
        profile = InterestProfile(categories=[], keywords=["retrieval-augmented generation"])
        _, why = score_candidate(hit, profile)
        assert why.matched_keywords == []

    def test_multiple_keywords_all_match(self):
        hit = _hit("cs.CL", "Retrieval-augmented generation and RAG evaluation")
        profile = InterestProfile(categories=[], keywords=["RAG", "retrieval-augmented generation", "evaluation"])
        _, why = score_candidate(hit, profile)
        assert set(why.matched_keywords) == {"RAG", "retrieval-augmented generation", "evaluation"}


class TestCategoryMatch:
    def test_fetched_category_in_profile_is_matched(self):
        hit = _hit("cs.CL", "Some paper")
        profile = InterestProfile(categories=["cs.CL", "cs.LG"], keywords=[])
        _, why = score_candidate(hit, profile)
        assert why.matched_categories == ["cs.CL"]

    def test_fetched_category_not_in_profile_is_not_matched(self):
        hit = _hit("cs.CV", "Some paper")
        profile = InterestProfile(categories=["cs.CL", "cs.LG"], keywords=[])
        _, why = score_candidate(hit, profile)
        assert why.matched_categories == []


class TestScoreFormula:
    def test_score_is_sum_of_matched_keyword_and_category_counts(self):
        hit = _hit("cs.CL", "Retrieval-augmented generation survey", abstract="RAG and evaluation.")
        profile = InterestProfile(categories=["cs.CL"], keywords=["RAG", "evaluation"])
        score, why = score_candidate(hit, profile)
        assert score == len(why.matched_keywords) + len(why.matched_categories)

    def test_no_match_scores_zero_by_default(self):
        hit = _hit("cs.CV", "Unrelated paper")
        profile = InterestProfile(categories=["cs.CL"], keywords=["RAG"])
        score, why = score_candidate(hit, profile)
        assert score == 0.0
        assert why.matched_keywords == []
        assert why.matched_categories == []

    def test_similarity_is_folded_additively_into_the_score(self):
        hit = _hit("cs.CV", "Unrelated paper")
        profile = InterestProfile(categories=["cs.CL"], keywords=["RAG"])
        score, why = score_candidate(hit, profile, similarity=0.42)
        assert score == 0.42
        assert why.similarity == 0.42

    def test_similarity_combines_with_keyword_and_category_terms(self):
        hit = _hit("cs.CL", "RAG survey")
        profile = InterestProfile(categories=["cs.CL"], keywords=["RAG"])
        score, why = score_candidate(hit, profile, similarity=0.5)
        assert score == 2 + 0.5
        assert why.similarity == 0.5
