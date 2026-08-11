# Execution Sandbox — Design & Architecture

## TLDR

`backend/sandbox/` runs experiment notebooks in isolated Docker containers, only after an
explicit human confirmation step, and separately hosts a live embedded Jupyter server per
experiment. There are two mutually-exclusive execution modes per experiment
(`__init__.py:57-61`): a one-shot **measured run** (`run_all`) — a fresh container per run,
`nbclient` executing the notebook top to bottom inside it, always `run_kind='clean_run_all'`
(`__init__.py:10-11`) — and a **live notebook server** (`start_notebook_server`) — a long-lived
container running real Jupyter Notebook 7, published on a loopback TCP port and embedded in an
iframe. A run can only start after a caller mints a one-shot `ConfirmationToken`
(`mint_confirmation`) tied to a hash of the exact `RunSpec` shown to the human, and `run_all`
re-derives and re-hashes that spec before consuming the token (`_consume_token`,
`__init__.py:341-354`), so an approval can't be replayed against a spec that changed underneath
it. All in-flight state — tokens, running containers, live server handles — lives in
in-process dicts, not the database (`__init__.py:13-18`, `217-248`); orphan sweeping at boot and
save-then-verify teardown on stop are the mechanisms that keep this safe across sidecar restarts.

## Storage / data model

Sandbox itself owns no database tables — it borrows `experiments`' `ExperimentRun`/`RunResult`
(`experiments/models.py`, re-exported as `sandbox.models.ExperimentRun`, `models.py:10,14`) as the
row that `run_all` ultimately writes via `experiments.record_run` (`__init__.py:556`). All of
sandbox's own state is process memory, not persisted:

- `_tokens: dict[str, _StoredToken]` (`__init__.py:217`) — one-shot confirmation tokens.
  `_StoredToken` (`__init__.py:209-214`) holds `experiment_id`, `spec_hash`, `expires_at`, `used`.
- `_running_containers: dict[uuid.UUID, tuple[Container, run_id]]` (`__init__.py:221`) — at most
  one in-flight measured-run container per experiment.
- `_live_servers: dict[uuid.UUID, _LiveServerHandle]` (`__init__.py:239`) — at most one live
  Jupyter server per experiment. `_LiveServerHandle` (`__init__.py:225-232`) holds container,
  project_id, port, network, url, started_at, and its own `ceiling_task`.
- `_live_server_locks: dict[uuid.UUID, asyncio.Lock]` (`__init__.py:248`) — per-experiment lock
  serializing start/stop, added to close a real race caught live via Playwright (comment,
  `__init__.py:241-247`).

Wire-shape models (`models.py`): `RunSpec` (container spec: image, mounts, network, cpu/memory
limits, timeouts, gpu flag), `MountSpec` (host_path/container_path/mode, "never the whole vault"
per `__init__.py:290-291`), `ConfirmationToken`, `KernelStatus`, `NotebookServerStatus`,
`NotebookServerAction`, plus two WebSocket event models (`RunLogEvent`, `RunStatusEvent`,
`NotebookServerStoppedEvent`) deliberately kept out of `harness.models.TurnEvent`'s closed union
(`__init__.py:20-33`).

## Core mechanics

**1. Cell proposal.** `propose_cell` (`__init__.py:259-285`) reads (or creates) the experiment's
notebook, appends/inserts a fresh `nbformat` code cell (no `execution_count`, no `outputs` — the
UI's "unrun, pending approval" signal, `models.py:29-32`), writes it back through
`vault.write_experiment_files`. Never executes anything.

**2. Approval gate.** `build_run_spec`/`load_run_spec` (`__init__.py:288-322`) construct the
`RunSpec` fresh from the experiment + project every time — mounts exactly
`experiments/<exp>/` read-write and `library/` read-only (`__init__.py:294-301`), network
`"bridge"` only if `experiment.network_optin`, GPU only if `experiment.gpu_optin`. CPU/memory/
timeout limits are fixed module constants (`_CPU_LIMIT=2.0`, `_MEMORY_LIMIT_MB=4096`,
`_IDLE_TIMEOUT_SECONDS=600`, `_CELL_TIMEOUT_SECONDS=120`, `__init__.py:132-135`) — no
per-experiment override exists. `mint_confirmation` (`__init__.py:329-338`) hashes that spec
(`_hash_spec`, sha256 of `model_dump_json()`) and stores a `_StoredToken` keyed by a
`secrets.token_urlsafe(32)` token, TTL 300s (`_TOKEN_TTL_SECONDS`). `_consume_token`
(`__init__.py:341-354`) checks: token exists, not used, not expired, minted for this
experiment_id, and its stored `spec_hash` matches a freshly recomputed hash of the *current*
spec — any mismatch raises `ConfirmationError`; success flips `used=True` (one-shot, no replay).

**3. `run_all` execution** (`__init__.py:390-569`):
   - Rejects if the experiment already has an in-flight run (`_running_containers`) or a live
     notebook server open (`_live_servers`) — the two modes are mutually exclusive since both
     would touch the same mounted notebook file (`__init__.py:423-428`).
   - Recomputes spec, consumes the token, records `approved_at`.
   - Confirms `notebook.ipynb` exists on the vault path; snapshots existing files under
     `outputs/` before the run so new artifacts can be diffed after.
   - Hashes `requirements.txt` and the notebook file; resolves the pinned image's local digest
     (`image.id`) — recorded per run since the image is only tagged, never pushed to a registry
     (`__init__.py:124-127`).
   - `network_optin`/`gpu` passed to the call must *both* agree with the approved spec's own
     `network`/`gpu` flags for either to actually enable — any mismatch runs more restricted,
     never more permissive (`__init__.py:406-412,456-457`).
   - Launches one `docker run` (detached) executing `/opt/run_notebook.py <notebook> <cell_timeout>
     <network_flag>` inside the pinned base image, with the built volumes/CPU/memory/GPU
     device-request settings.
   - Registers `(container, run_id)` in `_running_containers`, broadcasts `RunStatusEvent(status=
     "running")` over the project's WebSocket session if one exists.
   - Streams container stdout via `_stream_container_logs` (a background thread pumping the
     Docker SDK's blocking log generator into an `asyncio.Queue`-free byte iterator,
     `__init__.py:357-378`), broadcasting each line as `RunLogEvent`, all bounded by an overall
     `asyncio.timeout(idle_timeout_seconds)` — on timeout the container is killed
     (`__init__.py:501-507`).
   - Waits for exit code (`_wait_for_exit`, `container.wait(timeout=30)` falling back to
     `container.reload()`+`attrs`), then always force-removes the container in a `finally`
     (one-shot per run — no accumulating stopped containers, `__init__.py:509-516`).
   - Reads back the executed notebook (runner wrote it in place via the rw mount), diffs
     `outputs/` for new files, builds `RunArtifactFile` entries, writes notebook + stdout +
     artifacts through `vault.write_experiment_files(... run=RunArtifacts(...))`.
   - Builds a `RunResult` (exit code, image digest, reqs/notebook hashes, stdout ref, artifacts,
     `run_kind="clean_run_all"`, network/gpu enabled flags, `approved_at`) and calls
     `experiments.record_run` to persist it.
   - Broadcasts final `RunStatusEvent(status="done"|"failed", exit_code=...)`.
   - `run_experiment_job` (`__init__.py:572-588`) is the Job Queue wrapper: opens a DB session,
     calls `run_all` with a pre-assigned `run_id` so the HTTP layer can return immediately.

**4. Kernel status / cancel.** `kernel_status` (`__init__.py:591-598`) reports "running" only if
`_running_containers` has an entry for the experiment — there is no persistent kernel under this
`nbclient` fallback (D30 descope), so "start" has nothing real to do. `stop_kernel`
(`__init__.py:601-615`) is idempotent: no-op if nothing in flight, else `container.kill()`; the
owning `run_all` coroutine observes the kill through its own log/wait loop and finishes its
normal bookkeeping (still records the run, non-zero exit code).

**5. Live notebook server lifecycle.**
   - `start_notebook_server` (`__init__.py:724-787`), serialized per-experiment via
     `_get_live_server_lock`: idempotent (returns existing status if already running), rejects
     if a measured run is in flight, creates an empty notebook if none exists yet, picks a free
     loopback port (`_pick_free_port`, bind-read-release, accepted TOCTOU risk documented
     `__init__.py:618-625`), then `_run_notebook_server_container` (`__init__.py:692-721`)
     launches Jupyter first on the isolated `research-os-experiment-internal` network (created if
     missing) and checks `_tcp_reachable`; if unreachable, removes that container and retries
     exactly once on the default `bridge` network (confirmed necessary in this project's own dev
     sandbox by a live spike). `_wait_for_http_ready` (`__init__.py:638-665`) then polls with a
     real HTTP GET (not just TCP) until Tornado actually answers — a bare TCP accept was found
     live to precede real readiness. On success registers a `_LiveServerHandle`, starts an
     `_enforce_ceiling` background task, returns `NotebookServerStatus(state="running", url=...)`.
   - `notebook_server_status` (`__init__.py:888-901`) is a pure lookup against `_live_servers`.
   - `_save_and_verify_notebook` (`__init__.py:790-844`): forces Jupyter to write via its own
     `GET`/`PUT` `/api/contents/<path>` REST round trip, then polls the vault file's mtime up to
     `_SAVE_VERIFY_ATTEMPTS=5` times at `_SAVE_VERIFY_INTERVAL_S=0.5s` to confirm the write
     landed on the bind mount; raises `NotebookSaveError` if it never lands. If the container has
     already died, falls back to whatever's already on disk best-effort rather than blocking.
   - `stop_notebook_server` (`__init__.py:847-885`), also lock-serialized: idempotent (returns
     "stopped" if no handle); else forces the save-and-verify, writes the confirmed bytes through
     Vault Writer, pops the handle, cancels the ceiling task (unless the caller *is* the ceiling
     task itself — avoids self-cancelling mid-teardown, `__init__.py:871-878`), force-removes the
     container, and broadcasts `NotebookServerStoppedEvent`. If save can't be confirmed,
     `NotebookSaveError` propagates and the container is left running/registered.
   - `_enforce_ceiling` (`__init__.py:924-944`) is a hard 4h safety net
     (`_LIVE_SERVER_CEILING_SECONDS`, not real activity tracking) — sleeps, then routes through
     the same guarded `stop_notebook_server(reason="ceiling")`.
   - `stop_all_notebook_servers_for_shutdown` (`__init__.py:904-921`) — best-effort guarded stop
     of every live server, logging (not raising) on `NotebookSaveError`.
   - `sweep_orphaned_notebook_servers` (`__init__.py:947-960`) — one-shot boot-time sweep,
     force-removes any container carrying the `research-os.kind=notebook-server` label, catching
     containers orphaned by an unclean prior shutdown.

## Callers & dependents

All call paths found are live:

- `backend/api/experiments.py` — the only HTTP surface onto sandbox. Routes call
  `sandbox.load_run_spec` (`run_spec` preview, `confirmation`), `sandbox.propose_cell` (`cells`
  POST), `sandbox.mint_confirmation` (`confirmation` POST), `sandbox.kernel_status`/`stop_kernel`
  (`kernel` POST), `sandbox.notebook_server_status`/`start_notebook_server`/
  `stop_notebook_server` (`notebook_server` GET/POST, `NotebookSaveError`→503, mutual-exclusion
  `RuntimeError`→409), and dispatches `run_experiment_job` via `jobs.enqueue` from the `run_all`
  POST route (`experiments.py:196-204`) after a synchronous pre-check
  (`sandbox.notebook_server_status(...).state != "stopped"` → 409) that duplicates the check
  `run_all` itself makes, deliberately, because the job's own check runs too late for this
  response (`experiments.py:180-185`).
- `backend/jobs/__init__.py:85,94` — imports and registers `run_experiment_job` as one of the Job
  Queue's dispatchable job kinds (list at `jobs/__init__.py:87-96`); this is the only place
  `run_all` actually executes for a real run (fired by the enqueue in `experiments.py`).
- `backend/main.py:26,124,152` — sidecar lifespan: `sandbox.sweep_orphaned_notebook_servers()`
  called once at startup after Docker readiness is confirmed (`main.py:124`), and
  `sandbox.stop_all_notebook_servers_for_shutdown()` called once at shutdown (`main.py:152`) —
  both in the live FastAPI `lifespan` context manager, not dead code.
- `backend/ws/__init__.py` — no import of `sandbox`, but its own docstring documents that
  `sandbox` broadcasts `RunLogEvent`/`RunStatusEvent` through `ws.get_session`/`ws.broadcast`,
  which `__init__.py:485-500,558-567` confirms — `ws.broadcast`'s signature was widened to accept
  any event model, not only `TurnEvent`, specifically for this caller.
- `backend/experiments/models.py:14,111` — no runtime import; a comment documents that
  `ExperimentRun`'s `run_kind` enum is fixed rather than re-derived from `sandbox`'s
  `RunSpec`/`ExperimentRun`, and that sandbox re-exports `experiments.models.ExperimentRun` as
  its own model.
- `backend/api/runs.py` — only a comment reference ("bounded by the sandbox's own cell/idle
  timeouts"), no import, no call.

Nothing found in this trace was dead code or an unreachable stub — every public function in
`sandbox/__init__.py` has a live caller reachable from an HTTP route, a registered job, or the
FastAPI lifespan.

## Open questions / rough edges

- **No per-experiment resource override.** `_CPU_LIMIT`, `_MEMORY_LIMIT_MB`,
  `_IDLE_TIMEOUT_SECONDS`, `_CELL_TIMEOUT_SECONDS` are hard module constants
  (`__init__.py:130-135`) — every experiment gets the same container ceiling regardless of
  workload; the module comment frames this as "nothing to vary yet" rather than a deliberate cap.
- **`_pick_free_port`'s TOCTOU race is explicitly unaddressed** (`__init__.py:618-625`) — the
  gap between releasing the ephemeral port and `docker run` publishing it could be claimed by
  another process; accepted as-is, same risk profile as an earlier spike, not re-solved.
- **In-process-only state everywhere.** Tokens, running containers, live server handles, and
  their locks are all plain module-level dicts (`__init__.py:217,221,239,248`) — a sidecar
  restart silently orphans any in-flight run or live server; recovery relies entirely on
  `sweep_orphaned_notebook_servers` (containers only — a mid-restart measured run has no
  equivalent recovery path documented in this module beyond simply losing its
  `_running_containers` entry, since Docker itself keeps running orphaned containers that this
  sweep never targets by label).
- **`idle_timeout_seconds` is overloaded.** For `run_all` it's repurposed as the run's *entire*
  wall-clock budget rather than a true idle timeout, since there's no interactive kernel to sit
  idle (comment at `__init__.py:501-506`) — the field name in `RunSpec` doesn't reflect this dual
  meaning between the two execution modes.
- **`_wait_for_exit`'s 30-second `container.wait` timeout** (`__init__.py:381-387`) is shorter
  than the surrounding `idle_timeout_seconds` (600s) that already bounded the log-streaming loop
  — if the container hangs after logs stop but before exiting, this falls back to reading
  `ExitCode` from `container.attrs`, which may still reflect a container that hasn't actually
  exited; no further wait or retry is attempted.
- **Live-server "internal" network fallback is exactly one retry**, hardcoded to `bridge`
  (`__init__.py:692-721`) — if `bridge` also fails to become reachable, `start_notebook_server`
  raises and the caller gets a bare `RuntimeError`; there's no distinguishing signal in
  `NotebookServerStatus` for "network fallback occurred" vs. a clean start on the isolated
  network, so nothing downstream can tell after the fact which isolation level a running server
  actually got.
- **A mid-restart live server's ceiling task is lost.** `_enforce_ceiling`'s 4h safety net is an
  `asyncio.Task` (`__init__.py:775`); if the sidecar restarts, that task is gone along with the
  rest of `_live_servers`, and the orphaned container can only be caught by
  `sweep_orphaned_notebook_servers` at next boot — there's no persisted deadline, so a very long
  gap between restart and next boot leaves the container running unbounded in the interim.
