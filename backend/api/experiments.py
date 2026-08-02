"""`GET/POST/PATCH /api/projects/:id/experiments` — backs Experiment Record (TRD §4.2, US9)."""

import uuid

from fastapi import APIRouter, HTTPException

import db
import experiments
from experiments.models import Experiment, ExperimentInput

router = APIRouter()


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
