"""Execution Sandbox — runs notebook code in an isolated container only
after explicit human confirmation (MODULES.md).

Phase 2.1 shipped `propose_cell` only. Phase 2.2 adds the actual path to
execution, per D30's kernel-transport spike descope: there is no
persistent per-experiment kernel. `run_all` spins up a **new container per
run** via one-shot `docker run`, executing the whole notebook top to
bottom with `nbclient` *inside* the container (`docker/run_notebook.py`,
baked into the base image) and capturing the executed notebook + stdout +
exit code. `run_kind` is therefore always `'clean_run_all'` — this module
has no code path that produces `'interactive'`.

**Confirmation tokens** (`mint_confirmation`/`run_all`) are kept in an
in-process dict, not persisted: this is a single local desktop process
(same reasoning Session Transport's `_sessions` registry documents), so a
token that doesn't survive a sidecar restart is fine — the run it would
authorize couldn't survive one either. Tokens are minted via stdlib
`secrets.token_urlsafe`, never custom randomness (Rules.md).

**Run-log streaming over Session Transport (a documented judgment call).**
A run's stdout lines and status transitions need to reach the UI live, and
the existing per-project WebSocket (`backend/ws/`) is the obvious pipe to
reuse rather than standing up a second one. But a run is not a Companion
turn: `harness.models.TurnEvent` is a closed union describing exactly the
harness's own event shapes, and folding run events into it would make
every `TurnEvent` consumer (Session Transport, the frontend's turn
reducer) reason about a case that has nothing to do with a turn. Instead,
`RunLogEvent`/`RunStatusEvent` (`sandbox/models.py`) are a **separate**
model family, broadcast over the *same* per-project socket via `ws`'s
`get_session`/`broadcast` (widened to accept any event model, not only
`TurnEvent` — see `ws/__init__.py`). The frontend discriminates purely on
each message's own `event` field, same as it already must for the
`TurnEvent` union; nothing about `TurnEvent`'s semantics changes.

**The kernel route's honest semantics.** `POST .../kernel {action}` has no
"start a kernel" to perform under this execution model — see `KernelStatus`
and `kernel_status` below. `"stop"` is real: it cancels the in-flight run
container for the experiment, if any (`stop_kernel`).
"""

import asyncio
import hashlib
import queue
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import docker
import nbformat
from nbformat.v4 import new_code_cell, new_notebook
from sqlalchemy.ext.asyncio import AsyncSession

import db
import experiments
import vault
import ws
from db.models import ExperimentRuns, Project
from experiments.models import Experiment
from sandbox.models import (
    ConfirmationToken,
    ExperimentRun,
    KernelStatus,
    MountSpec,
    Notebook,
    RunLogEvent,
    RunSpec,
    RunStatusEvent,
)
from settings import get_vault_path
from vault.models import RunArtifactFile, RunArtifacts

# Pinned per docker/experiment-base.Dockerfile (TRD §2.7); a tag, not yet a
# digest — the digest (the local image's own content-addressed id, since
# this image is never pushed to a registry) is recorded per run in
# `run_all`, once there is a run to record it against.
EXPERIMENT_BASE_IMAGE = "research-os-experiment-base:latest"

# Always-set limits (Rules.md Security Rules); no per-experiment override
# exists yet, so these are constants rather than parameters nothing can vary.
_CPU_LIMIT = 2.0
_MEMORY_LIMIT_MB = 4096
_IDLE_TIMEOUT_SECONDS = 600
_CELL_TIMEOUT_SECONDS = 120

# A confirmation token's lifetime: long enough to read the code and the
# container spec before clicking confirm, short enough that approving a
# stale spec can't succeed much later. `run_all` recomputes the spec fresh
# and compares hashes regardless, so this is a second, independent bound —
# not the only thing standing between an old approval and a new run.
_TOKEN_TTL_SECONDS = 300

_OUTPUTS_DIRNAME = "outputs"
_RUNNER_SCRIPT_PATH = "/opt/run_notebook.py"

_docker_client: docker.DockerClient | None = None


def _get_docker_client() -> docker.DockerClient:
    """Lazy singleton: importing this module must not require a live Docker
    daemon (module import happens well before Sidecar Bootstrap's own
    Docker readiness check runs)."""
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.from_env()
    return _docker_client


class ConfirmationError(ValueError):
    """A confirmation token failed validation — missing, expired, already
    used, minted for a different experiment, or minted against a spec that
    no longer matches the current one. `run_all` raises rather than
    silently refusing (MODULES.md: "impossible by construction")."""


@dataclass
class _StoredToken:
    experiment_id: uuid.UUID
    spec_hash: str
    expires_at: datetime
    used: bool = False


_tokens: dict[str, _StoredToken] = {}

# experiment_id -> (container, run_id) for the one in-flight run this
# experiment may have. In-process only, per this module's docstring.
_running_containers: dict[uuid.UUID, tuple["docker.models.containers.Container", uuid.UUID]] = {}


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
    the whole vault (Rules.md)."""
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


async def load_run_spec(session: AsyncSession, experiment_id: uuid.UUID) -> tuple[Experiment, RunSpec]:
    """Fetches the experiment and builds its current `RunSpec` fresh — the
    one function both the approval-preview route and `run_all` call, so the
    spec a human reviews and the spec a token is validated against are
    always built the same way from the same inputs."""
    experiment = await experiments.get_experiment(session, experiment_id)
    if experiment is None:
        raise ValueError(f"experiment {experiment_id} not found")
    project = await session.get(Project, experiment.project_id)
    if project is None:
        raise ValueError(f"project {experiment.project_id} not found")
    return experiment, build_run_spec(experiment, project.slug)


def _hash_spec(spec: RunSpec) -> str:
    return hashlib.sha256(spec.model_dump_json().encode("utf-8")).hexdigest()


def mint_confirmation(experiment_id: uuid.UUID, spec: RunSpec) -> ConfirmationToken:
    """Issues a one-shot confirmation token for exactly this `spec` — the
    caller (the API route, on behalf of the UI) must already have built it
    and be about to show it to the human (D31); nothing here re-derives
    the spec or trusts one built elsewhere.
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_TOKEN_TTL_SECONDS)
    _tokens[token] = _StoredToken(experiment_id=experiment_id, spec_hash=_hash_spec(spec), expires_at=expires_at)
    return ConfirmationToken(token=token, experiment_id=experiment_id, expires_at=expires_at)


def _consume_token(experiment_id: uuid.UUID, token: ConfirmationToken | str, spec: RunSpec) -> None:
    raw = token.token if isinstance(token, ConfirmationToken) else token
    stored = _tokens.get(raw)
    if stored is None:
        raise ConfirmationError("confirmation token not found")
    if stored.used:
        raise ConfirmationError("confirmation token has already been used")
    if datetime.now(timezone.utc) > stored.expires_at:
        raise ConfirmationError("confirmation token has expired")
    if stored.experiment_id != experiment_id:
        raise ConfirmationError("confirmation token was not minted for this experiment")
    if stored.spec_hash != _hash_spec(spec):
        raise ConfirmationError("confirmation token does not match the experiment's current run spec")
    stored.used = True  # one-shot: no replay, matched or not


async def _stream_container_logs(container) -> AsyncIterator[bytes]:
    """Bridges the Docker SDK's blocking log generator (a background
    thread) to an async iterator of raw lines, without polling."""
    line_queue: "queue.Queue[bytes | None]" = queue.Queue()

    def _pump() -> None:
        try:
            for chunk in container.logs(stream=True, follow=True):
                line_queue.put(chunk)
        finally:
            line_queue.put(None)

    pump_task = asyncio.create_task(asyncio.to_thread(_pump))
    try:
        while True:
            chunk = await asyncio.to_thread(line_queue.get)
            if chunk is None:
                break
            for line in chunk.splitlines(keepends=True):
                yield line
    finally:
        await pump_task


def _wait_for_exit(container) -> int:
    try:
        result = container.wait(timeout=30)
        return int(result.get("StatusCode", -1))
    except Exception:
        container.reload()
        return int(container.attrs.get("State", {}).get("ExitCode", -1))


async def run_all(
    session: AsyncSession,
    experiment_id: uuid.UUID,
    token: ConfirmationToken | str,
    network_optin: bool = False,
    gpu: bool = False,
    run_id: uuid.UUID | None = None,
) -> ExperimentRun:
    """The only path to a real container execution (D31). Validates the
    confirmation token, recomputes the `RunSpec` fresh (never trusts a
    stale one a caller might still hold), then runs the whole notebook top
    to bottom via `nbclient` inside a one-shot `docker run` — the fallback
    this module ships per D30's kernel-transport spike descope. Always
    `run_kind='clean_run_all'`.

    `network_optin`/`gpu` are this call's own explicit runtime flags,
    deliberately separate from `experiment.network_optin`/`gpu_optin`
    baked into the approved `RunSpec`: both the experiment-level opt-in
    (what the human saw and approved) and this call's own flag must agree
    for network or GPU to actually be enabled — a mismatch just runs more
    restricted than requested (offline / CPU-only), never more permissive,
    which is always the safe direction to fail in.

    `run_id` lets a caller (the `run_experiment_job` Job Queue wrapper)
    hand a stable id back to an HTTP response before the run finishes; a
    direct caller with no such need gets a fresh one.

    Raises `ConfirmationError` for any token mismatch, `ValueError` if the
    experiment or its notebook doesn't exist, and `RuntimeError` if this
    experiment already has a run in flight.
    """
    if experiment_id in _running_containers:
        raise RuntimeError(f"a run is already in progress for experiment {experiment_id}")

    experiment, spec = await load_run_spec(session, experiment_id)
    project = await session.get(Project, experiment.project_id)
    _consume_token(experiment_id, token, spec)
    approved_at = datetime.now(timezone.utc)

    exp_dir = get_vault_path() / "projects" / project.slug / "experiments" / experiment.slug
    notebook_path = exp_dir / "notebook.ipynb"
    requirements_path = exp_dir / "requirements.txt"
    if not notebook_path.exists():
        raise ValueError(f"experiment {experiment_id} has no notebook to run")

    outputs_dir = exp_dir / _OUTPUTS_DIRNAME
    outputs_dir.mkdir(parents=True, exist_ok=True)
    existing_outputs = {path for path in outputs_dir.rglob("*") if path.is_file()}

    run_id = run_id or uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    reqs_bytes = requirements_path.read_bytes() if requirements_path.exists() else b""
    reqs_hash = hashlib.sha256(reqs_bytes).hexdigest()
    notebook_hash = hashlib.sha256(notebook_path.read_bytes()).hexdigest()

    docker_client = await asyncio.to_thread(_get_docker_client)
    image = await asyncio.to_thread(docker_client.images.get, spec.image)
    image_digest = image.id

    network_enabled = network_optin and spec.network == "bridge"
    gpu_enabled = gpu and spec.gpu

    volumes = {
        str(get_vault_path() / mount.host_path): {"bind": mount.container_path, "mode": mount.mode}
        for mount in spec.mounts
    }
    device_requests = [docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])] if gpu_enabled else None

    container = await asyncio.to_thread(
        docker_client.containers.run,
        spec.image,
        command=[
            "python",
            _RUNNER_SCRIPT_PATH,
            "/workspace/notebook.ipynb",
            str(spec.cell_timeout_seconds),
            "1" if network_enabled else "0",
        ],
        volumes=volumes,
        network_mode="bridge" if network_enabled else "none",
        nano_cpus=int(spec.cpu_limit * 1_000_000_000),
        mem_limit=f"{spec.memory_limit_mb}m",
        device_requests=device_requests,
        working_dir="/workspace",
        detach=True,
    )
    _running_containers[experiment_id] = (container, run_id)

    ws_session = ws.get_session(experiment.project_id)
    if ws_session is not None:
        await ws.broadcast(ws_session, RunStatusEvent(experiment_id=experiment_id, run_id=run_id, status="running"))

    stdout_chunks: list[bytes] = []
    try:
        async with asyncio.timeout(spec.idle_timeout_seconds):
            async for line in _stream_container_logs(container):
                stdout_chunks.append(line)
                if ws_session is not None:
                    await ws.broadcast(
                        ws_session,
                        RunLogEvent(
                            experiment_id=experiment_id, run_id=run_id, line=line.decode("utf-8", errors="replace")
                        ),
                    )
    except TimeoutError:
        # The one-shot run exceeded its overall wall-clock budget
        # (`idle_timeout_seconds` repurposed here as this run's total time
        # limit, since there is no interactive idle kernel to time out
        # under this fallback) — kill it; the exit code below reflects the
        # kill, which correctly keeps this run out of `measured` promotion.
        await asyncio.to_thread(container.kill)

    try:
        exit_code = await asyncio.to_thread(_wait_for_exit, container)
    finally:
        # One-shot per run (D30) — the image is the reusable artifact, not
        # the container; leaving stopped containers around would pile one
        # up per run indefinitely.
        await asyncio.to_thread(container.remove, force=True)
        _running_containers.pop(experiment_id, None)
    finished_at = datetime.now(timezone.utc)

    executed_notebook_bytes = notebook_path.read_bytes()  # runner wrote it back in place via the rw mount
    stdout_bytes = b"".join(stdout_chunks)

    new_outputs = sorted(path for path in outputs_dir.rglob("*") if path.is_file() and path not in existing_outputs)
    artifact_files = [
        RunArtifactFile(
            path=str(path.relative_to(get_vault_path())),
            kind=path.suffix.lstrip(".") or "bin",
            bytes=path.stat().st_size,
        )
        for path in new_outputs
    ]

    written = await vault.write_experiment_files(
        session,
        experiment_id,
        executed_notebook_bytes,
        run=RunArtifacts(run_id=run_id, stdout=stdout_bytes, artifacts=artifact_files),
    )
    assert written.stdout_ref is not None  # a `run` was always passed above

    row = ExperimentRuns(
        id=run_id,
        experiment_id=experiment_id,
        started_at=started_at,
        finished_at=finished_at,
        exit_code=exit_code,
        image=image_digest,
        reqs_hash=reqs_hash,
        notebook_hash=notebook_hash,
        stdout_ref=written.stdout_ref,
        artifacts=[artifact.model_dump() for artifact in artifact_files],
        run_kind="clean_run_all",
        network_enabled=network_enabled,
        gpu_enabled=gpu_enabled,
        approved_at=approved_at,
    )
    session.add(row)
    await session.flush()

    if ws_session is not None:
        await ws.broadcast(
            ws_session,
            RunStatusEvent(
                experiment_id=experiment_id,
                run_id=run_id,
                status="done" if exit_code == 0 else "failed",
                exit_code=exit_code,
            ),
        )

    return ExperimentRun.from_row(row)


async def run_experiment_job(
    _ctx: dict, *, experiment_id: str, run_id: str, token: str, network_optin: bool, gpu: bool
) -> None:
    """Registered Job Queue function — dispatched off the request path
    (`POST .../run_all` enqueues this rather than awaiting `run_all`
    directly), so the HTTP response returns `run_id` immediately while the
    container executes. `run_all` remains directly callable too (used by
    this module's own manual end-to-end verification)."""
    async with db.session() as db_session:
        await run_all(
            db_session,
            uuid.UUID(experiment_id),
            token,
            network_optin=network_optin,
            gpu=gpu,
            run_id=uuid.UUID(run_id),
        )


def kernel_status(experiment_id: uuid.UUID) -> KernelStatus:
    """The only real "kernel state" under this execution model: whether an
    in-flight run container exists for this experiment."""
    entry = _running_containers.get(experiment_id)
    if entry is None:
        return KernelStatus(experiment_id=experiment_id, state="idle")
    _container, run_id = entry
    return KernelStatus(experiment_id=experiment_id, state="running", run_id=run_id)


async def stop_kernel(experiment_id: uuid.UUID) -> None:
    """There is no persistent kernel to stop under the `nbclient` fallback
    (D30's descope) — this is realistically "cancel the in-flight run
    container for this experiment, if any." Idempotent: no run in flight is
    a no-op, not an error. The `run_all` coroutine that owns the container
    observes the kill through its own log/wait loop and finishes its normal
    bookkeeping itself — the run is still recorded, with whatever non-zero
    exit code the kill produced, which is already the correct "never
    promoted to measured" outcome for a cancelled run.
    """
    entry = _running_containers.get(experiment_id)
    if entry is None:
        return
    container, _run_id = entry
    await asyncio.to_thread(container.kill)
