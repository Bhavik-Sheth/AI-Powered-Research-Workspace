"""Section-aware chunking with a token-budget sub-split and small overlap
(D25). Character-based budget (~4 chars/token is a standard approximation)
rather than a real tokenizer — the embedding model tolerates the slack,
and this keeps the module dependency-free for something this local.
"""

_CHUNK_CHAR_BUDGET = 1600
_CHUNK_OVERLAP = 200


def split_span(text: str, budget: int = _CHUNK_CHAR_BUDGET, overlap: int = _CHUNK_OVERLAP) -> list[tuple[int, int]]:
    """(start, end) offsets into `text`, breaking on whitespace near the
    budget boundary rather than mid-word. A short text is one whole span."""
    n = len(text)
    if n == 0:
        return []
    if n <= budget:
        return [(0, n)]

    spans: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = min(start + budget, n)
        if end < n:
            space = text.rfind(" ", start, end)
            if space > start:
                end = space
        spans.append((start, end))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return spans
