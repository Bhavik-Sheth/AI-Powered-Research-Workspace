"""`GET /api/projects/:id/graph` — the project-scoped edge union, typed and
provenance-tagged (TRD §4.2, US11).
"""

import uuid

from fastapi import APIRouter

import db
import graph
from graph.models import Graph

router = APIRouter()


@router.get("/api/projects/{project_id}/graph", response_model=Graph)
async def get_project_graph(project_id: uuid.UUID, types: str = "") -> Graph:
    type_list = [t.strip() for t in types.split(",") if t.strip()] or None
    async with db.session() as session:
        return await graph.get_graph(session, project_id, type_list)
