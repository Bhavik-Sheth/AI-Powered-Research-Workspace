# Experiment Record — Design & Architecture

Experiment Record is 3 Postgres tables (`experiments`, `experiment_runs`, `experiment_metrics`) plus a thin async CRUD/gate layer (`backend/experiments/__init__.py`, `models.py`) — a lab notebook, not a run tracker. It owns the structured metadata about an experiment and the provenance of finished container runs; it never executes anything itself — `backend/sandbox/` does that and hands finished results back across the boundary.

---

## Storage / data model

Three tables, all defined in `backend/db/models.py:443-544`.

**`experiments`** (`db/models.py:443`) — the record itself:
- `id`, `project_id` (FK, `ON DELETE CASCADE`), `slug` (unique per `(project_id, slug)`), `title` (required), `hypothesis`, `setup` (JSONB, defaults `{}`), `notes`, `status` (CHECK: `planned|remaining|in-progress|done`, default `planned`), `notebook_path` (nullable — NULL until Vault Writer's `write_experiment_files` has run at least once), `network_optin`/`gpu_optin` (bools), `created_at`/`updated_at`.
- `__mapper_args__ = {"eager_defaults": True}` — asyncpg has no sync fallback to reload an `onupdate`-generated `updated_at`, so every PATCH needs it back immediately (`db/models.py:458-461`).

**`experiment_runs`** (`db/models.py:482`) — one row per container execution, "the strongest provenance in the system" (`experiments/models.py:107`):
- `id`, `experiment_id` (FK, CASCADE), `started_at`, `finished_at`, `exit_code`, `image`, `reqs_hash`, `notebook_hash`, `stdout_ref`, `artifacts` (JSONB list, default `[]`), `run_kind` (CHECK: `clean_run_all|interactive`), `network_enabled`, `gpu_enabled`, `approved_at` (**NOT NULL** — a row cannot exist without a validated confirmation token; this is the consent gate expressed at rest, `db/models.py:484-486`).

**`experiment_metrics`** (`db/models.py:514`) — one row per named value:
- `id`, `experiment_id` (FK, CASCADE), `name`, `value` (string, not numeric), `unit`, `source` (CHECK: `user|measured` — `llm` is structurally impossible, there's no third value to add), `run_id` (FK to `experiment_runs`, `ON DELETE RESTRICT`, nullable), `recorded_at`.
- Second CHECK: `source <> 'measured' OR run_id IS NOT NULL` (`db/models.py:527-529`) — this is the actual enforcement point for the "measured metrics need a run" rule, not any Python code.

**Pydantic wire models** (`backend/experiments/models.py`): `ExperimentInput` (all-optional patch shape), `Experiment`, `RunResult` (what Execution Sandbox hands back — carries `artifacts` as plain `list[dict]`, not `vault.models.RunArtifactFile`, so this module never imports Vault Writer's types), `ExperimentRun`, `MetricInput`, `ExperimentMetric`. Every `from_row` classmethod is a 1:1 field copy off the matching SQLAlchemy row — no derived fields, no joins folded in.

---

## Core mechanics

### Create / update path
- `create_experiment` (`experiments/__init__.py:66`): requires `fields.title`; looks up the `Project` row and raises `ValueError` if missing; derives a slug via `_unique_slug` (slugify the title, append `-2`, `-3`, ... until no `(project_id, slug)` collision exists, `experiments/__init__.py:52-63`); inserts the row with `status` defaulting to `"planned"` and `setup` defaulting to `{}`.
- `update_experiment` (`experiments/__init__.py:93`): patches only the fields present (non-`None`) on `ExperimentInput`, from a fixed field list (`title, hypothesis, setup, notes, status, network_optin, gpu_optin`). `slug` is never touched on update — only Vault Writer's `write_experiment_files` derives a vault path from it, and that path must stay stable.
- Neither function ever writes `notebook_path` — only `vault.write_experiment_files` (`backend/vault/__init__.py:381`) does that, after a notebook is actually persisted to the vault (`projects/<slug>/experiments/<exp-slug>/notebook.ipynb`, `vault/__init__.py:364`). So a freshly created experiment has `notebook_path = NULL` until something in `sandbox` (via `propose_cell` or a completed run) writes to the vault.

### Status
`status` is a plain string field on `experiments`, patched by whoever calls `update_experiment` — no state machine, no transition validation beyond the CHECK constraint's fixed value set. Nothing in `experiments/__init__.py` derives status from run outcomes; a caller (the harness tool, or the future UI) sets it directly.

### Run provenance path (the sandbox boundary)
`experiments/` never touches Docker, containers, or execution. The boundary (per the module's own docstrings, `experiments/__init__.py:1-19`, `experiments/models.py:78-88`):
- `sandbox.run_all` (`sandbox/__init__.py:390`) does the actual container run — validates a confirmation token, builds/recomputes the `RunSpec`, executes the notebook, hashes the image/reqs/notebook, captures stdout and artifacts, writes them through Vault Writer — and only at the very end constructs a `RunResult` and calls `experiments.record_run(session, experiment_id, run_result)` (`sandbox/__init__.py:556`).
- `record_run` (`experiments/__init__.py:109`) does nothing but map `RunResult` fields onto an `ExperimentRuns` row and insert it. It does not validate `run_kind`, does not check `exit_code`, does not re-derive anything — the DB CHECK on `run_kind` is the only validation at that layer.
- This is the one inbound call from `sandbox` into `experiments` for writes; `sandbox` also reads via `experiments.get_experiment` in three places (`sandbox/__init__.py:268, 316, 865`) to fetch the row it needs (notebook path, project id, slug) before building a `RunSpec` or tearing down a live notebook server.

### The measured-gate rule ("D29 gate")
This is the one piece of actual business logic in the module, and it exists in two places on purpose:
1. **Python predicate** `is_measured_eligible(run)` (`experiments/__init__.py:144`) — pure, no DB access — true only if `run.run_kind == "clean_run_all"`, `run.exit_code == 0`, and `id`, `image`, `reqs_hash`, `notebook_hash`, `approved_at` are all present. An `interactive` run, non-zero/missing exit code, or any missing provenance field is never eligible.
2. **DB CHECK** on `experiment_metrics.source <> 'measured' OR run_id IS NOT NULL` (`db/models.py:527-529`) is the actual enforcement point at rest.

`record_metric` (`experiments/__init__.py:167`) calls the Python predicate first, purely to fast-fail before a round trip: if `metric.source == "measured"`, it requires `run_id`, fetches the run, checks it belongs to `experiment_id`, and checks `is_measured_eligible`. Any failure raises `ValueError` (surfaced as HTTP 422 by `api/runs.py:56-58`). It does not re-implement the DB constraint — a `measured` metric could in principle still be rejected at the DB layer if this check were ever bypassed.

---

## Callers & dependents

All confirmed live by opening the call site:

- **`backend/api/experiments.py`** — `GET/POST/PATCH /api/projects/:id/experiments` call `list_experiments`, `create_experiment`, `update_experiment` directly (`api/experiments.py:34,41,50`). `run_all` (`api/experiments.py:173`) checks `experiments.get_experiment` exists before enqueueing `sandbox.run_experiment_job` via `jobs.enqueue` — a live dispatch path, not the deprecated inline call.
- **`backend/api/runs.py`** — `GET /api/runs/:runId` calls `experiments.get_run` (`api/runs.py:44`); `POST /api/experiments/:id/metrics` calls `experiments.record_metric` (`api/runs.py:56`), catching `ValueError` as HTTP 422.
- **`backend/harness/tools.py`** — both `log_experiment` and `update_experiment` tool schemas (`harness/tools.py:97,113`) are in `TOOL_SCHEMAS`, which the harness loop passes to every `complete(...)` call (`harness/__init__.py:488`) and dispatches through `dispatch_tool` (`harness/__init__.py:76,541` → `harness/tools.py:131`). `dispatch` calls `experiments.create_experiment` (`harness/tools.py:180`) and `experiments.update_experiment` (`harness/tools.py:198`) — **this is a live, reachable tool-call path**, not a stub; the LLM can genuinely create/patch experiment rows mid-conversation.
- **`backend/sandbox/__init__.py`** — reads `experiments.get_experiment` (3 call sites) and writes via `experiments.record_run` (1 call site, `sandbox/__init__.py:556`), as described above. All live — `run_all` is dispatched from the real `run_experiment_job` queue job, itself enqueued by `api/experiments.py`'s `run_all` route.
- **`backend/api/projects.py:131`** — the project dashboard route calls `experiments.list_experiments` to build stat counts (`in_progress`, `remaining`) and the "current focus" hypothesis list. Live, read-only.
- **`backend/matrix/__init__.py:191`** — the comparison-matrix view calls `experiments.get_experiment` per selected experiment id to build a row label; skips silently (`continue`) if the experiment no longer exists. Live, read-only.
- **`backend/sandbox/models.py:10`** — imports `experiments.models.ExperimentRun` and re-exports it as `sandbox.models.ExperimentRun` rather than defining a duplicate shape, since `experiments` is the module that persists the row. Not a call site, just a type re-export.

**Nothing found dead inside `experiments/` itself.** Every public function (`list_experiments`, `get_experiment`, `create_experiment`, `update_experiment`, `record_run`, `get_run`, `is_measured_eligible`, `record_metric`) has at least one confirmed live caller. The one adjacent dead path lives in `backend/memory/__init__.py:158-159`: `chunk_and_embed_job` raises `NotImplementedError` for `source_type="experiment"` — so experiment records never enter the memory/retrieval system despite the DB check-constraint on `project_chunks.source_type` allowing that value. That's a gap in `memory/`, not in `experiments/`, but it means nothing an experiment's `title`/`hypothesis`/`notes` ever becomes retrievable context for the harness.

---

## Open questions / rough edges

- **No delete path.** There is no `delete_experiment` — an experiment, once created, can only be patched, never removed, through this module's public surface (nothing in `api/experiments.py` exposes `DELETE` either).
- **`ExperimentMetric.value` is a string, not a number.** `MetricInput.value: str` (`experiments/models.py:159`) — any numeric comparison, sorting, or charting of metrics has to happen downstream after parsing; the model itself carries no numeric type or unit validation beyond a free-text `unit` field.
- **`setup: dict` has no schema.** `Experiments.setup` is JSONB with no further shape enforced anywhere in `experiments/` — `ExperimentInput.setup: dict | None` accepts anything.
- **`record_metric`'s ownership check has a gap window.** It checks `run.experiment_id != experiment_id` and `is_measured_eligible(run)` before inserting, but between that check and the `session.add`/`flush`, nothing re-verifies under a transaction lock — a concurrent write to the same run row (unlikely in practice, but not structurally prevented) could race. Not exploitable today since nothing else mutates a finished run.
- **`update_experiment` silently no-ops on unknown fields.** It iterates a fixed tuple of field names and uses `getattr`/`setattr` — if `ExperimentInput` ever gains a field not added to that tuple, `update_experiment` would silently ignore it rather than erroring. Currently in sync, but it's a manual sync point with no test-time enforcement visible in this file.
- **`is_measured_eligible` duplicates the DB CHECK's intent without being derived from it.** The docstring at `experiments/__init__.py:144-165` acknowledges this directly: it exists "so `record_metric` can refuse a doomed `measured` write before a round trip" and so a test suite has a pure function to assert against — but it is a second hand-written copy of the same rule (`run_kind='clean_run_all'`, `exit_code=0`, provenance fields present) that the DB CHECK also encodes. If the two ever drift, the Python check would either falsely reject an eligible run or falsely admit one to the DB where the CHECK would then reject it — either way, only caught by re-reading both.
- **Slug collisions are resolved with an unbounded loop.** `_unique_slug` (`experiments/__init__.py:52-63`) loops incrementing a suffix with no upper bound — fine for real usage volumes, but there is no cap if something pathological (e.g. thousands of identically-titled experiments) ever happened.
