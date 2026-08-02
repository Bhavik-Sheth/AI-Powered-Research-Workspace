"""`POST /api/search`, `GET /api/results/:resultId` (TRD §4.2)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
import search
from db.models import ResultStore
from search.models import ResultSet, SearchFilters

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    filters: SearchFilters | None = None


@router.post("/api/search", response_model=ResultSet)
async def post_search(body: SearchRequest) -> ResultSet:
    return await search.search_papers(body.query, body.filters)


@router.get("/api/results/{result_id}", response_model=ResultSet)
async def get_result(result_id: str) -> ResultSet:
    async with db.session() as session:
        row = await session.get(ResultStore, result_id)
    if row is None:
        raise HTTPException(status_code=404, detail="result set not found or expired")
    return ResultSet.model_validate(row.ui_view)
