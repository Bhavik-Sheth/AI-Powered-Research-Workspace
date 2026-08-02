"""Wire-shape models for Experiment Record (Rules.md: Pydantic model names match the wire shape)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from db.models import Experiments

ExperimentStatus = Literal["planned", "remaining", "in-progress", "done"]


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
