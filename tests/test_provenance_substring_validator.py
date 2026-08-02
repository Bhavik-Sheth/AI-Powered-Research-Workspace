"""D24 provenance substring validator suite (Rules.md) — fixture-based, no
network/DB/LLM. A claimed quote resolves at the claimed offsets, or it does
not; this is the deterministic, non-LLM gate before any `quote_anchors` or
`paper_cards` row is written.
"""

from provenance.validator import validate_substring

TEXT = "Attention sinks are tokens that receive disproportionate attention mass."


class TestExactMatch:
    def test_quote_matches_at_claimed_offsets(self):
        start, end = TEXT.index("Attention sinks"), TEXT.index("Attention sinks") + len("Attention sinks")
        assert validate_substring(TEXT, "Attention sinks", start, end) is True

    def test_whole_text_matches(self):
        assert validate_substring(TEXT, TEXT, 0, len(TEXT)) is True


class TestMismatch:
    def test_wrong_offsets_for_a_real_span_elsewhere_in_the_text(self):
        # "tokens" is real text, but not at these offsets.
        assert validate_substring(TEXT, "tokens", 0, 6) is False

    def test_quote_not_present_at_all(self):
        assert validate_substring(TEXT, "hallucinated claim", 0, 19) is False

    def test_off_by_one_start(self):
        real_start = TEXT.index("sinks")
        real_end = real_start + len("sinks")
        assert validate_substring(TEXT, "sinks", real_start + 1, real_end) is False

    def test_off_by_one_end(self):
        real_start = TEXT.index("sinks")
        real_end = real_start + len("sinks")
        assert validate_substring(TEXT, "sinks", real_start, real_end - 1) is False


class TestBoundaries:
    def test_negative_start_is_rejected(self):
        assert validate_substring(TEXT, "Attention", -1, 9) is False

    def test_end_past_text_length_is_rejected(self):
        assert validate_substring(TEXT, "mass.", len(TEXT) - 4, len(TEXT) + 10) is False

    def test_start_equal_to_end_is_rejected(self):
        assert validate_substring(TEXT, "", 5, 5) is False

    def test_start_after_end_is_rejected(self):
        assert validate_substring(TEXT, "x", 10, 5) is False

    def test_full_text_boundaries_are_inclusive_exclusive(self):
        assert validate_substring(TEXT, TEXT[:10], 0, 10) is True
        assert validate_substring(TEXT, TEXT[-10:], len(TEXT) - 10, len(TEXT)) is True
