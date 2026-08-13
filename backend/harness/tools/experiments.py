"""Mutations group (D19) — the experiments board (a lab notebook, not a run tracker)."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

import jobs
import sandbox
from experiments import create_experiment as _create_experiment
from experiments import update_experiment as _update_experiment
from experiments.models import ExperimentInput
from harness.models import Ref, ToolResult
from harness.registry import ToolContext, tool

# Mirrors `api/experiments.py`'s own `_RUN_JOB_TIMEOUT_S` — generous above
# `sandbox._IDLE_TIMEOUT_SECONDS` (the run's own internal wall-clock bound)
# so Job Queue's timeout is never what actually cuts a run off.
_RUN_JOB_TIMEOUT_S = 900


class LogExperimentArgs(BaseModel):
    title: str = Field(description="Short experiment title")
    hypothesis: str | None = Field(default=None, description="What this experiment is meant to test or show")
    notes: str | None = None


@tool(name="log_experiment", group="experiments", kind="action")
async def log_experiment(ctx: ToolContext, args: LogExperimentArgs) -> ToolResult:
    """Log a new entry on this project's experiments board (a lab notebook, not a run tracker) — records a hypothesis to test, not a result."""
    try:
        experiment = await _create_experiment(
            ctx.session, ctx.project_id, ExperimentInput(title=args.title, hypothesis=args.hypothesis, notes=args.notes)
        )
    except ValueError as exc:
        return ToolResult(model_view=str(exc))
    return ToolResult(
        model_view=f'Logged experiment "{experiment.title}".',
        refs=[Ref(kind="experiment", id=str(experiment.id), title=experiment.title)],
        ui_actions=[{"action": "log_experiment", "experiment_id": str(experiment.id)}],
    )


class UpdateExperimentArgs(BaseModel):
    experiment_id: str = Field(description="The experiment's UUID")
    title: str | None = None
    hypothesis: str | None = None
    notes: str | None = None
    status: Literal["planned", "remaining", "in-progress", "done"] | None = None


@tool(name="update_experiment", group="experiments", kind="action", core=False)
async def update_experiment(ctx: ToolContext, args: UpdateExperimentArgs) -> ToolResult:
    """Patch an existing experiment's title, hypothesis, notes, or status. Only the fields given are changed."""
    try:
        experiment_id = uuid.UUID(args.experiment_id)
    except ValueError:
        return ToolResult(model_view="That is not a valid experiment id.")
    try:
        experiment = await _update_experiment(
            ctx.session,
            experiment_id,
            ExperimentInput(title=args.title, hypothesis=args.hypothesis, notes=args.notes, status=args.status),
        )
    except ValueError as exc:
        return ToolResult(model_view=str(exc))
    return ToolResult(
        model_view=f'Updated experiment "{experiment.title}".',
        refs=[Ref(kind="experiment", id=str(experiment.id), title=experiment.title)],
        ui_actions=[{"action": "update_experiment", "experiment_id": str(experiment.id)}],
    )


class RunAllArgs(BaseModel):
    experiment_id: str = Field(description="The experiment's UUID")


@tool(name="run_all", group="experiments", kind="action", tier="confirm")
async def run_all(ctx: ToolContext, args: RunAllArgs) -> ToolResult:
    """Restart the kernel and run every cell in an experiment's notebook, inside a Docker container — requires explicit user approval before it runs.

    HarnessPlan H7, §3.9: the only path to a `source: measured` metric, and
    the reason `run_all` is `tier="confirm"` — by the time this handler
    runs, the human has already approved this exact call through the
    harness's own `approval_request`/`approval_response` events
    (`loop.py`'s three-way race), so that approval stands in for the UI's
    confirm-click and this mints its own one-shot token via
    `sandbox.mint_confirmation` rather than requiring a second round trip
    through `POST .../confirmation`. Everything after that — building the
    `RunSpec`, dispatching the run — calls exactly what
    `POST /api/experiments/:id/run_all` calls, so the DB invariant
    (`experiment_runs.approved_at NOT NULL`) is reached the same way either
    door is used. `network_optin`/`gpu` are **not** tool arguments: this
    call has no UI request body carrying a per-run override, so it uses the
    experiment's own already-stored `network_optin`/`gpu_optin` columns —
    what the human already opted into for this experiment — as the source
    of truth, exactly as `build_run_spec` already does for the `RunSpec`
    itself.
    """
    try:
        experiment_id = uuid.UUID(args.experiment_id)
    except ValueError:
        return ToolResult(model_view="That is not a valid experiment id.")

    if sandbox.notebook_server_status(experiment_id).state != "stopped":
        return ToolResult(
            model_view="A live notebook server is open for this experiment — stop it before running a measured pass."
        )

    try:
        experiment, spec = await sandbox.load_run_spec(ctx.session, experiment_id)
    except ValueError as exc:
        return ToolResult(model_view=str(exc))

    token = sandbox.mint_confirmation(experiment_id, spec)
    run_id = uuid.uuid4()
    await jobs.enqueue(
        "run_experiment_job",
        experiment_id=str(experiment_id),
        run_id=str(run_id),
        token=token.token,
        network_optin=experiment.network_optin,
        gpu=experiment.gpu_optin,
        timeout=_RUN_JOB_TIMEOUT_S,
    )
    return ToolResult(
        model_view=f'Started a measured run of "{experiment.title}" (run {run_id}). Watch the experiments board for progress.',
        refs=[Ref(kind="experiment", id=str(experiment.id), title=experiment.title)],
        ui_actions=[{"action": "run_all", "experiment_id": str(experiment.id), "run_id": str(run_id)}],
    )
