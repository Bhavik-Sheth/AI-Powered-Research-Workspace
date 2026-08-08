"""`POST /api/search`, `GET /api/results/:resultId` (TRD §4.2)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import search
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
    # Phase 6.2 "Search more": `offset` is which page of the (possibly just
    # widened) pool to return — 0 for a fresh search, the count already
    # shown for a reveal-via-POST or a widen. `page > 0` is a widen call —
    # `search_papers` fetches a deeper pool and appends it to `result_id`'s
    # cached one before this slices the response, so `result_id` is
    # required whenever `page > 0`.
    offset: int = 0
    page: int = 0
    result_id: str | None = None


@router.post("/api/search", response_model=ResultSet)
async def post_search(body: SearchRequest) -> ResultSet:
    if body.page > 0 and not body.result_id:
        raise HTTPException(status_code=400, detail="result_id is required to widen a search (page > 0)")
    try:
        result_set = await search.search_papers(body.query, body.filters, page=body.page, result_id=body.result_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    sliced = result_set.results[body.offset : body.offset + body.limit]
    return ResultSet(
        result_id=result_set.result_id,
        query=result_set.query,
        results=sliced,
        sources_failed=result_set.sources_failed,
        has_more=body.offset + len(sliced) < len(result_set.results),
        pool_size=len(result_set.results),
    )


@router.get("/api/results/{result_id}", response_model=ResultSet)
async def get_result(result_id: str, offset: int = 0, limit: int | None = None) -> ResultSet:
    try:
        return await search.refine_results(result_id, offset=offset, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="result set not found or expired") from exc
