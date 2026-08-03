"""Wire/value shapes for Research Feed (Rules.md: names match the wire shape)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from papers.models import SourceIds

FeedItemState = Literal["new", "saved", "dismissed"]


class RawFeedHit(BaseModel):
    """One source's un-deduped, un-scored candidate for one category, before
    it becomes a `FeedItem`. A local shape, not `search.RawHit` — Research
    Feed does not depend on Search Federation (MODULES.md)."""

    source_ids: SourceIds
    category: str
    title: str
    abstract: str | None = None
    venue: str | None = None
    source_url: str | None = None
    pdf_url: str | None = None
    published_at: datetime | None = None


class InterestProfile(BaseModel):
    """Inspectable and user-editable (D28) — the fetch's category window and
    the ranking's keyword-match term are both read from exactly this."""

    categories: list[str] = []
    keywords: list[str] = []


class WhyRelevant(BaseModel):
    """Why one item surfaced. Required on every `FeedItem` — an item with
    none of these non-empty never reaches `feed_items` (Rules.md)."""

    matched_keywords: list[str] = []
    matched_categories: list[str] = []
    similarity: float = 0.0


class FeedItem(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    canonical_id: str
    title: str
    metadata: dict
    score: float
    why_relevant: WhyRelevant
    state: FeedItemState
    polled_at: datetime
