"""Job Queue — cancellable background work off the request path (MODULES.md).

`enqueue` and the registered job kinds landed in Phase 1.4 with Paper
Pipeline's fetch/parse/extract/enrich chain, the first real dispatch. The
transactional-enqueue guarantee (D9: the job row commits in the same
transaction as the row it concerns) is not wired — SAQ's Postgres queue
owns its own connection pool, separate from SQLAlchemy's; `enqueue` runs
immediately after the caller's own transaction commits, which is a known,
narrow gap (a crash in between drops the job, not the row), not a silent
best-effort dressed up as the real guarantee.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from saq.job import Job
from saq.queue.postgres import PostgresQueue
from saq.worker import Worker

from config import get_config
from db import session
from db.models import Project, ScheduledJobs

logger = logging.getLogger(__name__)

_queue: PostgresQueue | None = None
_worker: Worker | None = None
_worker_task: asyncio.Task[None] | None = None

# The catch-up cursor's own schedule kinds (Schema.md `scheduled_jobs.job_kind`
# CHECK) mapped to the SAQ-registered handler name and its cadence — Job
# Queue owns this mapping (MODULES.md: "Hides: ...the catch-up-on-launch
# cursor check"), not Research Feed. PRD §14 leaves the exact feed cadence
# open/tunable post-v1; daily is a reasonable default until then.
_FEED_POLL_INTERVAL_S = 24 * 60 * 60
_INITIAL_FEED_LOOKBACK = timedelta(days=14)
_INTEREST_PROFILE_REEXTRACT_INTERVAL_S = 7 * 24 * 60 * 60  # weekly (PRD §14, TRD §3.6)

# SAQ's own job default is 10s (see papers/__init__.py's same note) — both
# jobs below fan out to external literature APIs and/or cold-load an ML
# model on first run, far longer than that default.
_FEED_POLL_JOB_TIMEOUT_S = 300
_REEXTRACT_JOB_TIMEOUT_S = 60


def _feed_poll_kwargs(row: ScheduledJobs) -> dict:
    since = row.last_run_at or (datetime.now(timezone.utc) - _INITIAL_FEED_LOOKBACK)
    return {"project_id": str(row.project_id), "since": since.isoformat(), "timeout": _FEED_POLL_JOB_TIMEOUT_S}


def _reextract_kwargs(row: ScheduledJobs) -> dict:
    return {"project_id": str(row.project_id), "timeout": _REEXTRACT_JOB_TIMEOUT_S}


# job_kind -> (SAQ-registered handler name, cadence seconds, kwargs builder).
_SCHEDULE_KINDS: dict[str, tuple[str, int, Callable[[ScheduledJobs], dict]]] = {
    "feed_poll": ("poll_feed_job", _FEED_POLL_INTERVAL_S, _feed_poll_kwargs),
    "interest_profile_reextract": (
        "interest_profile_reextract_job",
        _INTEREST_PROFILE_REEXTRACT_INTERVAL_S,
        _reextract_kwargs,
    ),
}


def _job_functions() -> list:
    # Imported locally, not at module top: papers/ imports jobs/ (to call
    # `enqueue` from within its own job handlers), so a top-level import
    # here would cycle. By the time `start()` runs (Sidecar Bootstrap's
    # lifespan), papers/ is already fully loaded via main.py's own imports.
    from feed import interest_profile_reextract_job, poll_feed_job
    from memory import chunk_and_embed_job
    from papers import embed_paper_job, enrich_paper_job, extract_card_job, parse_paper_job, trace_references_job
    from sandbox import run_experiment_job

    return [
        parse_paper_job,
        extract_card_job,
        enrich_paper_job,
        embed_paper_job,
        trace_references_job,
        chunk_and_embed_job,
        run_experiment_job,
        poll_feed_job,
        interest_profile_reextract_job,
    ]


async def start() -> None:
    """Connects the queue and starts the worker loop as a background task."""
    global _queue, _worker, _worker_task
    _queue = PostgresQueue.from_url(get_config().libpq_dsn)
    await _queue.connect()
    _worker = Worker(_queue, functions=_job_functions(), shutdown_grace_period_s=5)
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


async def enqueue(job_kind: str, **payload: object) -> Job | None:
    """Enqueues one job by its registered name (a function passed to `start`)."""
    if _queue is None:
        raise RuntimeError("job queue is not started")
    return await _queue.enqueue(job_kind, **payload)


async def _ensure_schedule_rows(db_session, job_kind: str, interval_seconds: int, now: datetime) -> None:
    """Every project gets a `scheduled_jobs` row for `job_kind` the first
    time this runs after the project exists — due immediately (`next_due_at
    = now`) so a brand-new project's first feed poll happens on the very
    next catch-up pass, not a full interval later."""
    project_ids = (await db_session.scalars(select(Project.id))).all()
    scheduled_ids = set(
        await db_session.scalars(select(ScheduledJobs.project_id).where(ScheduledJobs.job_kind == job_kind))
    )
    for project_id in project_ids:
        if project_id not in scheduled_ids:
            db_session.add(
                ScheduledJobs(job_kind=job_kind, project_id=project_id, interval_seconds=interval_seconds, next_due_at=now)
            )


async def run_catchup_pass() -> None:
    """Runs any `scheduled_jobs` overdue since `last_run_at`, once, at startup."""
    now = datetime.now(timezone.utc)
    async with session() as db_session:
        for job_kind, (_, interval_seconds, _) in _SCHEDULE_KINDS.items():
            await _ensure_schedule_rows(db_session, job_kind, interval_seconds, now)
        await db_session.flush()

        overdue = (await db_session.scalars(select(ScheduledJobs).where(ScheduledJobs.next_due_at <= now))).all()
        for row in overdue:
            registered = _SCHEDULE_KINDS.get(row.job_kind)
            if registered is None:
                continue
            function_name, interval_seconds, kwargs_builder = registered
            await enqueue(function_name, **kwargs_builder(row))
            row.last_run_at = now
            row.next_due_at = now + timedelta(seconds=interval_seconds)

    if overdue:
        logger.info("job_kind=catchup event=overdue_dispatched count=%d", len(overdue))
