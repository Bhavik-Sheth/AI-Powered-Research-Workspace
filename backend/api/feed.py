"""`GET/PUT /api/projects/:id/interest-profile` — backs Research Feed's
inspectable, user-editable profile (TRD §4.2, US13).
"""

import uuid

from fastapi import APIRouter, HTTPException

import db
import feed
from feed.models import InterestProfile

router = APIRouter()


@router.get("/api/projects/{project_id}/interest-profile", response_model=InterestProfile)
async def get_interest_profile(project_id: uuid.UUID) -> InterestProfile:
    async with db.session() as session:
        try:
            return await feed.get_interest_profile(session, project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/api/projects/{project_id}/interest-profile", response_model=InterestProfile)
async def put_interest_profile(project_id: uuid.UUID, body: InterestProfile) -> InterestProfile:
    async with db.session() as session:
        try:
            return await feed.update_interest_profile(session, project_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
