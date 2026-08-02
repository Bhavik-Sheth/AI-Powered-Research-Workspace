"""Job Queue — cancellable background work off the request path (MODULES.md).

Phase 1.1 ships the worker lifecycle and the catch-up pass only. `enqueue`
and `cancel` land in Phase 1.3 with the first real job kind (search/parse
dispatch) — that is also when the transactional-enqueue guarantee (D9: the
job row commits in the same transaction as the row it concerns) gets wired.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from saq.queue.postgres import PostgresQueue
from saq.worker import Worker

from config import get_config
from db import session
from db.models import ScheduledJobs

logger = logging.getLogger(__name__)

_queue: PostgresQueue | None = None
_worker: Worker | None = None
_worker_task: asyncio.Task[None] | None = None


async def start() -> None:
    """Connects the queue and starts the worker loop as a background task."""
    global _queue, _worker, _worker_task
    _queue = PostgresQueue.from_url(get_config().libpq_dsn)
    await _queue.connect()
    _worker = Worker(_queue, functions=[], shutdown_grace_period_s=5)
    # saq.Worker.start() unconditionally installs its own SIGINT/SIGTERM
    # handlers, which would steal the signal from uvicorn's (Sidecar
    # Bootstrap owns process shutdown, not the worker). Shutdown is already
    # driven explicitly by `stop()` below, so disable saq's own handling.
    _worker.SIGNALS = []
    _worker_task = asyncio.create_task(_worker.start())


async def stop() -> None:
    """Stops the worker and disconnects the queue, on shutdown."""
    if _worker is not None:
        await _worker.stop()
    if _worker_task is not None:
        await _worker_task
    if _queue is not None:
        await _queue.disconnect()


async def run_catchup_pass() -> None:
    """Runs any `scheduled_jobs` overdue since `last_run_at`, once, at startup.

    No `job_kind` has a registered handler yet — the first one (`feed_poll`)
    arrives in Phase 5. Until then this legitimately finds nothing to do; it
    exists now so the mechanism (table + startup pass) is proven end to end.
    """
    now = datetime.now(timezone.utc)
    async with session() as db_session:
        overdue = (await db_session.scalars(select(ScheduledJobs).where(ScheduledJobs.next_due_at <= now))).all()
    if overdue:
        logger.info("job_kind=catchup event=overdue_found count=%d", len(overdue))
