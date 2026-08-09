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

**Phase 2.4 — live embedded notebook server, un-descoping D30's original
interactive-kernel path.** The measured path above (`propose_cell`,
`mint_confirmation`, `run_all`) is unchanged by this addition — it remains
the only way to produce a `source: measured` metric. Separately,
`start_notebook_server`/`stop_notebook_server`/`notebook_server_status`
run a **long-lived, per-experiment container** carrying a real Jupyter
server (Jupyter Notebook 7, baked into the same base image), reachable from
the host over a published loopback TCP port and embedded by the frontend in
an `<iframe>`. This is not raw `jupyter_client`/ZMQ (the transport the
original kernel-transport spike found incompatible with `--network none`
inside this dev sandbox) — it's Jupyter's own HTTP/WebSocket protocol over
plain TCP port publishing, the one half of that spike that fully worked.
See `docker/jupyter_server_config.py` for why Jupyter's own CSP/XSRF/token
auth are disabled for this container (proven necessary by a live spike, not
assumed) and why that's an acceptable trade-off given loopback-only
publishing + an ephemeral per-container port.

A `KernelStatus`/live-server invariant this module enforces both ways: an
experiment may have an in-flight measured run (`_running_containers`) or a
live notebook server (`_live_servers`), never both — no auto-stop-and-switch
magic, just a clear rejection either direction (see `run_all` and
`start_notebook_server`).

**Phase 6.7 — a notebook survives navigating away.** The live server used to
be torn down whenever the frontend's panel unmounted (collapsing the card,
switching tabs), discarding anything Jupyter had not yet autosaved to the
bind-mounted `notebook.ipynb` (up to its own ~2 minute autosave window). The
frontend no longer stops the server on unmount at all — only the explicit
`Stop notebook` action and `_enforce_ceiling`'s 4h safety net do, and both
now route through `_save_and_verify_notebook`: force a save through Jupyter's
own `/api/contents/<path>` REST API (GET the server's current model, PUT it
straight back — the one shape that's actually documented and already
exercised by this module's own `_wait_for_http_ready` HTTP calls; there is no
way for a server-side call to reach into the iframe's own unsaved JS editor
buffer, so this closes the narrower, still-real race where Jupyter's *last
accepted* save has not yet reached the bind mount, not the case where the
browser tab itself is killed with edits never sent to the server at all),
then polls the vault file's mtime — bounded retries, not an open-ended watch
— to confirm the write actually landed before the container is removed. A
save that cannot be confirmed raises `NotebookSaveError` and the container is
left running rather than removed (Rules.md: never catch-log-rethrow into a
silent proceed that would reintroduce this phase's own bug).
"""

import asyncio
import hashlib
import logging
import queue
import secrets
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import docker
import httpx
import nbformat
from nbformat.v4 import new_code_cell, new_notebook
from sqlalchemy.ext.asyncio import AsyncSession

import db
import experiments
import vault
import ws
from db.models import Project
from experiments.models import Experiment, ExperimentRun, RunResult
from sandbox.models import (
    ConfirmationToken,
    KernelStatus,
    MountSpec,
    Notebook,
    NotebookServerStatus,
    NotebookServerStoppedEvent,
    RunLogEvent,
    RunSpec,
    RunStatusEvent,
)
from settings import get_vault_path
from vault.models import RunArtifactFile, RunArtifacts

logger = logging.getLogger(__name__)

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

# Phase 2.4 — live notebook server constants.
_JUPYTER_CONFIG_PATH = "/opt/jupyter_server_config.py"
_JUPYTER_CONTAINER_PORT = 8888
_JUPYTER_NOTEBOOK_FILENAME = "notebook.ipynb"
# A hard ceiling only — not real activity tracking (out of scope for v1);
# revisit if this proves too short/long in practice.
_LIVE_SERVER_CEILING_SECONDS = 4 * 3600
# Verified by spike: `internal` custom bridge networks reliably block
# published-port reachability in this project's own dev sandbox (Docker
# Desktop's linuxkit VM) — a real, reproduced finding, not a guess. A native
# Linux dockerd may not have this limitation, so the isolated network is
# still tried first; the fallback below is what actually runs here.
_EXPERIMENT_INTERNAL_NETWORK = "research-os-experiment-internal"
_NOTEBOOK_SERVER_LABEL_KEY = "research-os.kind"
_NOTEBOOK_SERVER_LABEL_VALUE = "notebook-server"
_NETWORK_SELFCHECK_TIMEOUT_S = 2.0

# Phase 6.7 — bounded save-then-verify before any stop path removes a live
# server's container. Not a watcher: a fixed number of short polls, then give
# up and refuse to remove the container (Rules.md: no open-ended watch loop).
_SAVE_HTTP_TIMEOUT_S = 5.0
_SAVE_VERIFY_ATTEMPTS = 5
_SAVE_VERIFY_INTERVAL_S = 0.5

# A bare `new_notebook()` has no kernelspec, which makes Jupyter Notebook 7
# prompt "Select Kernel" on first open instead of just starting one — real
# friction confirmed live, not a guess. Every fresh notebook this module
# creates gets this metadata so that dialog never appears.
_DEFAULT_NOTEBOOK_METADATA = {
    "kernelspec": {"name": "python3", "display_name": "Python 3 (ipykernel)", "language": "python"},
    "language_info": {"name": "python"},
}

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


class NotebookSaveError(RuntimeError):
    """A live notebook server's contents could not be confirmed saved to the
    vault before a stop path would otherwise remove its container (Phase
    6.7). Callers must leave the container running and the server registered
    in `_live_servers` when this is raised — the caller can retry `Stop`, or
    (for `_enforce_ceiling`) the failure is logged and the container simply
    stays up past the ceiling rather than being destroyed with unsaved work."""


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


@dataclass
class _LiveServerHandle:
    container: "docker.models.containers.Container"
    project_id: uuid.UUID
    port: int
    network: str
    url: str
    started_at: datetime
    ceiling_task: "asyncio.Task[None]"


# experiment_id -> its one live notebook-server container. In-process only,
# same reasoning as `_tokens`/`_running_containers` — a sidecar restart
# orphans the container; the startup sweep (`sweep_orphaned_notebook_servers`)
# and the ceiling task are the cleanup mechanisms, not persistence.
_live_servers: dict[uuid.UUID, _LiveServerHandle] = {}

# Serializes start/stop for one experiment's live server. Without this, an
# overlapping start (mount) and stop (cleanup from the immediately-preceding
# unmount — e.g. React StrictMode's dev-only double-invoke, or a fast tab
# switch away-and-back) can interleave: `stop`'s pop from `_live_servers`
# happens before its `await container.remove(...)` completes, so a `start`
# landing in that gap sees no existing entry and creates a second container.
# Caught live via Playwright testing, not theoretical.
_live_server_locks: dict[uuid.UUID, asyncio.Lock] = {}


def _get_live_server_lock(experiment_id: uuid.UUID) -> asyncio.Lock:
    lock = _live_server_locks.get(experiment_id)
    if lock is None:
        lock = asyncio.Lock()
        _live_server_locks[experiment_id] = lock
    return lock


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
        notebook = new_notebook(metadata=_DEFAULT_NOTEBOOK_METADATA)

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
    experiment already has a run in flight (or a live notebook server open —
    the two execution modes are mutually exclusive per experiment, since
    both would read/write the same mounted notebook file at once).
    """
    if experiment_id in _running_containers:
        raise RuntimeError(f"a run is already in progress for experiment {experiment_id}")
    if experiment_id in _live_servers:
        raise RuntimeError(
            f"experiment {experiment_id} has a live notebook server open — stop it before running a measured pass"
        )

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

    run_result = RunResult(
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
    experiment_run = await experiments.record_run(session, experiment_id, run_result)

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

    return experiment_run


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


def _pick_free_port() -> int:
    """Binds an ephemeral port, reads it, releases it. The same technique
    this project's own kernel-transport spike used; the TOCTOU race (another
    process claims the port before `docker run` publishes it) is accepted
    as-is, same risk profile as that spike, not re-solved here."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _tcp_reachable(port: int, timeout: float = _NETWORK_SELFCHECK_TIMEOUT_S) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    await writer.wait_closed()
    return True


async def _wait_for_http_ready(port: int, timeout: float = 10.0) -> bool:
    """A bare TCP connect (`_tcp_reachable`) only proves the port is
    *listening* — Jupyter accepts the connection before its Tornado app has
    finished initializing enough to answer a real request, and a request
    that lands in that gap gets an empty response (confirmed live: the
    embedded iframe showed Chromium's `ERR_EMPTY_RESPONSE` page on the very
    first load). Polls with a real HTTP GET until one actually completes, so
    `start_notebook_server` never hands the frontend a URL before the server
    can truly serve it."""
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient() as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                response = await client.get(f"http://127.0.0.1:{port}/", timeout=1.0)
                response.raise_for_status()
                return True
            except httpx.HTTPError:
                # Covers both a connection-level failure (not listening yet)
                # and a non-2xx (Tornado accepted the connection before its
                # contents manager finished initializing — Bug Fix Plan
                # Phase 6.12: the earlier bare `client.get` with no status
                # check treated *any* response, including a transient
                # non-2xx here, as "ready", occasionally handing the
                # frontend a notebook URL the server couldn't yet actually
                # serve — the live iframe's own "File Load Error ...
                # Invalid response: 404 Not Found" dialog).
                await asyncio.sleep(0.2)
    return False


def _start_jupyter_container(
    docker_client: docker.DockerClient, spec: RunSpec, port: int, volumes: dict, network_mode: str
):
    return docker_client.containers.run(
        spec.image,
        command=[
            "jupyter",
            "notebook",
            f"--port={_JUPYTER_CONTAINER_PORT}",
            "--no-browser",
            "--allow-root",
            f"--config={_JUPYTER_CONFIG_PATH}",
        ],
        ports={f"{_JUPYTER_CONTAINER_PORT}/tcp": ("127.0.0.1", port)},
        volumes=volumes,
        network_mode=network_mode,
        nano_cpus=int(spec.cpu_limit * 1_000_000_000),
        mem_limit=f"{spec.memory_limit_mb}m",
        working_dir="/workspace",
        labels={_NOTEBOOK_SERVER_LABEL_KEY: _NOTEBOOK_SERVER_LABEL_VALUE},
        detach=True,
    )


async def _run_notebook_server_container(
    docker_client: docker.DockerClient, spec: RunSpec, port: int, volumes: dict
) -> tuple["docker.models.containers.Container", str]:
    """Tries the isolated `internal` network first (no outbound route once
    running, still reachable via the published loopback port on a native
    Linux dockerd); falls back exactly once to the default `bridge` network
    if the published port isn't actually reachable — confirmed necessary in
    this project's own dev sandbox by a live spike, not a theoretical case.
    Exactly one retry, always logged, never a silent choice (Rules.md: no
    speculative auto-negotiation loop)."""
    try:
        network = await asyncio.to_thread(docker_client.networks.get, _EXPERIMENT_INTERNAL_NETWORK)
    except docker.errors.NotFound:
        network = await asyncio.to_thread(
            docker_client.networks.create, _EXPERIMENT_INTERNAL_NETWORK, driver="bridge", internal=True
        )

    container = await asyncio.to_thread(_start_jupyter_container, docker_client, spec, port, volumes, network.name)
    if await _tcp_reachable(port):
        return container, "internal"

    logger.warning(
        "event=notebook_server_network_fallback reason=internal_network_port_unreachable port=%d", port
    )
    await asyncio.to_thread(container.remove, force=True)
    container = await asyncio.to_thread(_start_jupyter_container, docker_client, spec, port, volumes, "bridge")
    if not await _tcp_reachable(port, timeout=5.0):
        await asyncio.to_thread(container.remove, force=True)
        raise RuntimeError(f"notebook server on port {port} did not become reachable on either network mode")
    return container, "bridge"


async def start_notebook_server(session: AsyncSession, experiment_id: uuid.UUID) -> NotebookServerStatus:
    """Starts (or returns the status of an already-running) long-lived,
    per-experiment Jupyter server container — the live, interactive
    counterpart to the measured `run_all` path (see module docstring).
    Idempotent. Raises `RuntimeError` if a measured run is currently in
    flight for this experiment (mutual exclusion, see `run_all`).

    Serialized per experiment (`_get_live_server_lock`) so an overlapping
    start/stop pair — e.g. React StrictMode's dev-only double-mount, or a
    fast tab switch away-and-back — can't race and produce two containers
    for one experiment; see that lock's own comment for the concrete bug
    this closes.
    """
    async with _get_live_server_lock(experiment_id):
        existing = _live_servers.get(experiment_id)
        if existing is not None:
            return NotebookServerStatus(
                experiment_id=experiment_id,
                state="running",
                url=existing.url,
                port=existing.port,
                network=existing.network,
                started_at=existing.started_at,
            )
        if experiment_id in _running_containers:
            raise RuntimeError(
                f"experiment {experiment_id} has a measured run in progress — stop it before opening the live notebook"
            )

        experiment, spec = await load_run_spec(session, experiment_id)

        if experiment.notebook_path is None:
            empty_notebook = nbformat.writes(new_notebook(metadata=_DEFAULT_NOTEBOOK_METADATA), version=4).encode(
                "utf-8"
            )
            await vault.write_experiment_files(session, experiment_id, empty_notebook)

        docker_client = await asyncio.to_thread(_get_docker_client)
        port = _pick_free_port()
        volumes = {
            str(get_vault_path() / mount.host_path): {"bind": mount.container_path, "mode": mount.mode}
            for mount in spec.mounts
        }

        container, network_used = await _run_notebook_server_container(docker_client, spec, port, volumes)
        if not await _wait_for_http_ready(port):
            await asyncio.to_thread(container.remove, force=True)
            raise RuntimeError(f"notebook server on port {port} never became ready to serve HTTP requests")

        started_at = datetime.now(timezone.utc)
        url = f"http://127.0.0.1:{port}/notebooks/{_JUPYTER_NOTEBOOK_FILENAME}"
        ceiling_task = asyncio.create_task(_enforce_ceiling(experiment_id))
        _live_servers[experiment_id] = _LiveServerHandle(
            container=container,
            project_id=experiment.project_id,
            port=port,
            network=network_used,
            url=url,
            started_at=started_at,
            ceiling_task=ceiling_task,
        )
        return NotebookServerStatus(
            experiment_id=experiment_id, state="running", url=url, port=port, network=network_used, started_at=started_at
        )


async def _save_and_verify_notebook(handle: "_LiveServerHandle", experiment: Experiment) -> bytes:
    """Forces Jupyter to (re)write `notebook.ipynb` through its own contents
    REST API, then confirms — a bounded poll of the vault file's mtime, not
    an open-ended watch — that the write actually reached the bind-mounted
    host path. Returns the confirmed bytes for the caller to hand to Vault
    Writer. Raises `NotebookSaveError` if the container is alive but the
    save can't be confirmed within the bounded window.

    If the container has already died (crashed, or killed out of band)
    there is nothing left server-side to force a save from — skip the HTTP
    round trip and fall back to whatever is already on disk, best effort,
    rather than blocking a stop forever on a server that can never answer.
    """
    if experiment.notebook_path is None:
        raise NotebookSaveError(f"experiment {experiment.id} has no notebook_path to save")
    absolute_path = get_vault_path() / experiment.notebook_path

    await asyncio.to_thread(handle.container.reload)
    if handle.container.status != "running":
        logger.warning(
            "event=notebook_save_skipped_container_not_running experiment_id=%s status=%s",
            experiment.id,
            handle.container.status,
        )
        if not absolute_path.exists():
            raise NotebookSaveError(
                f"container for experiment {experiment.id} is not running and no vault notebook exists to save"
            )
        return absolute_path.read_bytes()

    before_mtime = absolute_path.stat().st_mtime_ns if absolute_path.exists() else None
    contents_url = f"http://127.0.0.1:{handle.port}/api/contents/{_JUPYTER_NOTEBOOK_FILENAME}"
    try:
        async with httpx.AsyncClient(timeout=_SAVE_HTTP_TIMEOUT_S) as client:
            get_response = await client.get(contents_url, params={"content": 1, "type": "notebook"})
            get_response.raise_for_status()
            model = get_response.json()
            put_response = await client.put(
                contents_url,
                json={"type": "notebook", "format": "json", "content": model["content"]},
            )
            put_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise NotebookSaveError(f"could not force-save notebook for experiment {experiment.id}: {exc}") from exc

    for _attempt in range(_SAVE_VERIFY_ATTEMPTS):
        if absolute_path.exists():
            after_mtime = absolute_path.stat().st_mtime_ns
            if before_mtime is None or after_mtime > before_mtime:
                return absolute_path.read_bytes()
        await asyncio.sleep(_SAVE_VERIFY_INTERVAL_S)

    raise NotebookSaveError(
        f"vault notebook for experiment {experiment.id} did not update within the save-verification window"
    )


async def stop_notebook_server(
    session: AsyncSession, experiment_id: uuid.UUID, reason: Literal["manual", "ceiling"] = "manual"
) -> NotebookServerStatus:
    """Idempotent teardown. Forces a save through Jupyter's own contents API
    and confirms it landed on the vault file (`_save_and_verify_notebook`,
    Phase 6.7) before the container is removed — never the other order. If
    the save can't be confirmed, `NotebookSaveError` propagates, the
    container stays registered and running, and nothing here removes it; the
    caller can retry `Stop notebook`.

    Serialized per experiment against `start_notebook_server` — see that
    function's docstring and `_get_live_server_lock`'s comment for why.
    """
    async with _get_live_server_lock(experiment_id):
        handle = _live_servers.get(experiment_id)
        if handle is None:
            return NotebookServerStatus(experiment_id=experiment_id, state="stopped")

        experiment = await experiments.get_experiment(session, experiment_id)
        if experiment is not None:
            notebook_bytes = await _save_and_verify_notebook(handle, experiment)
            await vault.write_experiment_files(session, experiment_id, notebook_bytes)

        _live_servers.pop(experiment_id, None)
        # A ceiling breach calls this function from inside its own
        # `ceiling_task` — self-cancelling would throw `CancelledError` into
        # this very call at its next `await` (the `container.remove` below),
        # aborting the teardown partway through. Cancel it from any other
        # caller (an explicit Stop, or shutdown) same as before.
        current_task = asyncio.current_task()
        if handle.ceiling_task is not current_task:
            handle.ceiling_task.cancel()
        await asyncio.to_thread(handle.container.remove, force=True)

        ws_session = ws.get_session(handle.project_id)
        if ws_session is not None:
            await ws.broadcast(ws_session, NotebookServerStoppedEvent(experiment_id=experiment_id, reason=reason))

        return NotebookServerStatus(experiment_id=experiment_id, state="stopped")


def notebook_server_status(experiment_id: uuid.UUID) -> NotebookServerStatus:
    """Pure lookup — lets the frontend re-fetch the URL after remounting
    (e.g. a tab switch back) without restarting anything."""
    handle = _live_servers.get(experiment_id)
    if handle is None:
        return NotebookServerStatus(experiment_id=experiment_id, state="stopped")
    return NotebookServerStatus(
        experiment_id=experiment_id,
        state="running",
        url=handle.url,
        port=handle.port,
        network=handle.network,
        started_at=handle.started_at,
    )


async def stop_all_notebook_servers_for_shutdown() -> None:
    """Called once from Sidecar Bootstrap's shutdown (Phase 6.7) — gives
    every still-live notebook server the same guarded, save-then-verify stop
    the explicit `Stop notebook` action and the 4h ceiling use, best effort.
    A server whose save can't be confirmed within that same bounded window is
    left running rather than force-removed; `sweep_orphaned_notebook_servers`
    force-removes it at next boot, same as an unclean process exit already
    risked before this phase — no worse, and never removed here without a
    confirmed save."""
    for experiment_id in list(_live_servers.keys()):
        try:
            async with db.session() as session:
                await stop_notebook_server(session, experiment_id, reason="manual")
        except NotebookSaveError:
            logger.error(
                "event=notebook_server_shutdown_save_failed experiment_id=%s — left running for next boot's sweep",
                experiment_id,
            )


async def _enforce_ceiling(experiment_id: uuid.UUID) -> None:
    """A hard safety-net timeout, not real activity tracking (explicitly out
    of scope for v1) — covers a renderer/tab left open indefinitely. Routes
    through the same guarded `stop_notebook_server` as the explicit `Stop`
    action (Phase 6.7): if the forced save can't be confirmed, this logs the
    failure and leaves the container running rather than destroying unsaved
    work just because the ceiling was reached (Rules.md: never catch-log-
    rethrow into a silent proceed)."""
    await asyncio.sleep(_LIVE_SERVER_CEILING_SECONDS)
    if experiment_id not in _live_servers:
        return
    logger.info("event=notebook_server_ceiling_reached experiment_id=%s", experiment_id)
    async with db.session() as ceiling_session:
        try:
            await stop_notebook_server(ceiling_session, experiment_id, reason="ceiling")
        except NotebookSaveError:
            logger.error(
                "event=notebook_server_ceiling_save_failed experiment_id=%s — container left running, "
                "vault notebook could not be confirmed saved",
                experiment_id,
            )


async def sweep_orphaned_notebook_servers() -> None:
    """Force-removes any notebook-server container left over from a sidecar
    restart that didn't shut down cleanly — a one-shot sweep at boot
    (called from Sidecar Bootstrap once Docker is confirmed ready), not
    continuous reconciliation (D4's spirit)."""
    docker_client = await asyncio.to_thread(_get_docker_client)
    containers = await asyncio.to_thread(
        docker_client.containers.list,
        all=True,
        filters={"label": f"{_NOTEBOOK_SERVER_LABEL_KEY}={_NOTEBOOK_SERVER_LABEL_VALUE}"},
    )
    for container in containers:
        logger.info("event=notebook_server_sweep_removed container_id=%s", container.id)
        await asyncio.to_thread(container.remove, force=True)
