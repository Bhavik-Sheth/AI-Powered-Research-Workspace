"""`GET /api/runs/:runId` (PRD's REST table, ~line 363: "Outputs, logs, exit
code, image digest, hashes") and `POST /api/experiments/:id/metrics`.

The metrics route is not named in PRD's REST table, but the Phase 2
checklist requires "`source: user` metrics are typed by hand and fully
supported" — that capability needs *some* API path, and this is Experiment
Record's own resource boundary (same split as `papers.py` vs.
`highlights.py`: one router per owning module's resource, thin route glue,
no SQL or gate logic here — both live in `experiments`).
"""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
import experiments
from experiments.models import ExperimentMetric, ExperimentRun, MetricInput
from settings import get_vault_path

router = APIRouter()


class RunDetail(BaseModel):
    """`ExperimentRun` plus the run's captured stdout log content — the log
    itself lives on disk (`stdout_ref` is a vault-relative path), so this
    reads it back rather than making the caller resolve the path itself."""

    run: ExperimentRun
    stdout: str


@router.get("/api/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID) -> RunDetail:
    """A plain-text response is enough for now: the captured stdout of one
    notebook run is bounded by the sandbox's own cell/idle timeouts, not an
    arbitrarily large stream — unlike a paper PDF (`GET /api/papers/:id/pdf`,
    which streams via `FileResponse` because PDFs can be large binaries),
    there is no comparable size case here yet. If run logs grow large enough
    to matter, this is the seam to swap for a streaming response.
    """
    async with db.session() as session:
        run = await experiments.get_run(session, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    log_path = get_vault_path() / run.stdout_ref
    stdout = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    return RunDetail(run=run, stdout=stdout)


@router.post("/api/experiments/{experiment_id}/metrics", response_model=ExperimentMetric)
async def create_metric(experiment_id: uuid.UUID, body: MetricInput) -> ExperimentMetric:
    async with db.session() as session:
        try:
            return await experiments.record_metric(session, experiment_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
