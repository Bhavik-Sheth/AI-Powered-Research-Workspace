"""`POST /api/projects/:id/memory/query` — hybrid retrieval, reranked cited
rows (TRD §4.2). Route glue only: the fusion/rerank logic lives in Memory
Index (Rules.md: no SQL/LLM call in a route handler).
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from memory import query_memory
from memory.models import CitedRow

router = APIRouter()


class MemoryQueryRequest(BaseModel):
    query: str
    types: list[str] | None = None


class MemoryQueryResponse(BaseModel):
    rows: list[CitedRow]


@router.post("/api/projects/{project_id}/memory/query", response_model=MemoryQueryResponse)
async def query_project_memory(project_id: uuid.UUID, body: MemoryQueryRequest) -> MemoryQueryResponse:
    rows = await query_memory(project_id, body.query, body.types)
    return MemoryQueryResponse(rows=rows)
