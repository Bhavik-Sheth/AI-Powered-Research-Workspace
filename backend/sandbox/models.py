"""Wire-shape models for Execution Sandbox (Rules.md: Pydantic model names match the wire shape)."""

import uuid
from typing import Literal

import nbformat
from pydantic import BaseModel


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
