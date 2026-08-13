"""HarnessPlan H6 — the cite-aware streaming state machine (`§3.8`).

`loop.py` used to accumulate an entire turn's response, validate every
`<cite>`/quoted span against Provenance only after the model had finished
generating, then re-split the validated text and emit it — so the user
watched a status pill for the whole generation, D24's "nothing unvalidated
reaches the screen" enforced by never showing *anything* until the end.
D24 does not require that; it only forbids showing an unvalidated span.

`CitationStream` is fed raw text deltas as they arrive from the LLM and
yields ready-to-emit pieces as soon as they are known to be safe: plain
prose is emitted immediately, a `<cite>...</cite>` or quoted span is
buffered until its closing delimiter arrives and validated inline before
being emitted as `<cite>` or `<unverified>`. The same instance accumulates
the full validated text and the structured citation list, so whatever was
streamed to the user and whatever gets persisted to `messages.content` /
`messages.citations` are built from one object and can never disagree.

`_strip_tag_markup` and its use around every captured span are ported
unchanged from Bug Fix Plan 3.1: a doubled or quote-wrapped `<cite>` tag
must never leave literal tag markup stranded in the output, where it would
render as visible raw characters instead of `⚠ unverified`.
"""

import re
from collections.abc import Awaitable, Callable

__all__ = ["CitationStream", "Validator"]

# A 20B model routinely writes a fabricated "quote" in plain prose instead
# of wrapping it in `<cite>`, so an untagged quotation-marked span is
# validated the same way as an explicit `<cite>` span (Bug Fix Plan 2.2).
# The length floor keeps this to sentence-like spans, not every quoted term.
_MIN_UNTAGGED_QUOTE_CHARS = 20

_OPEN_TAG = "<cite>"
_CLOSE_TAG = "</cite>"
_QUOTE_CLOSERS = {'"': '"', "“": "”"}  # straight and curly quotes

# Bug Fix Plan 3.1: a doubled or quote-wrapped `<cite>` tag can leave
# literal `<cite>`/`</cite>`/`<unverified>`/`</unverified>` markup stranded
# in text that is not itself a validated span. Neither location is allowed
# to reach `messages.citations` still carrying that markup — the Companion
# Pane splits on the outermost match only and does not recurse into one, so
# a leftover tag prints as visible raw characters instead of being masked
# behind `⚠ unverified`.
_TAG_MARKUP_PATTERN = re.compile(r"</?(?:cite|unverified)>")

# What `_advance_prose` watches for in the raw stream: the two states'
# openers, plus the stray closing/unverified markup that must be stripped
# from prose rather than ever reaching the screen literally.
_PROSE_TRIGGER = re.compile(r"<cite>|</cite>|<unverified>|</unverified>|\"|“")

# Delimiter literals a trailing buffer fragment might still grow into with
# the next delta — `...<ci` must not be emitted as prose on the chance the
# next delta completes it to `<cite>`.
_DELIMITER_LITERALS = ("<cite>", "</cite>", "<unverified>", "</unverified>")


def _strip_tag_markup(text: str) -> str:
    """Removes any literal citation-tag markup from `text` — applied to a
    captured span before it is re-wrapped, and to text that turns out to be
    plain prose, so no shape or depth of malformed tag the model emits can
    leave a real `<cite>`/`<unverified>` tag nested or stranded inside the
    validated result."""
    return _TAG_MARKUP_PATTERN.sub("", text)


def _partial_suffix_overlap(tail: str, literal: str) -> int:
    """Longest suffix of `tail` that is a proper (non-empty, non-whole)
    prefix of `literal` — text that might still grow into `literal` with
    more incoming deltas, and so must not be treated as "definitely not
    `literal`" yet. `0` when `tail` cannot be the start of `literal`."""
    for length in range(min(len(tail), len(literal) - 1), 0, -1):
        if tail[-length:] == literal[:length]:
            return length
    return 0


def _partial_delimiter_len(tail: str) -> int:
    """Longest suffix of `tail` that is a proper prefix of any delimiter
    literal — used in `PROSE`, which watches for all four. `0` when `tail`
    cannot be the start of anything."""
    return max((_partial_suffix_overlap(tail, literal) for literal in _DELIMITER_LITERALS), default=0)


# `quote -> citation dict | None`. `None` means the quote does not resolve
# against any open paper's text or any `query_memory` row this turn
# retrieved; the caller (`loop.py`) owns *what counts as a match* — this
# module only owns the buffering and state transitions around calling it.
Validator = Callable[[str], Awaitable[dict | None]]


class CitationStream:
    """The three-state machine of HarnessPlan §3.8: `PROSE` emits
    immediately, `IN_TAG` (opened by `<cite>`) and `IN_QUOTE` (opened by a
    quote mark, closed span >= `_MIN_UNTAGGED_QUOTE_CHARS`) buffer to their
    closing delimiter, validate via `validator`, and emit `<cite>` or
    `<unverified>`. `feed` is the only way text enters the machine; `flush`
    force-closes whatever is still open at turn end. `text` and `citations`
    are the same validated content that was streamed, accumulated as it
    goes — the single source `loop.py` persists from.
    """

    def __init__(self, validator: Validator) -> None:
        self._validator = validator
        self._state = "prose"
        self._buf = ""  # unconsumed raw input, meaning depends on state
        self._tag_buf = ""  # accumulated IN_TAG content across deltas
        self._quote_char: str | None = None
        self._quote_buf = ""  # accumulated IN_QUOTE content across deltas
        self._text_parts: list[str] = []
        self._citations: list[dict] = []

    @property
    def text(self) -> str:
        """The full validated text emitted so far — exactly the
        concatenation of every piece this instance has yielded."""
        return "".join(self._text_parts)

    @property
    def citations(self) -> list[dict]:
        """The structured citation list `messages.citations` stores, in
        the order their spans were validated."""
        return list(self._citations)

    async def feed(self, delta: str) -> list[str]:
        """Advances the machine with one raw text delta from the LLM
        stream and returns zero or more ready-to-emit pieces, in order.
        The caller turns each into a `TextDeltaEvent` immediately."""
        self._buf += delta
        pieces: list[str] = []
        while True:
            before = (self._state, self._buf, self._tag_buf, self._quote_buf)
            if self._state == "prose":
                piece = await self._advance_prose()
            elif self._state == "in_tag":
                piece = await self._advance_in_tag()
            else:
                piece = await self._advance_in_quote()
            if piece:
                pieces.append(piece)
            after = (self._state, self._buf, self._tag_buf, self._quote_buf)
            if before == after:
                break  # blocked: needs more input to make further progress
        return pieces

    async def flush(self) -> str | None:
        """Force-closes any still-open `IN_TAG`/`IN_QUOTE` span as
        `<unverified>` — called at turn end (normal completion, interrupt,
        iteration cap, or error). Partial results, never well-formed ones
        (TRD §3): an unclosed span never gets the benefit of validation,
        it is always `<unverified>` on flush, regardless of length or how
        much of it resolved. Returns the emitted piece, or `None` if
        nothing was pending."""
        if self._state == "in_tag":
            quote = _strip_tag_markup(self._tag_buf + self._buf)
            self._tag_buf, self._buf, self._state = "", "", "prose"
            piece = f"<unverified>{quote}</unverified>"
        elif self._state == "in_quote":
            content = _strip_tag_markup(self._quote_buf + self._buf)
            self._quote_buf, self._buf, self._state = "", "", "prose"
            piece = f"<unverified>{content}</unverified>"
        elif self._buf:
            # A held-back possible-partial-delimiter tail that never
            # resolved into one — it is just text.
            piece = self._buf
            self._buf = ""
        else:
            return None
        self._text_parts.append(piece)
        return piece

    async def _advance_prose(self) -> str | None:
        buf = self._buf
        if not buf:
            return None
        match = _PROSE_TRIGGER.search(buf)
        if match is None:
            hold = _partial_delimiter_len(buf)
            emit, self._buf = buf[: len(buf) - hold], buf[len(buf) - hold :]
            if not emit:
                return None
            self._text_parts.append(emit)
            return emit

        prefix, literal, rest = buf[: match.start()], match.group(), buf[match.end() :]
        self._buf = rest
        if literal == _OPEN_TAG:
            self._state, self._tag_buf = "in_tag", ""
        elif literal in _QUOTE_CLOSERS:
            self._state, self._quote_char, self._quote_buf = "in_quote", literal, ""
        # else: stray `</cite>`/`<unverified>`/`</unverified>` markup —
        # consumed and dropped, never a valid opener in prose.
        if prefix:
            self._text_parts.append(prefix)
            return prefix
        return None

    async def _advance_in_tag(self) -> str | None:
        idx = self._buf.find(_CLOSE_TAG)
        if idx == -1:
            # `</cite>` can itself split across a delta boundary (e.g.
            # `...</ci` then `te>...`) — searching only `self._buf` per
            # call would otherwise never find it, since the tag's tail sits
            # in `_tag_buf` and its head arrives in the *next* delta. Hold
            # back a trailing partial match of the closer instead of
            # absorbing it into `_tag_buf`, so the next `feed()` sees the
            # complete `</cite>` in one buffer and this branch's `.find`
            # actually succeeds.
            hold = _partial_suffix_overlap(self._buf, _CLOSE_TAG)
            self._tag_buf += self._buf[: len(self._buf) - hold]
            self._buf = self._buf[len(self._buf) - hold :]
            return None
        self._tag_buf += self._buf[:idx]
        self._buf = self._buf[idx + len(_CLOSE_TAG) :]
        quote = _strip_tag_markup(self._tag_buf)
        self._tag_buf, self._state = "", "prose"
        return await self._resolve_span(quote)

    async def _advance_in_quote(self) -> str | None:
        assert self._quote_char is not None
        opener = self._quote_char
        closer = _QUOTE_CLOSERS[opener]
        buf = self._buf
        for i, ch in enumerate(buf):
            if ch == "<":
                # Bug Fix Plan 3.1: a `<cite>` tag the model wrapped in
                # quote marks must not be swallowed as "quoted" text —
                # abandon the quote attempt. The opener and everything
                # buffered since it are finalised as plain prose right
                # here (never re-offered to PROSE as a fresh quote-open
                # candidate — re-queueing the same `"` would just retrigger
                # this same abort forever); `buf` is rewound to start
                # exactly at this `<` so PROSE sees it fresh and can
                # recognise `<cite>` (or strip stray markup) on its own
                # terms.
                literal = _strip_tag_markup(opener + self._quote_buf + buf[:i])
                self._buf = buf[i:]
                self._quote_buf, self._quote_char, self._state = "", None, "prose"
                self._text_parts.append(literal)
                return literal
            if ch == closer:
                content = self._quote_buf + buf[:i]
                self._buf = buf[i + 1 :]
                self._quote_buf, self._quote_char, self._state = "", None, "prose"
                if len(content) >= _MIN_UNTAGGED_QUOTE_CHARS:
                    return await self._resolve_span(_strip_tag_markup(content))
                # Below the length floor: not a citation attempt at all —
                # reproduce the original text verbatim, quote marks
                # included, exactly as it would have stayed untouched text
                # under the old whole-turn regex.
                literal = _strip_tag_markup(f"{opener}{content}{closer}")
                self._text_parts.append(literal)
                return literal
        self._quote_buf += buf
        self._buf = ""
        return None

    async def _resolve_span(self, quote: str) -> str:
        citation = await self._validator(quote)
        if citation is not None:
            self._citations.append(citation)
            piece = f"<cite>{quote}</cite>"
        else:
            piece = f"<unverified>{quote}</unverified>"
        self._text_parts.append(piece)
        return piece
