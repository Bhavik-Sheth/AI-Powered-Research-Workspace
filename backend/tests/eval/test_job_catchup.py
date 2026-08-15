"""Deterministic regression guard for the job-catchup bug fix
(`jobs/__init__.py:run_catchup_pass`, prompted by a real incident: starting
the sidecar against an idle-but-real vault fired 14 overdue jobs in one
burst, two of which call a real LLM, with no way to prevent it short of not
starting the process at all).

Pure, structural checks only — `run_catchup_pass`/`_provider_is_configured`
themselves need a real DB session (`ScheduledJobs`/`ApiKeys` rows) to
exercise meaningfully, so they're out of scope for this deterministic tier;
what's tested here is the piece that regresses silently and dangerously if
it does: which job kinds are marked as calling the LLM, and that the
per-pass dispatch cap exists and is small."""

import jobs


def test_interest_profile_reextract_is_marked_needs_llm():
    """`interest_profile_reextract_job` (`feed/__init__.py`) calls
    `complete_structured` unconditionally every time it runs — this is the
    job kind Layer 1 must never dispatch without a configured provider."""
    _, _, _, needs_llm = jobs._SCHEDULE_KINDS["interest_profile_reextract"]
    assert needs_llm is True


def test_feed_poll_is_not_marked_needs_llm():
    """`poll_feed_job` uses `score_candidate` (keyword/category match, "no
    LLM anywhere in this path" per its own docstring) plus the embeddings/
    reranker capabilities — never a chat-model call — so it must stay
    dispatchable regardless of whether an LLM provider is configured."""
    _, _, _, needs_llm = jobs._SCHEDULE_KINDS["feed_poll"]
    assert needs_llm is False


def test_catchup_dispatch_cap_is_small_and_positive():
    """Layer 3: a burst of overdue jobs must never all fire in one pass —
    the cap exists and is a small number, not accidentally unbounded."""
    assert 0 < jobs._MAX_CATCHUP_DISPATCHES_PER_PASS <= 5
