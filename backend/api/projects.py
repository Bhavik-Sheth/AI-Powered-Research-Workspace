"""`GET/POST /api/projects` — backs Project Record (TRD §4.2)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
import projects
from db.models import Project

router = APIRouter()


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


@router.put("/api/projects/{project_id}/tab-stack", response_model=ProjectResponse)
async def save_tab_stack(project_id: uuid.UUID, body: SaveTabStackRequest) -> ProjectResponse:
    async with db.session() as session:
        row = await projects.save_tab_stack(
            session, project_id, [t.model_dump() for t in body.tab_stack], body.active_tab
        )
        if row is None:
            raise HTTPException(status_code=404, detail="project not found")
        return ProjectResponse.from_row(row)
