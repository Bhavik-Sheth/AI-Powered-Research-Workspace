"""Wire-shape models for Manuscript (Rules.md: names match the wire shape).

`Document`/`DocumentInput` are `vault.models`' types — Vault Writer already
owns that shape (MODULES.md dependency order: Manuscript depends on Vault
Writer, not the reverse) — re-exported here so callers only ever import one
name for the concept.
"""

import uuid
from typing import Literal

from pydantic import BaseModel

from vault.models import Document, DocumentInput

__all__ = ["Document", "DocumentInput", "CitationFinding", "Reference", "CompileResult", "AssetInserted"]


class CitationFinding(BaseModel):
    """One entry of `documents.citation_findings` (Schema.md) — a missing
    `\\cite` target or a claim `check_citations` could not link to a source."""

    kind: Literal["missing", "unsupported"]
    span: str
    note: str | None = None


class Reference(BaseModel):
    """One of the project's own papers, shaped for `\\cite` autocomplete and
    BibTeX export — `cite_key` is the same deterministic value both surfaces
    use, so a key inserted while autocompleting always resolves in the
    exported `.bib`."""

    paper_id: uuid.UUID
    cite_key: str
    title: str
    authors: list[str] = []
    year: int | None = None


class CompileResult(BaseModel):
    """What a Tectonic compile returns — success or failure, never a 500
    (MODULES.md's Errors clause). On failure the previous `.tex` on disk is
    untouched, so `document` is the unchanged prior row."""

    success: bool
    engine: Literal["tectonic"]
    log: str
    document: Document


class AssetInserted(BaseModel):
    """What inserting an image, a rendered Mermaid diagram, or a workspace
    dataviz export into a manuscript returns — the vault-relative path the
    editor inlines into an `\\includegraphics{...}` snippet."""

    path: str
