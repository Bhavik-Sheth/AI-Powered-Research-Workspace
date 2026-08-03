"""Manuscript — owns LaTeX document records and checks their citations
against the project's references (MODULES.md, D34). The `.tex` file under
`projects/<slug>/manuscript/` is truth; Vault Writer is the only module that
touches it. This package's own job is everything Vault Writer must not
know: running the Tectonic escape hatch, and the missing/unsupported
citation rule.
"""

import uuid
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import vault
from db.models import Documents, Paper
from vault.models import Document, DocumentInput
from writing.citation_check import check_citations as _run_citation_check
from writing.models import AssetInserted, CitationFinding, CompileResult, Reference
from writing.references import autocomplete_citations as _autocomplete_citations
from writing.references import format_bibtex, list_references
from writing.tectonic import compile_tex

__all__ = [
    "Document",
    "DocumentInput",
    "CompileResult",
    "AssetInserted",
    "CitationFinding",
    "Reference",
    "save_document",
    "list_documents",
    "get_document",
    "insert_asset",
    "check_citations",
    "autocomplete_citations",
    "export_bibtex",
]


def _document_from_row(row: Documents) -> Document:
    return Document.from_row(row)


async def list_documents(session: AsyncSession, project_id: uuid.UUID) -> list[Document]:
    rows = await session.scalars(select(Documents).where(Documents.project_id == project_id))
    return [_document_from_row(row) for row in rows]


async def get_document(session: AsyncSession, document_id: uuid.UUID) -> Document | None:
    row = await session.get(Documents, document_id)
    return _document_from_row(row) if row is not None else None


async def save_document(
    session: AsyncSession,
    project_id: uuid.UUID,
    tex: str,
    title: str,
    document_id: uuid.UUID | None = None,
    engine: Literal["swiftlatex", "tectonic"] | None = None,
) -> Document | CompileResult:
    """Persists `tex`; `engine="tectonic"` also runs the Docker escape hatch
    first (MODULES.md). A failing Tectonic compile is not an error (Rules.md)
    — it returns a `CompileResult(success=False, ...)` carrying the engine's
    log as the compile-error panel's content, and the `.tex` already on disk
    is left exactly as it was: the write only happens after a successful
    compile, so a broken edit never clobbers the last one that built.
    `engine=None`/`"swiftlatex"` never touches Docker — that preview runs
    entirely in the renderer's own WASM worker.
    """
    if engine == "tectonic":
        manuscript_dir = await vault.manuscript_dir(session, project_id)
        success, log, pdf_bytes = await compile_tex(tex, manuscript_dir)
        if not success:
            existing = await get_document(session, document_id) if document_id is not None else None
            if existing is None:
                existing = Document(
                    id=document_id or uuid.uuid4(),
                    project_id=project_id,
                    title=title,
                    file_path="",
                    body=tex,
                    citation_findings=[],
                    last_compiled_at=None,
                    last_compile_engine=None,
                )
            return CompileResult(success=False, engine="tectonic", log=log, document=existing)

        doc = await vault.write_document(session, project_id, DocumentInput(id=document_id, title=title, tex=tex), compiled_pdf=pdf_bytes)
        row = await session.get(Documents, doc.id)
        row.last_compiled_at = datetime.now(timezone.utc)
        row.last_compile_engine = "tectonic"
        await session.flush()
        return CompileResult(success=True, engine="tectonic", log=log, document=_document_from_row(row))

    return await vault.write_document(session, project_id, DocumentInput(id=document_id, title=title, tex=tex))


async def insert_asset(session: AsyncSession, project_id: uuid.UUID, filename: str, content: bytes) -> AssetInserted:
    """Backs the editor's insert paths — image upload, a rendered Mermaid
    diagram, or a workspace dataviz PNG/SVG export (D34) — by writing the
    bytes into the manuscript's asset folder through Vault Writer and
    handing back the `\\includegraphics`-ready relative path."""
    path = await vault.write_manuscript_asset(session, project_id, filename, content)
    return AssetInserted(path=path)


async def autocomplete_citations(session: AsyncSession, project_id: uuid.UUID, prefix: str) -> list[Reference]:
    return await _autocomplete_citations(session, project_id, prefix)


async def export_bibtex(session: AsyncSession, project_id: uuid.UUID) -> str:
    references = await list_references(session, project_id)
    papers_by_id = {paper.id: paper for paper in await session.scalars(select(Paper).where(Paper.id.in_([r.paper_id for r in references])))}
    return format_bibtex(references, papers_by_id)


async def check_citations(session: AsyncSession, document_id: uuid.UUID) -> list[CitationFinding] | None:
    """Runs the missing/unsupported checks over the document's current
    source and persists the result to `documents.citation_findings`
    (Schema.md: "from the last check run") — `None` only when the document
    itself doesn't exist."""
    row = await session.get(Documents, document_id)
    if row is None:
        return None
    references = await list_references(session, row.project_id)
    findings = await _run_citation_check(row.body, {ref.cite_key for ref in references})
    row.citation_findings = [f.model_dump() for f in findings]
    await session.flush()
    return findings
