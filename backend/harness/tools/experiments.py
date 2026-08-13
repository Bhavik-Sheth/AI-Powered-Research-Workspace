"""Mutations group (D19) — the experiments board (a lab notebook, not a run tracker)."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from experiments import create_experiment as _create_experiment
from experiments import update_experiment as _update_experiment
from experiments.models import ExperimentInput
from harness.models import Ref, ToolResult
from harness.registry import ToolContext, tool


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


@tool(name="update_experiment", group="experiments", kind="action")
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
