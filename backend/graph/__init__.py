"""Knowledge Graph — the project-scoped union of metadata + LLM-derived
edges (MODULES.md). Phase 1.4 ships the write side, called from Paper
Pipeline's enrichment and extraction passes; `get_graph` is surfaced in
Phase 3, once the Graph View exists to render it.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

import db
from db.models import Paper, PaperEdges, ProjectPapers
from graph.models import Graph, GraphEdge, LLMEdge, MetadataEdge

_CONFLICT_KEY = ["src_type", "src_id", "dst_type", "dst_id", "relation", "provenance"]

# How far the traversal walks out from the project's own papers through the
# global `paper_edges` graph (e.g. "papers this paper cites") — a project's
# own `idea_edges` are unioned in flat, unaffected by this bound.
_DEFAULT_TRAVERSAL_DEPTH = 2


async def _upsert(session: AsyncSession, values: dict) -> None:
    # Idempotent re-extraction (Schema.md): the same edge from a re-run is a
    # no-op, not a duplicate row.
    stmt = insert(PaperEdges).values(**values).on_conflict_do_nothing(index_elements=_CONFLICT_KEY)
    await session.execute(stmt)


async def write_metadata_edges(session: AsyncSession, paper_id: uuid.UUID, edges: list[MetadataEdge]) -> None:
    """Writes exact edges from a literature API — solid in the graph view."""
    for edge in edges:
        await _upsert(
            session,
            {
                "src_type": edge.src_type,
                "src_id": edge.src_id,
                "dst_type": edge.dst_type,
                "dst_id": edge.dst_id,
                "relation": edge.relation,
                "provenance": "metadata",
                "source_api": edge.source_api,
                "confidence": None,
            },
        )


async def write_llm_edges(session: AsyncSession, paper_id: uuid.UUID, edges: list[LLMEdge]) -> None:
    """Writes LLM-derived edges for a paper the user actually opened (D26) — dashed in the graph view."""
    for edge in edges:
        await _upsert(
            session,
            {
                "src_type": edge.src_type,
                "src_id": edge.src_id,
                "dst_type": edge.dst_type,
                "dst_id": edge.dst_id,
                "relation": edge.relation,
                "provenance": "llm",
                "source_api": None,
                "confidence": edge.confidence,
            },
        )


async def get_graph(
    session: AsyncSession, project_id: uuid.UUID, types: list[str] | None = None, depth: int = _DEFAULT_TRAVERSAL_DEPTH
) -> Graph:
    """The project-scoped union (D26): every `idea_edges` row for this
    project, plus `paper_edges` reachable within `depth` hops of the
    project's own papers. A project with no matching edges returns an empty
    `Graph`, not an error."""
    canonical_ids = await session.scalars(
        select(Paper.canonical_id).join(ProjectPapers, ProjectPapers.paper_id == Paper.id).where(ProjectPapers.project_id == project_id)
    )
    roots = [("paper", canonical_id) for canonical_id in canonical_ids]
    rows = await db.traverse_graph(session, project_id, roots, depth, types)
    edges = [GraphEdge(**row) for row in rows]

    paper_node_ids = {edge.src_id for edge in edges if edge.src_type == "paper"} | {
        edge.dst_id for edge in edges if edge.dst_type == "paper"
    }
    paper_ids: dict[str, uuid.UUID] = {}
    paper_titles: dict[str, str] = {}
    if paper_node_ids:
        rows = await session.execute(
            select(Paper.canonical_id, Paper.id, Paper.title).where(Paper.canonical_id.in_(paper_node_ids))
        )
        for canonical_id, paper_id, title in rows:
            paper_ids[canonical_id] = paper_id
            paper_titles[canonical_id] = title

    return Graph(edges=edges, paper_ids=paper_ids, paper_titles=paper_titles)
