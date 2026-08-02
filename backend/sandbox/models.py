"""Wire-shape models for Execution Sandbox (Rules.md: Pydantic model names match the wire shape)."""

import uuid
from datetime import datetime
from typing import Literal

import nbformat
from pydantic import BaseModel

from vault.models import RunArtifactFile


class NotebookCell(BaseModel):
    """One cell as the UI needs to render it. `execution_count` and
    `outputs` absent/empty is the unrun signal `propose_cell` relies on —
    nbformat's own freshly-created code cell already carries no other kind
    of "pending approval" marker, so none is invented here (MODULES.md)."""

    cell_type: Literal["code", "markdown", "raw"]
    source: str
    execution_count: int | None = None
    outputs: list[dict] = []


class Notebook(BaseModel):
    """The vault notebook's cell list, returned by `propose_cell`."""

    experiment_id: uuid.UUID
    cells: list[NotebookCell]

    @classmethod
    def from_nbformat(cls, experiment_id: uuid.UUID, nb: nbformat.NotebookNode) -> "Notebook":
        return cls(
            experiment_id=experiment_id,
            cells=[
                NotebookCell(
                    cell_type=cell["cell_type"],
                    source=cell["source"],
                    execution_count=cell.get("execution_count"),
                    outputs=cell.get("outputs", []),
                )
                for cell in nb["cells"]
            ],
        )


class MountSpec(BaseModel):
    """One bind mount into the run container (D30/Rules.md: never the whole
    vault, never `$HOME`)."""

    host_path: str  # vault-relative
    container_path: str
    mode: Literal["ro", "rw"]


class RunSpec(BaseModel):
    """The container spec the approval prompt displays before `run_all` can
    mint a confirmation (D31) — image, mounts, network, and the limits that
    are "always set" per Rules.md's Security Rules. Nothing in Phase 2.1
    consumes this yet; `mint_confirmation`/`run_all` (Phase 2.2) do."""

    image: str
    mounts: list[MountSpec]
    network: Literal["none", "bridge"] = "none"
    cpu_limit: float
    memory_limit_mb: int
    idle_timeout_seconds: int
    cell_timeout_seconds: int
    gpu: bool = False


class ConfirmationToken(BaseModel):
    """The one-shot credential `run_all` requires (D31) — opaque beyond the
    fields the approval prompt needs to display (`expires_at`, for a
    countdown or a "this approval expired, review again" message).
    """

    token: str
    experiment_id: uuid.UUID
    expires_at: datetime


class ExperimentRun(BaseModel):
    """The `experiment_runs` row (Schema.md) — the strongest provenance in
    the system. `approved_at` is always set: a row cannot exist without a
    validated confirmation token (D31, invariant #5)."""

    id: uuid.UUID
    experiment_id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    exit_code: int | None
    image: str
    reqs_hash: str
    notebook_hash: str
    stdout_ref: str
    artifacts: list[RunArtifactFile]
    run_kind: Literal["clean_run_all", "interactive"]
    network_enabled: bool
    gpu_enabled: bool
    approved_at: datetime

    @classmethod
    def from_row(cls, row) -> "ExperimentRun":
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
            artifacts=[RunArtifactFile(**artifact) for artifact in row.artifacts],
            run_kind=row.run_kind,
            network_enabled=row.network_enabled,
            gpu_enabled=row.gpu_enabled,
            approved_at=row.approved_at,
        )


class KernelStatus(BaseModel):
    """Response for `POST /api/experiments/:id/kernel`. Under the `nbclient`
    fallback (D30's descope) there is no persistent per-experiment kernel to
    start or stop — containers are per-run, not per-experiment. `state`
    honestly reports the only kernel-shaped fact that exists here: whether
    this experiment currently has an in-flight run container. `"start"`
    cannot create anything real to report back beyond this same status;
    `"stop"` maps to cancelling that in-flight container, if any.
    """

    experiment_id: uuid.UUID
    state: Literal["idle", "running"]
    run_id: uuid.UUID | None = None


class RunLogEvent(BaseModel):
    """One captured line of a run container's output. Broadcast over the
    project's existing Session Transport socket but deliberately **not**
    part of `harness.models.TurnEvent` — see `sandbox/__init__.py`'s module
    docstring for why."""

    event: Literal["run_log"] = "run_log"
    experiment_id: uuid.UUID
    run_id: uuid.UUID
    line: str


class RunStatusEvent(BaseModel):
    """A run's lifecycle transition, broadcast the same way as `RunLogEvent`."""

    event: Literal["run_status"] = "run_status"
    experiment_id: uuid.UUID
    run_id: uuid.UUID
    status: Literal["running", "done", "failed"]
    exit_code: int | None = None
