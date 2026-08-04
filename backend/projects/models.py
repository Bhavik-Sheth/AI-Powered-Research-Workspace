"""Wire shapes for Project Record's dashboard projection (Rules.md: Pydantic
model names match the wire shape). Backs the Dashboard module's stat row and
`NEEDS ATTENTION` section (Bug Fix Plan Phase 4.3) — every figure here is a
read-only projection over rows Paper Pipeline, Notes, Experiment Record and
Research Feed already own; this module computes nothing new, only counts
and mixes what those modules already return.
"""

import uuid
from typing import Literal

from pydantic import BaseModel


class DashboardStat(BaseModel):
    """One stat-row tile (UI_DESIGN.md §4.1): `total` is the tile's big
    number, `qualifier` is the always-actionable-subset phrase rendered
    under it (`"4 unmarked"`, `"1 in progress"`, `"new since Tue"`) — never
    a bare count on its own."""

    total: int
    qualifier: str


class NeedsAttentionItem(BaseModel):
    """One row of the `NEEDS ATTENTION` stack, mixing two severities on
    purpose (UI_DESIGN.md §4.1): `nudge` renders as the dashed soft-prompt
    block (§3.4), `error` as the danger error card. `paper_id` is present
    only on a `retry`-actionable error, so the frontend can call the
    existing Phase 1.3 reprocess endpoint directly from this row."""

    severity: Literal["nudge", "error"]
    message: str
    paper_id: uuid.UUID | None = None
    action: Literal["retry"] | None = None


class DashboardSummary(BaseModel):
    papers: DashboardStat
    notes: DashboardStat
    experiments: DashboardStat
    feed: DashboardStat
    needs_attention: list[NeedsAttentionItem]
