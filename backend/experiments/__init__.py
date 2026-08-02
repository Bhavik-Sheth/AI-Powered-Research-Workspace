"""Experiment Record — the structured record and the measured-gate rule (MODULES.md).

Phase 2.1 shipped only `create_experiment`/`update_experiment` plus the read
path a REST list/get route needs (same gap Notes' `list_notes` fills — a
plain select is not a vault write, so it does not belong on Vault Writer's
surface). Phase 2.3 adds `record_run`/`record_metric` with the D29 gate.

**The measured gate, twice.** `is_measured_eligible` is a pure, pre-DB
predicate `record_metric` fast-fails against before ever touching the
database — but the actual enforcement point is still
`experiment_metrics`'s own `CHECK (source <> 'measured' OR run_id IS NOT
NULL)` plus `experiment_runs.run_kind`'s CHECK (MODULES.md: "the DB CHECK
is the enforcement point, not a re-implementation here"). Both exist for
different reasons: the DB constraint is what makes the rule true no matter
what code path reaches it; the Python predicate exists so `record_metric`
can refuse a doomed `measured` write before a round trip, and so the D29
pytest suite has a deterministic, DB-free function to assert against
(Rules.md's mocking policy: no Postgres in that suite).
"""

import uuid

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ExperimentMetrics, Experiments, ExperimentRuns, Project
from experiments.models import (
    Experiment,
    ExperimentInput,
    ExperimentMetric,
    ExperimentRun,
    MetricInput,
    RunResult,
)


async def list_experiments(session: AsyncSession, project_id: uuid.UUID) -> list[Experiment]:
    """Newest first — backs the experiments board (Schema.md's
    `(project_id, created_at DESC)` index)."""
    rows = await session.scalars(
        select(Experiments).where(Experiments.project_id == project_id).order_by(Experiments.created_at.desc())
    )
    return [Experiment.from_row(row) for row in rows]


async def get_experiment(session: AsyncSession, experiment_id: uuid.UUID) -> Experiment | None:
    row = await session.get(Experiments, experiment_id)
    return Experiment.from_row(row) if row is not None else None


async def _unique_slug(session: AsyncSession, project_id: uuid.UUID, title: str) -> str:
    base = slugify(title) or "experiment"
    candidate = base
    suffix = 1
    while True:
        collision = await session.scalar(
            select(Experiments.id).where(Experiments.project_id == project_id, Experiments.slug == candidate)
        )
        if collision is None:
            return candidate
        suffix += 1
        candidate = f"{base}-{suffix}"


async def create_experiment(session: AsyncSession, project_id: uuid.UUID, fields: ExperimentInput) -> Experiment:
    """Creates the structured record; `slug` is derived from `title` and
    made unique within the project (names `projects/<slug>/experiments/<exp-slug>/`,
    MODULES.md/Schema.md)."""
    if fields.title is None:
        raise ValueError("title is required to create an experiment")
    project = await session.get(Project, project_id)
    if project is None:
        raise ValueError(f"project {project_id} not found")

    slug = await _unique_slug(session, project_id, fields.title)
    row = Experiments(
        project_id=project_id,
        slug=slug,
        title=fields.title,
        hypothesis=fields.hypothesis,
        setup=fields.setup or {},
        notes=fields.notes,
        status=fields.status or "planned",
        network_optin=fields.network_optin or False,
        gpu_optin=fields.gpu_optin or False,
    )
    session.add(row)
    await session.flush()
    return Experiment.from_row(row)


async def update_experiment(session: AsyncSession, experiment_id: uuid.UUID, fields: ExperimentInput) -> Experiment:
    """Patches the fields present on `fields`; `slug` never changes on update
    — only Vault Writer's `write_experiment_files` derives a path from it,
    and that path must stay stable once assigned."""
    row = await session.get(Experiments, experiment_id)
    if row is None:
        raise ValueError(f"experiment {experiment_id} not found")

    for field in ("title", "hypothesis", "setup", "notes", "status", "network_optin", "gpu_optin"):
        value = getattr(fields, field)
        if value is not None:
            setattr(row, field, value)
    await session.flush()
    return Experiment.from_row(row)


async def record_run(session: AsyncSession, experiment_id: uuid.UUID, run: RunResult) -> ExperimentRun:
    """Persists one `experiment_runs` row — the boundary Execution Sandbox's
    `run_all` used to cross directly (a `Depends on: Vault Writer,
    Experiment Record` violation per MODULES.md). Execution Sandbox
    completes the run (container exit, hashes, image digest) and hands the
    finished `RunResult` here; this module owns writing the row and nothing
    about how the run happened.
    """
    row = ExperimentRuns(
        id=run.id,
        experiment_id=experiment_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        exit_code=run.exit_code,
        image=run.image,
        reqs_hash=run.reqs_hash,
        notebook_hash=run.notebook_hash,
        stdout_ref=run.stdout_ref,
        artifacts=run.artifacts,
        run_kind=run.run_kind,
        network_enabled=run.network_enabled,
        gpu_enabled=run.gpu_enabled,
        approved_at=run.approved_at,
    )
    session.add(row)
    await session.flush()
    return ExperimentRun.from_row(row)


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> ExperimentRun | None:
    """Backs `GET /api/runs/:runId` (PRD's REST table)."""
    row = await session.get(ExperimentRuns, run_id)
    return ExperimentRun.from_row(row) if row is not None else None


def is_measured_eligible(run: ExperimentRun) -> bool:
    """The D29 gate as a pure predicate: true only for a clean
    `run_kind='clean_run_all'` run that exited `0` and carries all four
    provenance fields — a present `id` (the `run_id` a metric would
    reference), image digest, `reqs_hash`, `notebook_hash`, and a timestamp
    (`approved_at`, which cannot exist without a validated confirmation
    token, D31). An `'interactive'` run_kind, a non-zero or missing exit
    code, or any missing provenance field is never eligible — this is the
    same rule `experiment_metrics`'s CHECK enforces at rest, expressed here
    so `record_metric` can fail fast and the D29 suite has a pure function
    to test (Rules.md).
    """
    return (
        run.run_kind == "clean_run_all"
        and run.exit_code == 0
        and bool(run.id)
        and bool(run.image)
        and bool(run.reqs_hash)
        and bool(run.notebook_hash)
        and run.approved_at is not None
    )


async def record_metric(session: AsyncSession, experiment_id: uuid.UUID, metric: MetricInput) -> ExperimentMetric:
    """Inserts one `experiment_metrics` row. `source` is `user` or
    `measured` only (`MetricInput.source`'s type already makes `llm`
    unrepresentable); a `measured` write additionally fast-fails against
    `is_measured_eligible` before the DB round trip, but the DB `CHECK
    (source <> 'measured' OR run_id IS NOT NULL)` remains the actual
    enforcement point — this call does not re-implement it, only avoids a
    doomed round trip (MODULES.md).
    """
    if metric.source == "measured":
        if metric.run_id is None:
            raise ValueError("a measured metric requires run_id")
        run = await get_run(session, metric.run_id)
        if run is None:
            raise ValueError(f"run {metric.run_id} not found")
        if run.experiment_id != experiment_id:
            raise ValueError(f"run {metric.run_id} does not belong to experiment {experiment_id}")
        if not is_measured_eligible(run):
            raise ValueError(
                f"run {metric.run_id} is not eligible to back a measured metric "
                "(requires run_kind='clean_run_all', exit_code=0, and all provenance fields present)"
            )

    row = ExperimentMetrics(
        experiment_id=experiment_id,
        name=metric.name,
        value=metric.value,
        unit=metric.unit,
        source=metric.source,
        run_id=metric.run_id,
    )
    session.add(row)
    await session.flush()
    return ExperimentMetric.from_row(row)
