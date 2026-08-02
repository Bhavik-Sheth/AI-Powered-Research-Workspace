"""D33: the fuzzy quote locator — normalises whitespace, hyphenation and
ligatures so a quote copied from one text stream (docling's parse, or
PDF.js's text layer) locates in the other, or relocates after a re-parse
changes exact offsets (the quote survives; the offsets don't have to).
"""

from provenance.models import CharSpan

_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬆ": "st", "ﬅ": "ft"}


def _normalize_with_positions(text: str) -> tuple[str, list[int]]:
    """Returns `(normalized_text, positions)` where `positions[i]` is the
    index into the original `text` that `normalized_text[i]` came from."""
    chars: list[str] = []
    positions: list[int] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in _LIGATURES:
            for expanded_char in _LIGATURES[ch]:
                chars.append(expanded_char)
                positions.append(i)
            i += 1
            continue
        if ch == "-" and i + 1 < n and text[i + 1].isspace():
            # A hyphen immediately before whitespace is a line-wrap artifact
            # ("atten-\ntion"), not a real hyphen — drop it and the whitespace.
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            i = j
            continue
        if ch.isspace():
            chars.append(" ")
            positions.append(i)
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            i = j
            continue
        chars.append(ch)
        positions.append(i)
        i += 1
    return "".join(chars), positions


def locate(quote: str, text_stream: str) -> CharSpan | None:
    """The bare fuzzy-match primitive (MODULES.md) — the first normalised
    match, or `None`. Exact matches are found too (normalisation is a
    superset of the exact case)."""
    if not quote:
        return None

    normalized_quote, _ = _normalize_with_positions(quote)
    if not normalized_quote:
        return None

    normalized_text, positions = _normalize_with_positions(text_stream)
    index = normalized_text.find(normalized_quote)
    if index == -1:
        return None

    start = positions[index]
    end = positions[index + len(normalized_quote) - 1] + 1
    return CharSpan(start=start, end=end)


def locate_all(quote: str, text_stream: str) -> list[CharSpan]:
    """Every normalised occurrence, for prefix/suffix disambiguation of a
    quote that appears more than once (Schema.md `quote_anchors`)."""
    if not quote:
        return []
    normalized_quote, _ = _normalize_with_positions(quote)
    if not normalized_quote:
        return []

    normalized_text, positions = _normalize_with_positions(text_stream)
    spans: list[CharSpan] = []
    search_from = 0
    while (index := normalized_text.find(normalized_quote, search_from)) != -1:
        start = positions[index]
        end = positions[index + len(normalized_quote) - 1] + 1
        spans.append(CharSpan(start=start, end=end))
        search_from = index + 1
    return spans
