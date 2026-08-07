"""`GET /api/anchors/:id` — resolves a quote anchor to the paper and offsets
it points at (TRD §4.2, extended Phase 6.1). Route glue only: the anchor
object itself is Provenance's (D33).
"""

import uuid

from fastapi import APIRouter, HTTPException

import db
import provenance
from provenance.models import QuoteAnchor

router = APIRouter()


@router.get("/api/anchors/{anchor_id}", response_model=QuoteAnchor)
async def get_anchor(anchor_id: uuid.UUID) -> QuoteAnchor:
    async with db.session() as session:
        anchor = await provenance.get_anchor(session, anchor_id)
        if anchor is None:
            raise HTTPException(status_code=404, detail="anchor not found")
        return anchor
