"""`POST /api/projects/:id/highlights` — anchors the selection (D24/D33) then
mirrors it to the vault (D4). Route glue only: no SQL, no anchoring logic
here (Rules.md) — both live in Provenance and Vault Writer.
"""

import uuid

from fastapi import APIRouter, HTTPException

import db
import provenance
import vault
from vault.models import Highlight, HighlightInput

router = APIRouter()


@router.post("/api/projects/{project_id}/highlights", response_model=Highlight)
async def create_highlight(project_id: uuid.UUID, body: HighlightInput) -> Highlight:
    async with db.session() as session:
        anchor = await provenance.validate_and_anchor(
            session, body.paper_id, body.anchor.quote, body.anchor.prefix, body.anchor.suffix
        )
        if anchor is None:
            raise HTTPException(status_code=422, detail="quote does not resolve in the paper's parsed text")
        try:
            return await vault.write_highlight(
                session,
                project_id=project_id,
                paper_id=body.paper_id,
                anchor_id=anchor.id,
                quote=anchor.quote,
                prefix=anchor.prefix,
                suffix=anchor.suffix,
                section_heading=anchor.section_heading,
                char_start=anchor.char_start,
                char_end=anchor.char_end,
                comment=body.comment,
                color=body.color,
                note_id=body.note_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except vault.VaultWriteFailed as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
