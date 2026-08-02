"""`GET/POST/PATCH /api/projects/:id/experiments` — backs Experiment Record
(TRD §4.2, US9) — and the Phase 2.2 approval-gate routes: `run_spec`
(preview), `confirmation` (mints the token), `kernel` (honest no-persistent-
kernel status/cancel), `run_all` (dispatches the actual run via Job Queue).
"""

import uuid
from typing import Literal

import nbformat
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
import experiments
import jobs
import sandbox
from db.models import Experiments as ExperimentsRow
from experiments.models import Experiment, ExperimentInput
from sandbox.models import ConfirmationToken, KernelStatus, Notebook, RunSpec
from settings import get_vault_path

router = APIRouter()

# Generous ceiling above `sandbox._IDLE_TIMEOUT_SECONDS` (the run's own
# internal wall-clock bound) so Job Queue's own timeout is never the thing
# that actually cuts a run off.
_RUN_JOB_TIMEOUT_S = 900


@router.get("/api/projects/{project_id}/experiments", response_model=list[Experiment])
async def list_experiments(project_id: uuid.UUID) -> list[Experiment]:
    async with db.session() as session:
        return await experiments.list_experiments(session, project_id)


@router.post("/api/projects/{project_id}/experiments", response_model=Experiment)
async def create_experiment(project_id: uuid.UUID, body: ExperimentInput) -> Experiment:
    async with db.session() as session:
        try:
            return await experiments.create_experiment(session, project_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/api/projects/{project_id}/experiments/{experiment_id}", response_model=Experiment)
async def update_experiment(project_id: uuid.UUID, experiment_id: uuid.UUID, body: ExperimentInput) -> Experiment:
    async with db.session() as session:
        try:
            return await experiments.update_experiment(session, experiment_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


class RunSpecPreview(BaseModel):
    """What the approval prompt renders before a human confirms: the
    proposed code and the full container spec (image, mounts, network,
    GPU) — D31's explicit requirement for what the prompt must show."""

    notebook: Notebook
    run_spec: RunSpec


@router.get("/api/experiments/{experiment_id}/run_spec", response_model=RunSpecPreview)
async def get_run_spec(experiment_id: uuid.UUID) -> RunSpecPreview:
    """No side effect, no token minted — a caller can fetch this as many
    times as it likes while the human is still reading."""
    async with db.session() as session:
        try:
            _experiment, spec = await sandbox.load_run_spec(session, experiment_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        row = await session.get(ExperimentsRow, experiment_id)
        if row.notebook_path and (get_vault_path() / row.notebook_path).exists():
            notebook = Notebook.from_nbformat(
                experiment_id, nbformat.read(get_vault_path() / row.notebook_path, as_version=4)
            )
        else:
            notebook = Notebook(experiment_id=experiment_id, cells=[])
    return RunSpecPreview(notebook=notebook, run_spec=spec)


@router.post("/api/experiments/{experiment_id}/confirmation", response_model=ConfirmationToken)
async def create_confirmation(experiment_id: uuid.UUID) -> ConfirmationToken:
    """Mints the one-shot token `run_all` requires. Always recomputes the
    spec itself rather than trusting anything the caller might send —
    `mint_confirmation` is issued only after *this* call has the spec in
    hand (D31)."""
    async with db.session() as session:
        try:
            _experiment, spec = await sandbox.load_run_spec(session, experiment_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return sandbox.mint_confirmation(experiment_id, spec)


class KernelActionRequest(BaseModel):
    action: Literal["start", "stop"]


@router.post("/api/experiments/{experiment_id}/kernel", response_model=KernelStatus)
async def kernel_action(experiment_id: uuid.UUID, body: KernelActionRequest) -> KernelStatus:
    """`"start"` has nothing real to start under the `nbclient` fallback —
    there is no persistent per-experiment kernel (D30's descope) — so it
    just reports current status, same as a bare query would.
    `"stop"` is real: it cancels this experiment's in-flight run container,
    if any (`sandbox.stop_kernel`)."""
    if body.action == "stop":
        await sandbox.stop_kernel(experiment_id)
    return sandbox.kernel_status(experiment_id)


class RunAllRequest(BaseModel):
    confirmation_token: str
    network_optin: bool = False
    gpu: bool = False


class RunAllResponse(BaseModel):
    run_id: uuid.UUID


@router.post("/api/experiments/{experiment_id}/run_all", response_model=RunAllResponse)
async def run_all(experiment_id: uuid.UUID, body: RunAllRequest) -> RunAllResponse:
    """Dispatches the run via Job Queue rather than awaiting it inline —
    the container execution can run long, and cancellation/log streaming
    already ride the job/WS pipes independently of this request. The
    `run_id` is assigned here so the client has a stable id to correlate
    against `run_log`/`run_status` events on the project's WebSocket before
    the run itself has produced anything."""
    async with db.session() as session:
        if await experiments.get_experiment(session, experiment_id) is None:
            raise HTTPException(status_code=404, detail=f"experiment {experiment_id} not found")

    run_id = uuid.uuid4()
    await jobs.enqueue(
        "run_experiment_job",
        experiment_id=str(experiment_id),
        run_id=str(run_id),
        token=body.confirmation_token,
        network_optin=body.network_optin,
        gpu=body.gpu,
        timeout=_RUN_JOB_TIMEOUT_S,
    )
    return RunAllResponse(run_id=run_id)
