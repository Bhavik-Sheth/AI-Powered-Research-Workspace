"""D28 deterministic feed ranking suite (Rules.md) — fixture-based, no
network/DB/LLM. `score_candidate` is the synonym-keyword-match and
category-match half of the deterministic rank; the centroid-cosine and
cross-encoder-rerank halves load real embedding/rerank models and are
exercised by tracer fire instead, same reason the other four suites never
load an ML model either.

`TestWindowOverlapDedup` (Bug Fix Plan Phase 5.4) covers the other half of
`poll_feed_job`: `_dedupe` collapses one poll's own fetch (a paper can be
cross-listed in more than one profile category, or returned by more than one
source, in the same window) down to one hit per canonical id, and
`_select_unseen` is what makes a *second* catch-up poll over a window
overlapping the first a no-op for anything the first poll already surfaced —
the seen-set row it wrote is still there. Neither function touches the
network, Postgres or an LLM; both are pure over `RawFeedHit` fixtures and a
plain `set[str]` standing in for `db.filter_unseen`'s anti-join result.
"""

from feed import _dedupe, _select_unseen, score_candidate
from feed.models import InterestProfile, RawFeedHit
from papers.models import SourceIds


def _hit(category: str, title: str, abstract: str | None = None, *, arxiv_id: str = "2401.00001") -> RawFeedHit:
    return RawFeedHit(source_ids=SourceIds(arxiv_id=arxiv_id), category=category, title=title, abstract=abstract)


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


class TestDedupeWithinAPoll:
    """A single poll's own fetch can already contain the same paper twice —
    cross-listed in more than one profile category, or returned by more than
    one source for the same category — before cross-poll dedup even applies."""

    def test_same_canonical_id_from_two_categories_collapses_to_one(self):
        hits = [_hit("cs.CL", "RAG survey", arxiv_id="2401.00001"), _hit("cs.LG", "RAG survey", arxiv_id="2401.00001")]
        deduped = _dedupe(hits)
        assert list(deduped.keys()) == ["arxiv:2401.00001"]

    def test_first_hit_seen_is_kept(self):
        first = _hit("cs.CL", "RAG survey (arXiv listing)", arxiv_id="2401.00001")
        second = _hit("cs.CL", "RAG survey (OpenAlex listing)", arxiv_id="2401.00001")
        deduped = _dedupe([first, second])
        assert deduped["arxiv:2401.00001"].title == "RAG survey (arXiv listing)"

    def test_distinct_canonical_ids_are_all_kept(self):
        hits = [_hit("cs.CL", "Paper A", arxiv_id="2401.00001"), _hit("cs.CL", "Paper B", arxiv_id="2401.00002")]
        deduped = _dedupe(hits)
        assert set(deduped.keys()) == {"arxiv:2401.00001", "arxiv:2401.00002"}

    def test_hit_with_no_resolvable_source_id_is_dropped(self):
        unresolvable = RawFeedHit(source_ids=SourceIds(), category="cs.CL", title="No id")
        resolvable = _hit("cs.CL", "Has id", arxiv_id="2401.00001")
        deduped = _dedupe([unresolvable, resolvable])
        assert list(deduped.keys()) == ["arxiv:2401.00001"]


class TestSelectUnseen:
    """`_select_unseen` is the piece that turns `db.filter_unseen`'s anti-join
    result into the set of candidates a poll actually scores and writes —
    the boundary this suite can reach without a live Postgres seen_set."""

    def test_candidate_not_in_unseen_ids_is_dropped(self):
        candidates = _dedupe([_hit("cs.CL", "RAG survey", arxiv_id="2401.00001")])
        assert _select_unseen(candidates, unseen_ids=set()) == {}

    def test_candidate_in_unseen_ids_is_kept(self):
        candidates = _dedupe([_hit("cs.CL", "RAG survey", arxiv_id="2401.00001")])
        selected = _select_unseen(candidates, unseen_ids={"arxiv:2401.00001"})
        assert set(selected.keys()) == {"arxiv:2401.00001"}

    def test_only_the_unseen_subset_survives_a_mixed_batch(self):
        candidates = _dedupe(
            [
                _hit("cs.CL", "Already surfaced", arxiv_id="2401.00001"),
                _hit("cs.CL", "New this poll", arxiv_id="2401.00002"),
            ]
        )
        selected = _select_unseen(candidates, unseen_ids={"arxiv:2401.00002"})
        assert set(selected.keys()) == {"arxiv:2401.00002"}


class TestWindowOverlapDedup:
    """Reproduces two catch-up polls whose fetch windows overlap (D9's normal
    operating condition across restarts) and proves the item that both windows
    contain is kept exactly once — never double-written to `feed_items`, never
    double-counted by the Dashboard's "new since" stat, which is a direct
    `len()` over `feed_items` rows (`backend/api/projects.py:get_dashboard`).
    """

    def test_item_surfaced_by_poll_one_is_not_resurfaced_by_an_overlapping_poll_two(self):
        # Poll 1's window: papers A and B. Nothing seen yet.
        poll_one_candidates = _dedupe(
            [_hit("cs.CL", "Paper A", arxiv_id="2401.00001"), _hit("cs.CL", "Paper B", arxiv_id="2401.00002")]
        )
        poll_one_selected = _select_unseen(poll_one_candidates, unseen_ids=set(poll_one_candidates.keys()))
        assert set(poll_one_selected.keys()) == {"arxiv:2401.00001", "arxiv:2401.00002"}

        # Every surfaced item writes a seen_set row (reason='surfaced') in the
        # same transaction as its feed_items row — modelled here as the ledger
        # `db.filter_unseen` would consult on the next poll.
        seen_after_poll_one = set(poll_one_selected.keys())

        # Poll 2's window overlaps poll 1's: it refetches paper B (still inside
        # the new `since` bound) plus a genuinely new paper C.
        poll_two_candidates = _dedupe(
            [_hit("cs.CL", "Paper B", arxiv_id="2401.00002"), _hit("cs.CL", "Paper C", arxiv_id="2401.00003")]
        )
        poll_two_unseen_ids = set(poll_two_candidates.keys()) - seen_after_poll_one
        poll_two_selected = _select_unseen(poll_two_candidates, poll_two_unseen_ids)

        # Paper B is not rescored/rewritten by poll 2 — only genuinely new
        # paper C is.
        assert set(poll_two_selected.keys()) == {"arxiv:2401.00003"}

        # Across both polls, every canonical id that would ever reach
        # `feed_items` appears exactly once — the Dashboard's `len(feed_items)`
        # count reflects distinct papers, not (poll x overlap) pairs.
        all_written = list(poll_one_selected.keys()) + list(poll_two_selected.keys())
        assert len(all_written) == len(set(all_written)) == 3

    def test_fully_overlapping_second_poll_writes_nothing_new(self):
        poll_one_candidates = _dedupe([_hit("cs.CL", "Paper A", arxiv_id="2401.00001")])
        poll_one_selected = _select_unseen(poll_one_candidates, unseen_ids=set(poll_one_candidates.keys()))
        seen_after_poll_one = set(poll_one_selected.keys())

        # Poll 2's window is a strict subset of poll 1's — the same single
        # paper, nothing else.
        poll_two_candidates = _dedupe([_hit("cs.CL", "Paper A", arxiv_id="2401.00001")])
        poll_two_unseen_ids = set(poll_two_candidates.keys()) - seen_after_poll_one
        poll_two_selected = _select_unseen(poll_two_candidates, poll_two_unseen_ids)

        assert poll_two_selected == {}
