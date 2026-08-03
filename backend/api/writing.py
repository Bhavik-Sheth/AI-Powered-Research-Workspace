"""`GET/POST /api/projects/:id/documents`, `PUT .../documents/:docId`,
`POST .../documents/:docId/assets` — backs Manuscript (TRD §4.2, US12).
"""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

import db
import vault
import writing
from writing.models import AssetInserted, CompileResult, Document

router = APIRouter()


class SaveDocumentRequest(BaseModel):
    title: str
    tex: str
    engine: Literal["swiftlatex", "tectonic"] | None = None


@router.get("/api/projects/{project_id}/documents", response_model=list[Document])
async def list_documents(project_id: uuid.UUID) -> list[Document]:
    async with db.session() as session:
        return await writing.list_documents(session, project_id)


@router.post("/api/projects/{project_id}/documents", response_model=Document | CompileResult)
async def create_document(project_id: uuid.UUID, body: SaveDocumentRequest) -> Document | CompileResult:
    async with db.session() as session:
        try:
            return await writing.save_document(session, project_id, body.tex, body.title, engine=body.engine)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except vault.VaultWriteFailed as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/projects/{project_id}/documents/{document_id}", response_model=Document)
async def get_document(project_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    async with db.session() as session:
        document = await writing.get_document(session, document_id)
        if document is None or document.project_id != project_id:
            raise HTTPException(status_code=404, detail="document not found")
        return document


@router.put("/api/projects/{project_id}/documents/{document_id}", response_model=Document | CompileResult)
async def update_document(project_id: uuid.UUID, document_id: uuid.UUID, body: SaveDocumentRequest) -> Document | CompileResult:
    async with db.session() as session:
        try:
            return await writing.save_document(session, project_id, body.tex, body.title, document_id=document_id, engine=body.engine)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except vault.VaultWriteFailed as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/documents/{document_id}/assets", response_model=AssetInserted)
async def insert_asset(project_id: uuid.UUID, document_id: uuid.UUID, file: UploadFile) -> AssetInserted:
    content = await file.read()
    async with db.session() as session:
        try:
            return await writing.insert_asset(session, project_id, file.filename or "asset", content)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
