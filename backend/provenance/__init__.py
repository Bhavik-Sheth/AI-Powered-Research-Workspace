"""Provenance — confirms a claimed quote resolves in a paper's parsed text
and produces the durable anchor object every consumer cites (MODULES.md).
"""

import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PaperContent, QuoteAnchors
from provenance.locator import CharSpan, locate, locate_all
from provenance.models import QuoteAnchor

__all__ = ["validate_and_anchor", "locate", "get_anchor"]

_CONTEXT_WINDOW = 40


def _context_score(full_text: str, span: CharSpan, prefix: str, suffix: str) -> float:
    actual_prefix = full_text[max(0, span.start - _CONTEXT_WINDOW) : span.start]
    actual_suffix = full_text[span.end : span.end + _CONTEXT_WINDOW]
    prefix_score = SequenceMatcher(None, actual_prefix, prefix[-_CONTEXT_WINDOW:]).ratio()
    suffix_score = SequenceMatcher(None, actual_suffix, suffix[:_CONTEXT_WINDOW]).ratio()
    return prefix_score + suffix_score


def _section_for_offset(sections: list[dict], offset: int) -> str | None:
    for section in sections:
        if section.get("char_start", -1) <= offset < section.get("char_end", -1):
            return section.get("heading")
    return None


async def validate_and_anchor(
    session: AsyncSession, paper_id: uuid.UUID, quote: str, prefix: str, suffix: str
) -> QuoteAnchor | None:
    """D24's substring check plus D33's fuzzy locator in one call (MODULES.md).

    `None` means the quote does not resolve — every caller is contractually
    required to drop the field to "not stated" or strip the citation and
    mark it unverified (Rules.md); this function never raises for that case.
    """
    content = await session.get(PaperContent, paper_id)
    if content is None:
        return None

    candidates = locate_all(quote, content.full_text)
    if not candidates:
        return None

    span = max(candidates, key=lambda candidate: _context_score(content.full_text, candidate, prefix, suffix))
    section_heading = _section_for_offset(content.sections, span.start)
    validated_at = datetime.now(timezone.utc)

    row = QuoteAnchors(
        id=uuid.uuid4(),
        paper_id=paper_id,
        quote=quote,
        prefix=prefix,
        suffix=suffix,
        char_start=span.start,
        char_end=span.end,
        section_heading=section_heading,
        validated_at=validated_at,
    )
    session.add(row)
    await session.flush()

    return QuoteAnchor(
        id=row.id,
        paper_id=paper_id,
        quote=quote,
        prefix=prefix,
        suffix=suffix,
        char_start=span.start,
        char_end=span.end,
        section_heading=section_heading,
        validated_at=validated_at,
    )


async def get_anchor(session: AsyncSession, anchor_id: uuid.UUID) -> QuoteAnchor | None:
    """Reads back an anchor `validate_and_anchor` already produced — the
    Companion citation click path (Phase 6.1) resolves an `anchor_id` to the
    paper and offsets `scroll_to`/`highlight_span` need this way, the same
    object a card field or a highlight already cites."""
    row = await session.get(QuoteAnchors, anchor_id)
    if row is None:
        return None
    return QuoteAnchor(
        id=row.id,
        paper_id=row.paper_id,
        quote=row.quote,
        prefix=row.prefix,
        suffix=row.suffix,
        char_start=row.char_start,
        char_end=row.char_end,
        section_heading=row.section_heading,
        validated_at=row.validated_at,
    )
