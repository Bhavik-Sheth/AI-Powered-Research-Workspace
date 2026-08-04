"""`GET/POST /api/projects` — backs Project Record (TRD §4.2)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
import experiments
import feed
import notes
import papers
import projects
from db.models import Project
from papers.models import Paper
from projects.models import DashboardStat, DashboardSummary, NeedsAttentionItem

router = APIRouter()

# The four `papers.Paper` state columns a stage can fail or stall on
# (Schema.md) — read here, not re-derived, same fields Library View's own
# retry affordance already keys off (Bug Fix Plan Phase 1.3).
_PIPELINE_STAGES = ("fetch_state", "parse_state", "embed_state", "extract_state")


def _stalled(paper: Paper) -> bool:
    """A stage that will never progress on its own: a `queued` stage whose
    prerequisite is already `done` — the signature of a job that never ran.
    Mirrors `frontend/src/library/LibraryView.tsx`'s `needsRetry`, minus the
    `failed` check, which Dashboard reports separately as a real error."""
    return (paper.fetch_state == "done" and paper.parse_state == "queued") or (
        paper.parse_state == "done" and (paper.embed_state == "queued" or paper.extract_state == "queued")
    )


class TabRef(BaseModel):
    """One entry in the persisted center-pane tab stack (TRD §4.1's
    `TabRef`) — a routed view instance, e.g. two open papers are two
    entries with the same `kind` and different `params`."""

    id: str
    kind: str
    params: dict[str, str] = {}
    label: str


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    focus_seed: str | None
    last_opened_at: datetime | None
    tab_stack: list[TabRef]
    active_tab: str | None

    @classmethod
    def from_row(cls, row: Project) -> "ProjectResponse":
        return cls(
            id=row.id,
            name=row.name,
            slug=row.slug,
            focus_seed=row.focus_seed,
            last_opened_at=row.last_opened_at,
            tab_stack=[TabRef.model_validate(t) for t in row.tab_stack],
            active_tab=row.active_tab,
        )


class CreateProjectRequest(BaseModel):
    name: str
    focus_seed: str | None = None


class SaveTabStackRequest(BaseModel):
    tab_stack: list[TabRef]
    active_tab: str | None = None


@router.get("/api/projects", response_model=list[ProjectResponse])
async def list_projects() -> list[ProjectResponse]:
    async with db.session() as session:
        rows = await projects.list_projects(session)
        return [ProjectResponse.from_row(row) for row in rows]


@router.post("/api/projects", response_model=ProjectResponse)
async def create_project(body: CreateProjectRequest) -> ProjectResponse:
    async with db.session() as session:
        row = await projects.create_project(session, body.name, body.focus_seed)
        return ProjectResponse.from_row(row)


@router.get("/api/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: uuid.UUID) -> ProjectResponse:
    async with db.session() as session:
        row = await projects.get_project(session, project_id)
        if row is None:
            raise HTTPException(status_code=404, detail="project not found")
        return ProjectResponse.from_row(row)


@router.get("/api/projects/{project_id}/dashboard", response_model=DashboardSummary)
async def get_dashboard(project_id: uuid.UUID) -> DashboardSummary:
    """The Dashboard's stat row and `NEEDS ATTENTION` section (UI_DESIGN.md
    §4.1, Bug Fix Plan Phase 4.3) — a read-only projection over rows Paper
    Pipeline, Notes, Experiment Record and Research Feed already own. No new
    table, no new state: this route only counts and mixes existing reads."""
    async with db.session() as session:
        if await projects.get_project(session, project_id) is None:
            raise HTTPException(status_code=404, detail="project not found")

        paper_rows = await projects.list_project_papers(session, project_id)
        note_list = await notes.list_notes(session, project_id)
        unlinked_note_ids = await notes.list_unlinked_note_ids(session, project_id)
        experiment_list = await experiments.list_experiments(session, project_id)
        feed_items = await feed.get_feed(session, project_id)

    paper_views = [papers.paper_from_row(row) for row, _membership in paper_rows]
    unmarked = sum(1 for _row, membership in paper_rows if membership.relevance == "unset")
    in_progress = sum(1 for experiment in experiment_list if experiment.status == "in-progress")
    remaining = sum(1 for experiment in experiment_list if experiment.status == "remaining")

    needs_attention: list[NeedsAttentionItem] = []
    for paper in paper_views:
        if any(getattr(paper, stage) == "failed" for stage in _PIPELINE_STAGES):
            needs_attention.append(
                NeedsAttentionItem(
                    severity="error",
                    message=f'"{paper.title}" failed to process.',
                    paper_id=paper.id,
                    action="retry",
                )
            )
        elif _stalled(paper):
            needs_attention.append(
                NeedsAttentionItem(
                    severity="nudge",
                    message=f'"{paper.title}" is stalled mid-pipeline — no job is in flight for it.',
                    paper_id=paper.id,
                    action="retry",
                )
            )
    if unmarked:
        plural = "s" if unmarked != 1 else ""
        verb = "are" if unmarked != 1 else "is"
        needs_attention.append(
            NeedsAttentionItem(severity="nudge", message=f"{unmarked} paper{plural} {verb} still unmarked for relevance")
        )
    if remaining:
        plural = "s" if remaining != 1 else ""
        verb = "sit" if remaining != 1 else "sits"
        needs_attention.append(
            NeedsAttentionItem(
                severity="nudge", message=f"{remaining} experiment{plural} {verb} in remaining with no next step"
            )
        )

    feed_qualifier = f"new since {max(item.polled_at for item in feed_items):%a}" if feed_items else "nothing new"

    return DashboardSummary(
        papers=DashboardStat(total=len(paper_views), qualifier=f"{unmarked} unmarked"),
        notes=DashboardStat(total=len(note_list), qualifier=f"{len(unlinked_note_ids)} unlinked"),
        experiments=DashboardStat(total=len(experiment_list), qualifier=f"{in_progress} in progress"),
        feed=DashboardStat(total=len(feed_items), qualifier=feed_qualifier),
        needs_attention=needs_attention,
    )


@router.put("/api/projects/{project_id}/tab-stack", response_model=ProjectResponse)
async def save_tab_stack(project_id: uuid.UUID, body: SaveTabStackRequest) -> ProjectResponse:
    async with db.session() as session:
        row = await projects.save_tab_stack(
            session, project_id, [t.model_dump() for t in body.tab_stack], body.active_tab
        )
        if row is None:
            raise HTTPException(status_code=404, detail="project not found")
        return ProjectResponse.from_row(row)
