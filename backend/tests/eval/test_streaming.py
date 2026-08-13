"""HarnessPlan H6 (§3.8) — deterministic tests for `harness.streaming.CitationStream`.

Pure logic: no DB, no LLM, no network — a fake async validator stands in for
Provenance/`query_memory`. `backend/pyproject.toml` declares no async pytest
plugin, so every test drives the coroutines with `asyncio.run` directly,
matching the rest of this suite's dependency-free style (Rules.md: "No
coverage target... no test that requires the compose stack").
"""

import asyncio

from harness.streaming import CitationStream

_LONG_QUOTE = "this quote is long enough to clear the floor"  # 45 chars
_SHORT_QUOTE = "too short"  # 9 chars, below _MIN_UNTAGGED_QUOTE_CHARS (20)


def _always_valid():
    async def validator(quote: str) -> dict | None:
        return {"kind": "anchor", "anchor_id": "fixture-anchor", "quote": quote}

    return validator


def _always_invalid():
    async def validator(quote: str) -> dict | None:
        return None

    return validator


def _never_called():
    async def validator(quote: str) -> dict | None:
        raise AssertionError(f"validator must not be called for {quote!r}")

    return validator


async def _feed(stream: CitationStream, deltas: list[str]) -> list[list[str]]:
    """Feeds each delta in turn, returning the list of piece-lists yielded
    per delta (so a test can assert what did or did not stream at each
    step, not just the final result)."""
    return [await stream.feed(delta) for delta in deltas]


def test_cite_span_split_across_deltas():
    """(a) An explicit `<cite>` span whose opener, content and closer each
    arrive in a different delta still resolves as one validated citation,
    and the concatenation of every streamed piece equals what gets
    persisted."""

    async def run():
        stream = CitationStream(_always_valid())
        per_delta = await _feed(
            stream,
            ["Hello <ci", "te>the quo", f"te</cite> world"],
        )
        return stream, per_delta

    stream, per_delta = asyncio.run(run())

    assert stream.text == "Hello <cite>the quote</cite> world"
    assert stream.citations == [{"kind": "anchor", "anchor_id": "fixture-anchor", "quote": "the quote"}]
    # Nothing before the tag closed carried a bare tag fragment onto the
    # screen — nothing streamed on the middle delta (still mid-tag).
    assert per_delta[1] == []


def test_delta_ending_mid_delimiter_is_held_back():
    """(b) A delta ending exactly on a partial delimiter (`...<ci`) must not
    emit that fragment as prose — it is held until the next delta either
    completes the delimiter or proves it was never one."""

    async def run():
        stream = CitationStream(_always_valid())
        first = await stream.feed("Some notes ...<ci")
        second = await stream.feed(f"te>{_LONG_QUOTE}</cite> done")
        return stream, first, second

    stream, first, second = asyncio.run(run())

    assert first == ["Some notes ..."]  # "<ci" held back, not emitted
    assert not any("<ci" in piece and "<cite>" not in piece for piece in first)
    assert stream.text == f"Some notes ...<cite>{_LONG_QUOTE}</cite> done"
    assert len(stream.citations) == 1


def test_closing_delimiter_split_across_deltas():
    """Regression: `</cite>` itself, not just `<cite>`, can split across a
    delta boundary while IN_TAG (e.g. `...</ci` then `te>...`). A first
    version of `_advance_in_tag` searched only the newly-arrived buffer for
    `</cite>` and absorbed an unmatched partial closer straight into the
    tag body instead of holding it back — so the close tag was never found,
    the state machine stayed stuck in IN_TAG, and everything after (an
    entire unrelated sentence, in the case that caught this) got swallowed
    into one oversized `<unverified>` span at flush. Every split point of
    the seven-character delimiter is exercised here, including one
    character at a time."""

    async def run_split_at(offset: int):
        opener_and_body = "<cite>a valid long enough quote here"
        closer = "</cite>"
        tail = " more prose after."
        stream = CitationStream(_always_valid())
        await stream.feed(opener_and_body + closer[:offset])
        await stream.feed(closer[offset:] + tail)
        flushed = await stream.flush()
        return stream, flushed

    for offset in range(1, len("</cite>")):  # every split point of the 7-char closing delimiter
        stream, flushed = asyncio.run(run_split_at(offset))
        assert flushed is None, f"split at {offset}: nothing should be left pending after the tail delta"
        assert stream.text == "<cite>a valid long enough quote here</cite> more prose after.", f"split at {offset} failed: {stream.text!r}"
        assert stream.citations == [{"kind": "anchor", "anchor_id": "fixture-anchor", "quote": "a valid long enough quote here"}]

    # Worst case: the closer arrives one character at a time.
    async def run_char_by_char():
        stream = CitationStream(_always_valid())
        for ch in "<cite>another valid long quote</cite> and tail text":
            await stream.feed(ch)
        return stream

    stream = asyncio.run(run_char_by_char())
    assert stream.text == "<cite>another valid long quote</cite> and tail text"
    assert len(stream.citations) == 1


def test_untagged_quote_at_or_above_floor_is_validated():
    """(c) A quoted span >= `_MIN_UNTAGGED_QUOTE_CHARS` (20) is treated
    exactly like an explicit `<cite>` span: validated and wrapped."""

    async def run():
        stream = CitationStream(_always_valid())
        text = f'The paper says "{_LONG_QUOTE}" about it.'
        pieces = await stream.feed(text)
        return stream, pieces

    stream, pieces = asyncio.run(run())

    assert stream.text == f'The paper says <cite>{_LONG_QUOTE}</cite> about it.'
    assert stream.citations == [{"kind": "anchor", "anchor_id": "fixture-anchor", "quote": _LONG_QUOTE}]


def test_untagged_quote_below_floor_is_plain_prose():
    """(d) A quoted span shorter than the floor is never treated as a
    citation attempt at all — it passes through untouched, quote marks and
    all, and the validator is never invoked for it."""

    async def run():
        stream = CitationStream(_never_called())
        text = f'They said "{_SHORT_QUOTE}" once.'
        await stream.feed(text)
        return stream

    stream = asyncio.run(run())

    assert stream.text == f'They said "{_SHORT_QUOTE}" once.'
    assert stream.citations == []


def test_unclosed_tag_flushes_as_unverified():
    """(e) An unclosed `<cite>` span at turn end (interrupt, cap, or error)
    is force-closed as `<unverified>` by `flush()` — partial results, never
    well-formed ones."""

    async def run():
        stream = CitationStream(_always_valid())
        await stream.feed("Hello <cite>partial and never closed")
        tail = await stream.flush()
        return stream, tail

    stream, tail = asyncio.run(run())

    assert tail == "<unverified>partial and never closed</unverified>"
    assert stream.text == "Hello <unverified>partial and never closed</unverified>"
    assert stream.citations == []  # never reached the validator


def test_unclosed_quote_flushes_as_unverified_regardless_of_length():
    """(e, variant) An unclosed quoted span flushes as `<unverified>` too,
    even if it never reached the untagged-quote length floor — an
    interrupted span never gets the benefit of "it was probably just
    prose"."""

    async def run():
        stream = CitationStream(_never_called())
        await stream.feed('Then it says "')
        tail = await stream.flush()
        return stream, tail

    stream, tail = asyncio.run(run())

    assert tail == "<unverified></unverified>"
    assert stream.text == "Then it says <unverified></unverified>"


def test_flush_is_a_noop_when_nothing_is_pending():
    async def run():
        stream = CitationStream(_always_valid())
        await stream.feed("Just plain prose, nothing pending.")
        return stream, await stream.flush()

    stream, tail = asyncio.run(run())

    assert tail is None
    assert stream.text == "Just plain prose, nothing pending."


def test_quote_wrapped_cite_tag_is_not_swallowed_as_quoted_text():
    """(f) Bug Fix Plan 3.1's malformed-tag case: a model that wraps its own
    `<cite>` tag in quote marks (`"<cite>...</cite>"`) must have the `<cite>`
    span recognised and validated on its own terms, never swallowed whole as
    "quoted" text — and no `<cite>`/`</cite>` markup may leak through
    unresolved anywhere in the output."""

    async def run():
        stream = CitationStream(_always_valid())
        text = f'before "<cite>{_LONG_QUOTE}</cite>" after'
        await stream.feed(text)
        await stream.flush()  # force-close the still-open trailing `"`
        return stream

    stream = asyncio.run(run())

    assert f"<cite>{_LONG_QUOTE}</cite>" in stream.text
    assert len(stream.citations) == 1
    assert stream.citations[0]["quote"] == _LONG_QUOTE
    # No raw tag markup survives outside the one validated span.
    outside = stream.text.replace(f"<cite>{_LONG_QUOTE}</cite>", "")
    assert "<cite>" not in outside and "</cite>" not in outside


def test_stray_closing_tag_markup_is_stripped_from_prose():
    """A stray `</cite>`/`<unverified>` the model emits in plain prose
    (never opened by a matching `<cite>`) is stripped, never shown raw —
    the `_strip_tag_markup` behaviour ported from Bug Fix Plan 3.1."""

    async def run():
        stream = CitationStream(_never_called())
        await stream.feed("Some text </cite> more <unverified>text</unverified> end")
        return stream

    stream = asyncio.run(run())

    assert "</cite>" not in stream.text
    assert "<unverified>" not in stream.text
    assert "</unverified>" not in stream.text
    assert stream.text == "Some text  more text end"


def test_invalid_cite_span_becomes_unverified():
    """A `<cite>` span that fails validation is relabelled `<unverified>`,
    never trusted from the model's own say-so."""

    async def run():
        stream = CitationStream(_always_invalid())
        await stream.feed(f"See <cite>{_LONG_QUOTE}</cite>.")
        return stream

    stream = asyncio.run(run())

    assert stream.text == f"See <unverified>{_LONG_QUOTE}</unverified>."
    assert stream.citations == []
