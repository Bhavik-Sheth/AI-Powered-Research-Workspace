"""Database Layer — pooled connection + the two hand-written SQL primitives (MODULES.md).

Phase 1.1 ships `session` and `run_migrations` only; `hybrid_retrieve` and
`traverse_graph` land with Memory Index (Phase 1.7) and Knowledge Graph
(Phase 3), the modules that need them.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
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
