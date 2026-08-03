"""`GET/PUT /api/projects/:id/interest-profile` and `GET/POST /api/projects/:id/feed`
— back Research Feed (TRD §4.2, US13).
"""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
import feed
from feed.models import FeedItem, InterestProfile

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


@router.get("/api/projects/{project_id}/feed", response_model=list[FeedItem])
async def list_feed(project_id: uuid.UUID) -> list[FeedItem]:
    async with db.session() as session:
        return await feed.get_feed(session, project_id)


class FeedActionRequest(BaseModel):
    action: Literal["save", "dismiss"]
    item_id: uuid.UUID


@router.post("/api/projects/{project_id}/feed", response_model=list[FeedItem])
async def act_on_feed_item(project_id: uuid.UUID, body: FeedActionRequest) -> list[FeedItem]:
    async with db.session() as session:
        try:
            if body.action == "save":
                await feed.save_item(session, project_id, body.item_id)
            else:
                await feed.dismiss_item(session, project_id, body.item_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await feed.get_feed(session, project_id)
