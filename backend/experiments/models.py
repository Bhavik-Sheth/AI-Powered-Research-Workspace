"""Wire-shape models for Experiment Record (Rules.md: Pydantic model names match the wire shape)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from db.models import ExperimentMetrics, Experiments, ExperimentRuns

ExperimentStatus = Literal["planned", "remaining", "in-progress", "done"]

# Only `clean_run_all` may back a `measured` metric — this module's own
# fixed enum, not re-derived from `sandbox`'s `RunSpec`/`ExperimentRun`
# (Experiment Record must not know about Docker, MODULES.md).
RunKind = Literal["clean_run_all", "interactive"]

# `llm` is absent on purpose (D29, invariant): the CHECK constraint on
# `experiment_metrics.source` is the enforcement point, and this type-level
# restriction is the same rule expressed where a caller writes code, not a
# re-implementation of it.
MetricSource = Literal["user", "measured"]


class ExperimentInput(BaseModel):
    """What a caller submits to create or update an experiment record.

    All fields are optional so `update_experiment` can patch just one; on
    create, `title` is the only one that must be present in practice — the
    module enforces that, not this shape.
    """

    title: str | None = None
    hypothesis: str | None = None
    setup: dict | None = None
    notes: str | None = None
    status: ExperimentStatus | None = None
    network_optin: bool | None = None
    gpu_optin: bool | None = None


class Experiment(BaseModel):
    """The `experiments` row (Schema.md), returned by every experiments endpoint."""

    id: uuid.UUID
    project_id: uuid.UUID
    slug: str
    title: str
    hypothesis: str | None
    setup: dict
    notes: str | None
    status: ExperimentStatus
    notebook_path: str | None
    network_optin: bool
    gpu_optin: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Experiments) -> "Experiment":
        return cls(
            id=row.id,
            project_id=row.project_id,
            slug=row.slug,
            title=row.title,
            hypothesis=row.hypothesis,
            setup=row.setup,
            notes=row.notes,
            status=row.status,
            notebook_path=row.notebook_path,
            network_optin=row.network_optin,
            gpu_optin=row.gpu_optin,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class RunResult(BaseModel):
    """What Execution Sandbox hands `record_run` once a container run
    finishes (MODULES.md's own signature for `record_run`) — everything
    needed to construct the `experiment_runs` row, without this module
    knowing anything about Docker, containers, or how the run happened.

    `artifacts` is carried as plain dicts (Schema.md's `[{path, kind,
    bytes}]` JSONB shape) rather than `vault.models.RunArtifactFile` —
    Experiment Record depends on Database Layer only (MODULES.md); it must
    not import Vault Writer's types just to describe a run's outputs.
    """

    id: uuid.UUID
    experiment_id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    exit_code: int | None
    image: str
    reqs_hash: str
    notebook_hash: str
    stdout_ref: str
    artifacts: list[dict] = []
    run_kind: RunKind
    network_enabled: bool
    gpu_enabled: bool
    approved_at: datetime


class ExperimentRun(BaseModel):
    """The `experiment_runs` row (Schema.md) — the strongest provenance in
    the system. `approved_at` is always set: a row cannot exist without a
    validated confirmation token (D31, invariant #5). This is the type
    `record_run` returns and `is_measured_eligible` inspects; Execution
    Sandbox re-exports it (`sandbox.models.ExperimentRun`) rather than
    defining a second, duplicate shape, since Experiment Record is the
    module that persists this row (MODULES.md's own `record_run` signature).
    """

    id: uuid.UUID
    experiment_id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    exit_code: int | None
    image: str
    reqs_hash: str
    notebook_hash: str
    stdout_ref: str
    artifacts: list[dict]
    run_kind: RunKind
    network_enabled: bool
    gpu_enabled: bool
    approved_at: datetime

    @classmethod
    def from_row(cls, row: ExperimentRuns) -> "ExperimentRun":
        return cls(
            id=row.id,
            experiment_id=row.experiment_id,
            started_at=row.started_at,
            finished_at=row.finished_at,
            exit_code=row.exit_code,
            image=row.image,
            reqs_hash=row.reqs_hash,
            notebook_hash=row.notebook_hash,
            stdout_ref=row.stdout_ref,
            artifacts=row.artifacts,
            run_kind=row.run_kind,
            network_enabled=row.network_enabled,
            gpu_enabled=row.gpu_enabled,
            approved_at=row.approved_at,
        )


class MetricInput(BaseModel):
    """What a caller submits to `record_metric`. `source: llm` is
    unrepresentable at the type level (`MetricSource`); `run_id` is required
    by the caller only when `source='measured'` — the DB `CHECK` is what
    actually enforces that pairing (MODULES.md), this shape just allows it.
    """

    name: str
    value: str
    unit: str | None = None
    source: MetricSource
    run_id: uuid.UUID | None = None


class ExperimentMetric(BaseModel):
    """The `experiment_metrics` row (Schema.md), returned by the metrics endpoint."""

    id: uuid.UUID
    experiment_id: uuid.UUID
    name: str
    value: str
    unit: str | None
    source: MetricSource
    run_id: uuid.UUID | None
    recorded_at: datetime

    @classmethod
    def from_row(cls, row: ExperimentMetrics) -> "ExperimentMetric":
        return cls(
            id=row.id,
            experiment_id=row.experiment_id,
            name=row.name,
            value=row.value,
            unit=row.unit,
            source=row.source,
            run_id=row.run_id,
            recorded_at=row.recorded_at,
        )
