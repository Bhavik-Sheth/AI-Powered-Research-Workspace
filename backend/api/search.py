"""`POST /api/search`, `GET /api/results/:resultId` (TRD §4.2)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
import search
from db.models import ResultStore
from search.models import ResultSet, SearchFilters

router = APIRouter()


_DEFAULT_LIMIT = 5


class SearchRequest(BaseModel):
    query: str
    filters: SearchFilters | None = None
    # Phase 6.1: exactly 5 render by default. `search_papers` itself keeps
    # its signature unchanged (MODULES.md) and returns the full fetched
    # pool, cached in full in `result_store` for Phase 6.2's "Search more" —
    # the cap to `limit` happens here, at the API boundary, not inside the
    # module.
    limit: int = _DEFAULT_LIMIT


@router.post("/api/search", response_model=ResultSet)
async def post_search(body: SearchRequest) -> ResultSet:
    result_set = await search.search_papers(body.query, body.filters)
    return ResultSet(
        result_id=result_set.result_id,
        query=result_set.query,
        results=result_set.results[: body.limit],
        sources_failed=result_set.sources_failed,
    )


@router.get("/api/results/{result_id}", response_model=ResultSet)
async def get_result(result_id: str) -> ResultSet:
    async with db.session() as session:
        row = await session.get(ResultStore, result_id)
    if row is None:
        raise HTTPException(status_code=404, detail="result set not found or expired")
    return ResultSet.model_validate(row.ui_view)
