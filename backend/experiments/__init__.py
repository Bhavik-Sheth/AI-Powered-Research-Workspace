"""Experiment Record — the structured record and the measured-gate rule (MODULES.md).

Phase 2.1 ships only `create_experiment`/`update_experiment` plus the read
path a REST list/get route needs (same gap Notes' `list_notes` fills — a
plain select is not a vault write, so it does not belong on Vault Writer's
surface). `record_metric`/`record_run` land in Phase 2.3 with the D29 gate;
this module knows nothing about Docker or how a container runs.
"""

import uuid

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Experiments, Project
from experiments.models import Experiment, ExperimentInput


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
