"""`GET/POST/PATCH /api/projects/:id/notes` — backs Vault Writer + Notes read path (TRD §4.2, US5)."""

import uuid

from fastapi import APIRouter, HTTPException

import db
import notes
import vault
from vault.models import Note, NoteInput

router = APIRouter()


@router.get("/api/projects/{project_id}/notes", response_model=list[Note])
async def list_notes(project_id: uuid.UUID) -> list[Note]:
    async with db.session() as session:
        return await notes.list_notes(session, project_id)


@router.post("/api/projects/{project_id}/notes", response_model=Note)
async def create_note(project_id: uuid.UUID, body: NoteInput) -> Note:
    if body.frontmatter_id is not None:
        raise HTTPException(status_code=422, detail="a new note must not carry frontmatter_id; use PATCH to update")
    async with db.session() as session:
        try:
            return await vault.write_note(session, project_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except vault.VaultWriteFailed as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/api/projects/{project_id}/notes", response_model=Note)
async def update_note(project_id: uuid.UUID, body: NoteInput) -> Note:
    if body.frontmatter_id is None:
        raise HTTPException(status_code=422, detail="frontmatter_id is required to update a note")
    async with db.session() as session:
        try:
            return await vault.write_note(session, project_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except vault.VaultWriteFailed as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
