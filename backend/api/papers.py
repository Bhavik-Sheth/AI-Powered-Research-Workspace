"""`POST /api/projects/:id/papers`, `GET /api/papers/:paperId`,
`GET /api/papers/:paperId/pdf`, `PATCH /api/projects/:id/papers/:paperId` (TRD §4.2).
"""

import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
import papers
import projects
from papers.models import Paper, PaperCardField, PaperContentView, PaperInput
from settings import get_vault_path

router = APIRouter()


class PaperDetail(BaseModel):
    paper: Paper
    content: PaperContentView | None = None
    card: list[PaperCardField] | None = None


@router.post("/api/projects/{project_id}/papers", response_model=Paper)
async def add_paper(project_id: uuid.UUID, body: PaperInput) -> Paper:
    async with db.session() as session:
        if await projects.get_project(session, project_id) is None:
            raise HTTPException(status_code=404, detail="project not found")
        paper = await papers.add_paper(session, body)
        await projects.add_paper_to_project(session, project_id, paper.id)
        return paper


class LibraryEntry(BaseModel):
    paper: Paper
    relevance: str
    why_relevant: str | None


@router.get("/api/projects/{project_id}/papers", response_model=list[LibraryEntry])
async def list_project_papers(project_id: uuid.UUID) -> list[LibraryEntry]:
    async with db.session() as session:
        if await projects.get_project(session, project_id) is None:
            raise HTTPException(status_code=404, detail="project not found")
        rows = await projects.list_project_papers(session, project_id)
        return [
            LibraryEntry(
                paper=papers.paper_from_row(paper_row),
                relevance=membership.relevance,
                why_relevant=membership.why_relevant,
            )
            for paper_row, membership in rows
        ]


_CONTENT_FIELDS = {"sections", "content", "references", "datasets", "code"}


@router.get("/api/papers/{paper_id}", response_model=PaperDetail)
async def get_paper(paper_id: uuid.UUID, include: str = "") -> PaperDetail:
    """`include` is one parameterised read (TRD §4.3's
    `get_paper(paper_id, include=[card|sections|references|datasets|code])`)
    — `sections`/`references`/`datasets`/`code` all come from the same
    `paper_content` row, so any one of them fetches the whole row."""
    fields = {f.strip() for f in include.split(",") if f.strip()}
    async with db.session() as session:
        paper = await papers.get_paper(session, paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail="paper not found")
        content = await papers.get_paper_content(session, paper_id) if fields & _CONTENT_FIELDS else None
        card = await papers.get_paper_card(session, paper_id) if "card" in fields else None
    return PaperDetail(paper=paper, content=content, card=card)


@router.get("/api/papers/{paper_id}/pdf")
async def get_paper_pdf(paper_id: uuid.UUID) -> FileResponse:
    async with db.session() as session:
        pdf_path = await papers.get_pdf_path(session, paper_id)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail="no PDF available for this paper")
    return FileResponse(get_vault_path() / Path(pdf_path), media_type="application/pdf")


class PatchProjectPaperRequest(BaseModel):
    relevance: Literal["relevant", "somewhat", "not", "unset"] | None = None
    why_relevant: str | None = None


class ProjectPaperResponse(BaseModel):
    project_id: uuid.UUID
    paper_id: uuid.UUID
    relevance: str
    why_relevant: str | None


@router.patch("/api/projects/{project_id}/papers/{paper_id}", response_model=ProjectPaperResponse)
async def patch_project_paper(project_id: uuid.UUID, paper_id: uuid.UUID, body: PatchProjectPaperRequest) -> ProjectPaperResponse:
    async with db.session() as session:
        row = await projects.set_paper_relevance(session, project_id, paper_id, body.relevance, body.why_relevant)
        if row is None:
            raise HTTPException(status_code=404, detail="paper is not in this project's library")
        return ProjectPaperResponse(
            project_id=row.project_id, paper_id=row.paper_id, relevance=row.relevance, why_relevant=row.why_relevant
        )
