"""Database Layer — pooled connection + the two hand-written SQL primitives (MODULES.md).

Phase 1.1 shipped `session` and `run_migrations`; `traverse_graph` lands
with Knowledge Graph (Phase 3), the module that needs it.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import get_config

_engine = create_async_engine(get_config().database_url)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """One unit of work; commits on clean exit, rolls back on exception."""
    async with _session_factory() as db_session:
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise


def _upgrade_to_head() -> None:
    alembic_cfg = AlembicConfig(str(Path(__file__).parent.parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", get_config().sync_database_url)
    command.upgrade(alembic_cfg, "head")


async def run_migrations() -> None:
    """Applies pending Alembic revisions once, at startup.

    Runs in a thread: Alembic's runner is synchronous, and this call happens
    before the app serves any request, so a brief blocking startup step is
    correct here (Rules.md: one-time setup, not a request path) while still
    not stalling the event loop the health-poll relies on.
    """
    await asyncio.to_thread(_upgrade_to_head)


async def dispose() -> None:
    await _engine.dispose()


_RRF_K = 60
_ARM_CANDIDATES = 50

_PAPER_ARM_SQL = text(
    """
    WITH dense AS (
        SELECT pc.id, ROW_NUMBER() OVER (ORDER BY pc.embedding <=> CAST(:embedding AS vector)) AS rnk
        FROM paper_chunks pc
        JOIN project_papers pp ON pp.paper_id = pc.paper_id
        WHERE pp.project_id = :project_id
          AND (CAST(:types AS text[]) IS NULL OR pc.source_type = ANY(CAST(:types AS text[])))
        ORDER BY pc.embedding <=> CAST(:embedding AS vector)
        LIMIT :k
    ),
    lexical AS (
        SELECT pc.id, ROW_NUMBER() OVER (ORDER BY ts_rank(pc.tsv, plainto_tsquery('english', :query_text)) DESC) AS rnk
        FROM paper_chunks pc
        JOIN project_papers pp ON pp.paper_id = pc.paper_id
        WHERE pp.project_id = :project_id
          AND (CAST(:types AS text[]) IS NULL OR pc.source_type = ANY(CAST(:types AS text[])))
          AND pc.tsv @@ plainto_tsquery('english', :query_text)
        ORDER BY rnk
        LIMIT :k
    )
    SELECT pc.id, pc.paper_id, pc.source_type, pc.source_id, pc.section_heading, pc.text,
           lower(pc.char_span) AS char_start, upper(pc.char_span) AS char_end,
           COALESCE(1.0 / (:rrf_k + dense.rnk), 0) + COALESCE(1.0 / (:rrf_k + lexical.rnk), 0) AS score
    FROM paper_chunks pc
    LEFT JOIN dense ON dense.id = pc.id
    LEFT JOIN lexical ON lexical.id = pc.id
    WHERE dense.id IS NOT NULL OR lexical.id IS NOT NULL
    """
)

_PROJECT_ARM_SQL = text(
    """
    WITH dense AS (
        SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:embedding AS vector)) AS rnk
        FROM project_chunks
        WHERE project_id = :project_id
          AND (CAST(:types AS text[]) IS NULL OR source_type = ANY(CAST(:types AS text[])))
        ORDER BY embedding <=> CAST(:embedding AS vector)
        LIMIT :k
    ),
    lexical AS (
        SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank(tsv, plainto_tsquery('english', :query_text)) DESC) AS rnk
        FROM project_chunks
        WHERE project_id = :project_id
          AND (CAST(:types AS text[]) IS NULL OR source_type = ANY(CAST(:types AS text[])))
          AND tsv @@ plainto_tsquery('english', :query_text)
        ORDER BY rnk
        LIMIT :k
    )
    SELECT pc.id, NULL::uuid AS paper_id, pc.source_type, CAST(pc.source_id AS text) AS source_id,
           NULL::text AS section_heading, pc.text,
           lower(pc.char_span) AS char_start, upper(pc.char_span) AS char_end,
           COALESCE(1.0 / (:rrf_k + dense.rnk), 0) + COALESCE(1.0 / (:rrf_k + lexical.rnk), 0) AS score
    FROM project_chunks pc
    LEFT JOIN dense ON dense.id = pc.id
    LEFT JOIN lexical ON lexical.id = pc.id
    WHERE dense.id IS NOT NULL OR lexical.id IS NOT NULL
    """
)


async def hybrid_retrieve(
    session: AsyncSession,
    project_id: uuid.UUID,
    query_text: str,
    query_embedding: list[float],
    types: list[str] | None = None,
    limit: int = 20,
) -> list[dict]:
    """The memory union (D25, Schema.md "Join strategies"): arm A —
    `paper_chunks` scoped through `project_papers` membership; arm B —
    `project_chunks` scoped by `project_id` directly. Each arm fuses its own
    dense (pgvector cosine) and lexical (`tsvector`) candidates via
    reciprocal rank fusion; the two arms are then merged and truncated here,
    never materialised into one table. Reranking is the caller's job — this
    returns fused candidates, not a final order.
    """
    params = {
        "project_id": project_id,
        "query_text": query_text,
        "embedding": str(query_embedding),
        "types": types,
        "k": _ARM_CANDIDATES,
        "rrf_k": _RRF_K,
    }
    paper_rows = (await session.execute(_PAPER_ARM_SQL, params)).mappings().all()
    project_rows = (await session.execute(_PROJECT_ARM_SQL, params)).mappings().all()

    merged = [dict(row, score=float(row["score"])) for row in [*paper_rows, *project_rows]]
    merged.sort(key=lambda row: row["score"], reverse=True)
    return merged[:limit]
