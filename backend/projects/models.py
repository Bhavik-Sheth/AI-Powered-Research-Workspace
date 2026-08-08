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


class CurrentFocus(BaseModel):
    """"Currently working on" (Phase 6.10) — no new `research_questions` table
    (grill decision): the project's own `focus_seed` plus the hypothesis of
    every `in-progress` experiment, both already-owned fields projected
    here, nothing computed or inferred."""

    focus_seed: str | None
    in_progress_hypotheses: list[str]


class ExperimentProgress(BaseModel):
    """One segmented meter, four bands (Phase 6.10) — exactly the four
    `experiments.status` values, counted, never a fifth `failed` band."""

    planned: int
    remaining: int
    in_progress: int
    done: int


class PendingExperimentItem(BaseModel):
    """A `planned`/`remaining` experiment surfaced on its own (Phase 6.10) —
    distinct from the `in-progress` ones already named under `focus`."""

    id: uuid.UUID
    title: str
    status: str
    hypothesis: str | None = None


class RelevantPaperItem(BaseModel):
    """A library paper ranked against the project's current focus text
    (Phase 6.10) via the existing embedding/rerank machinery — no new
    model, no Feed involvement (this is the project's own library, not
    unseen Feed items)."""

    paper_id: uuid.UUID
    title: str
    score: float


class DashboardSummary(BaseModel):
    papers: DashboardStat
    notes: DashboardStat
    experiments: DashboardStat
    feed: DashboardStat
    focus: CurrentFocus
    progress: ExperimentProgress
    pending_experiments: list[PendingExperimentItem]
    relevant_papers: list[RelevantPaperItem]
    needs_attention: list[NeedsAttentionItem]
