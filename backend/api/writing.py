"""`GET/POST /api/projects/:id/documents`, `PUT .../documents/:docId`,
`POST .../documents/:docId/assets`, `POST .../documents/:docId/check_citations`,
`GET .../references`, `GET .../documents/bibtex` — backs Manuscript
(TRD §4.2, US12).
"""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

import db
import vault
import writing
from settings import get_vault_path
from writing.models import AssetInserted, CitationFinding, CompileResult, Document, Reference

router = APIRouter()


class SaveDocumentRequest(BaseModel):
    title: str
    tex: str
    engine: Literal["swiftlatex", "tectonic"] | None = None


class CheckCitationsResponse(BaseModel):
    findings: list[CitationFinding]


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


@router.get("/api/projects/{project_id}/documents/{document_id}/pdf")
async def get_document_pdf(project_id: uuid.UUID, document_id: uuid.UUID) -> FileResponse:
    async with db.session() as session:
        pdf_path = await writing.get_compiled_pdf_path(session, document_id)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail="document has no compiled PDF yet")
    return FileResponse(get_vault_path() / pdf_path, media_type="application/pdf")


@router.post("/api/projects/{project_id}/documents/{document_id}/assets", response_model=AssetInserted)
async def insert_asset(project_id: uuid.UUID, document_id: uuid.UUID, file: UploadFile) -> AssetInserted:
    content = await file.read()
    async with db.session() as session:
        try:
            return await writing.insert_asset(session, project_id, file.filename or "asset", content)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/documents/{document_id}/check_citations", response_model=CheckCitationsResponse)
async def check_citations(project_id: uuid.UUID, document_id: uuid.UUID) -> CheckCitationsResponse:
    async with db.session() as session:
        findings = await writing.check_citations(session, document_id)
        if findings is None:
            raise HTTPException(status_code=404, detail="document not found")
        return CheckCitationsResponse(findings=findings)


@router.get("/api/projects/{project_id}/references", response_model=list[Reference])
async def list_references(project_id: uuid.UUID, prefix: str = "") -> list[Reference]:
    async with db.session() as session:
        return await writing.autocomplete_citations(session, project_id, prefix)


@router.get("/api/projects/{project_id}/bibtex", response_class=PlainTextResponse)
async def export_bibtex(project_id: uuid.UUID) -> str:
    async with db.session() as session:
        return await writing.export_bibtex(session, project_id)
