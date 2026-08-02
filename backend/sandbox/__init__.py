"""Execution Sandbox — runs notebook code in an isolated container only
after explicit human confirmation (MODULES.md).

Phase 2.1 ships `propose_cell` only: it writes a cell to the vault notebook
and never executes anything (D31, invariant #5) — no kernel, no Docker
invocation exists in this module yet. `RunSpec`/`MountSpec` (sandbox/models.py)
give the container spec its shape; `build_run_spec` constructs one from an
experiment record, but nothing calls it yet. `mint_confirmation`, `run_all`
and `stop_kernel` land in Phase 2.2/2.3 behind this same interface, per the
kernel-transport spike's descope decision (D30's "Kernel-transport spike
outcome" paragraph): the `nbclient`-under-one-shot-`docker run` fallback,
not a long-lived per-experiment kernel container.
"""

import uuid

import nbformat
from nbformat.v4 import new_code_cell, new_notebook
from sqlalchemy.ext.asyncio import AsyncSession

import experiments
import vault
from experiments.models import Experiment
from sandbox.models import MountSpec, Notebook, RunSpec
from settings import get_vault_path

# Pinned per docker/experiment-base.Dockerfile (TRD §2.7); a tag, not yet a
# digest — the digest is recorded per run once `run_all` exists (D29).
EXPERIMENT_BASE_IMAGE = "research-os-experiment-base:latest"

# Always-set limits (Rules.md Security Rules); no per-experiment override
# exists yet, so these are constants rather than parameters nothing can vary.
_CPU_LIMIT = 2.0
_MEMORY_LIMIT_MB = 4096
_IDLE_TIMEOUT_SECONDS = 600
_CELL_TIMEOUT_SECONDS = 120


async def propose_cell(
    session: AsyncSession, experiment_id: uuid.UUID, code: str, index: int | None = None
) -> Notebook:
    """Appends (or inserts at `index`) a code cell into the experiment's
    vault notebook and writes it back through Vault Writer. A freshly
    created `nbformat` code cell carries no `execution_count` and no
    `outputs` — that absence is the unrun, pending-approval signal the UI
    renders on (D31/D32); this call never executes the cell it writes.
    """
    experiment = await experiments.get_experiment(session, experiment_id)
    if experiment is None:
        raise ValueError(f"experiment {experiment_id} not found")

    if experiment.notebook_path is not None and (get_vault_path() / experiment.notebook_path).exists():
        notebook = nbformat.read(get_vault_path() / experiment.notebook_path, as_version=4)
    else:
        notebook = new_notebook()

    cell = new_code_cell(code)
    if index is None:
        notebook.cells.append(cell)
    else:
        notebook.cells.insert(index, cell)

    notebook_bytes = nbformat.writes(notebook, version=4).encode("utf-8")
    await vault.write_experiment_files(session, experiment_id, notebook_bytes)
    return Notebook.from_nbformat(experiment_id, notebook)


def build_run_spec(experiment: Experiment, project_slug: str) -> RunSpec:
    """Constructs the container spec for one experiment's run — mounts
    exactly `experiments/<exp>/` read-write and `library/` read-only, never
    the whole vault (Rules.md). Nothing consumes this yet; `run_all`
    (Phase 2.2) will pass it to the container invocation and to the
    approval prompt (D31).
    """
    return RunSpec(
        image=EXPERIMENT_BASE_IMAGE,
        mounts=[
            MountSpec(
                host_path=f"projects/{project_slug}/experiments/{experiment.slug}",
                container_path="/workspace",
                mode="rw",
            ),
            MountSpec(host_path="library", container_path="/workspace/library", mode="ro"),
        ],
        network="bridge" if experiment.network_optin else "none",
        cpu_limit=_CPU_LIMIT,
        memory_limit_mb=_MEMORY_LIMIT_MB,
        idle_timeout_seconds=_IDLE_TIMEOUT_SECONDS,
        cell_timeout_seconds=_CELL_TIMEOUT_SECONDS,
        gpu=experiment.gpu_optin,
    )
