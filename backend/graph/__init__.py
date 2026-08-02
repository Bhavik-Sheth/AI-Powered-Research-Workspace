"""Knowledge Graph — the project-scoped union of metadata + LLM-derived
edges (MODULES.md). Phase 1.4 ships the write side, called from Paper
Pipeline's enrichment and extraction passes; `get_graph` is surfaced in
Phase 3, once the Graph View exists to render it.
"""

import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PaperEdges
from graph.models import LLMEdge, MetadataEdge

_CONFLICT_KEY = ["src_type", "src_id", "dst_type", "dst_id", "relation", "provenance"]


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
