"""D24: the deterministic, non-LLM substring validator.

Confirms a claimed quote resolves at claimed offsets, or it does not — no
fuzzy matching here (that is `locator.py`); this is the fast, exact check
run first, and the last gate before a `quote_anchors` row can exist.
"""


def validate_substring(text: str, quote: str, char_start: int, char_end: int) -> bool:
    """True iff `text[char_start:char_end]` is exactly `quote`."""
    if char_start < 0 or char_end > len(text) or char_start >= char_end:
        return False
    return text[char_start:char_end] == quote
