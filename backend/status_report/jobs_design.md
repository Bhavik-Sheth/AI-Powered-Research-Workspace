# Job Queue — Design & Architecture

## TLDR

`backend/jobs/__init__.py` is a thin wrapper around SAQ's Postgres-backed queue: it starts one worker task per process, registers ten job functions imported from four other modules (`papers`, `memory`, `feed`, `sandbox`), exposes a single `enqueue(job_kind, **payload)` used across the API and pipeline code to hand off work off the request path, and runs a one-shot startup "catch-up pass" that seeds a `scheduled_jobs` row per project for two recurring job kinds (`feed_poll`, `interest_profile_reextract`) and dispatches any that are overdue. There is no periodic scheduler loop — recurrence exists only as a startup check plus whatever `next_due_at` was calculated last time. The module is wired into the app lifecycle in `main.py`, started only after migrations succeed, and stopped on shutdown before the DB is disposed.

## Storage / data model

**`scheduled_jobs`** (`backend/db/models.py:58-73`, class `ScheduledJobs`) — the only table this module owns directly:
- `id` (uuid pk), `job_kind` (string, no DB check-constraint visible in this model file — the docstring at `jobs/__init__.py:34-38` says Schema.md has one), `project_id` (uuid, nullable, no FK yet per its own comment), `interval_seconds`, `last_run_at` (nullable), `next_due_at` (not nullable), `created_at`.
- One row per `(job_kind, project_id)` pair; created lazily by `_ensure_schedule_rows` (`jobs/__init__.py:132-145`) the first time `run_catchup_pass` runs after a project exists, with `next_due_at = now` so a brand-new project's first poll fires on the very next catch-up pass rather than waiting a full interval.

**Queue backing store**: SAQ's own `PostgresQueue` (`saq.queue.postgres`), constructed from `get_config().libpq_dsn` (`jobs/__init__.py:104`). SAQ manages its own tables/connection pool independently of the SQLAlchemy session used for `scheduled_jobs` — the module's own docstring (`jobs/__init__.py:1-11`) flags this as a known gap: `enqueue()` is not in the same transaction as the row it concerns, so a crash between commit and enqueue silently drops the job (not a documented "guarantee," just a known narrow window).

## Core mechanics

**Startup / shutdown** (`jobs/__init__.py:101-123`):
- `start()` builds a `PostgresQueue`, connects it, builds a `Worker(_queue, functions=_job_functions(), shutdown_grace_period_s=5)`, then explicitly sets `_worker.SIGNALS = []` — this suppresses SAQ's own SIGINT/SIGTERM handlers so they don't fight uvicorn's, since shutdown is driven explicitly by `stop()`. The worker loop runs as a background `asyncio.Task`, not awaited inline.
- `stop()` calls `_worker.stop()`, awaits the worker task, then disconnects the queue. Both are no-ops (skipped, not erroring) if the corresponding global is still `None`.
- Called from `main.py`'s lifespan: `jobs.start()` and `jobs.run_catchup_pass()` run only after `db.run_migrations()` succeeds and only if Docker/vault readiness already passed (`main.py:129-138`); any exception in that block marks the `database` readiness flag failed and jobs never start. `jobs.stop()` runs on shutdown (`main.py:153`), after the sandbox's own shutdown sweep and before `db.dispose()`.

**Registered job functions** (`_job_functions()`, `jobs/__init__.py:70-98`) — imported locally (not at module top) specifically to avoid a cycle with `papers/`, which itself imports `jobs` to enqueue from within its own handlers:
| Function | Imported from |
|---|---|
| `fetch_pdf_job` | `papers` |
| `parse_paper_job` | `papers` |
| `extract_card_job` | `papers` |
| `enrich_paper_job` | `papers` |
| `embed_paper_job` | `papers` |
| `trace_references_job` | `papers` |
| `chunk_and_embed_job` | `memory` |
| `run_experiment_job` | `sandbox` |
| `poll_feed_job` | `feed` |
| `interest_profile_reextract_job` | `feed` |

SAQ registers each by its function name, so the string passed to `enqueue()` must exactly match the `def` name — every call site checked in step 2 does (see Callers section).

**Scheduling / catch-up logic for recurring jobs** (`jobs/__init__.py:59-67`, `132-168`):
- `_SCHEDULE_KINDS` is a fixed dict mapping the two schedule `job_kind` strings (`"feed_poll"`, `"interest_profile_reextract"`) to a tuple of `(registered SAQ function name, cadence in seconds, a kwargs-builder callable)`. `feed_poll` → `poll_feed_job`, 24h cadence, `_feed_poll_kwargs` (computes `since` from `last_run_at` or a 14-day initial lookback, `jobs/__init__.py:50-52`). `interest_profile_reextract` → `interest_profile_reextract_job`, 7-day cadence, `_reextract_kwargs`.
- `run_catchup_pass()` runs once at startup only (there is no `while True` / interval loop anywhere in this file — the only way a job becomes overdue again after this pass is another process restart, since nothing re-invokes this function on a timer):
  1. Opens one DB session, calls `_ensure_schedule_rows` for both schedule kinds — inserts a `ScheduledJobs` row per project missing one, due immediately.
  2. Flushes, then selects every `ScheduledJobs` row where `next_due_at <= now`.
  3. For each overdue row, looks up its `job_kind` in `_SCHEDULE_KINDS`; if not found, `continue`s silently (defensive — should not happen given step 1 only ever creates rows for the two known kinds).
  4. Calls `enqueue(function_name, **kwargs_builder(row))`, then updates `last_run_at = now` and `next_due_at = now + interval` in the same session/transaction.
  5. Logs one summary line (`event=overdue_dispatched count=N`) after the loop, only if there was at least one overdue row.
- Per-job timeouts are hardcoded module constants, not derived from SAQ's own default (10s) because both jobs fan out to external APIs and/or cold-load an ML model on first run: `_FEED_POLL_JOB_TIMEOUT_S = 300`, `_REEXTRACT_JOB_TIMEOUT_S = 60` (`jobs/__init__.py:43-47`).

## Callers & dependents

Every `jobs.enqueue(...)` call site found by grepping `backend/`, grouped by `job_kind` string:

- **`chunk_and_embed_job`** — `backend/api/notes.py:33`, on note save.
- **`fetch_pdf_job`** — `backend/papers/__init__.py:179` (initial ingest) and `:225` (retry/re-fetch path).
- **`parse_paper_job`** — `backend/papers/__init__.py:200`, `:231`.
- **`extract_card_job`** — `backend/papers/__init__.py:238`, `:339`.
- **`embed_paper_job`** — `backend/papers/__init__.py:242`, `:340`.
- **`trace_references_job`** — `backend/papers/__init__.py:246`, `:263`, `:341`.
- **`enrich_paper_job`** — `backend/papers/__init__.py:539`.
- **`run_experiment_job`** — `backend/api/experiments.py:196`, on `POST .../run_all` — enqueues rather than awaiting `run_all` directly so the HTTP response can return `run_id` immediately (confirmed by the handler's own docstring at `backend/sandbox/__init__.py:572-578`).
- **`poll_feed_job`** / **`interest_profile_reextract_job`** — never enqueued directly by any caller; only reached indirectly through `run_catchup_pass()`'s `_SCHEDULE_KINDS` dispatch (`jobs/__init__.py:162`). No other site in `backend/` calls `enqueue("poll_feed_job", ...)` or `enqueue("interest_profile_reextract_job", ...)` by hand — the only path to either is the startup catch-up pass.

**Match check**: every `job_kind` string passed to `enqueue()` across all call sites above has a function of the exact same name defined in its source module (`fetch_pdf_job`, `parse_paper_job`, `extract_card_job`, `enrich_paper_job`, `embed_paper_job`, `trace_references_job` in `papers/__init__.py`; `chunk_and_embed_job` in `memory/__init__.py`; `run_experiment_job` in `sandbox/__init__.py`; `poll_feed_job`, `interest_profile_reextract_job` in `feed/__init__.py`) and that same function is present in `_job_functions()`'s return list. No mismatch, no dead job kind, and no enqueue call reaching an unregistered function was found.

**Lifecycle callers**: `main.py:135-136` (`jobs.start()`, `jobs.run_catchup_pass()`) and `main.py:153` (`jobs.stop()`) are the only callers of those three functions in the codebase.

## Open questions / rough edges

- **No periodic re-check after startup.** `run_catchup_pass()` runs exactly once, in the lifespan's startup block. If the process stays up longer than the 24h/7-day cadences (which a long-running sidecar is expected to), nothing re-evaluates `next_due_at` again until the next restart — recurring jobs only actually recur across process restarts, not within one. There is no `asyncio` interval loop, cron-style scheduler, or SAQ `cron`/`schedule` API usage anywhere in this file.
- **Transactional-enqueue gap is self-documented, not fixed.** The module docstring (`jobs/__init__.py:1-11`) explicitly states D9 (enqueue commits in the same transaction as the row it concerns) is not implemented — SAQ's Postgres queue uses its own connection pool, separate from SQLAlchemy's session. Every one of the many `papers/__init__.py` call sites that does `db_session` work then `await jobs.enqueue(...)` inherits this same narrow crash window.
- **Silent `continue` on unrecognized schedule `job_kind`** (`jobs/__init__.py:159-160`): defensive code with no logging — if a `scheduled_jobs` row ever existed with a `job_kind` outside `_SCHEDULE_KINDS` (e.g., stale data from a schema change), it would be silently skipped forever with no signal that it's stuck.
- **`ScheduledJobs.job_kind` has no enforced constraint at the ORM level.** The model (`db/models.py:68`) is a plain `String`; the check-constraint referenced by the `jobs/__init__.py:34-38` comment lives in the DB migration/Schema.md, not in this file, so nothing in Python prevents a caller from inserting an arbitrary string outside the two known kinds.
- **`enqueue()` return value (`Job | None`) is never checked by any caller.** Every call site in `papers/__init__.py`, `notes.py`, and `experiments.py` simply `await`s it and discards the result — there's no handling for the `None` case (queue not started) beyond the `RuntimeError` raised inside `enqueue()` itself if `_queue is None`.
- **`_worker.SIGNALS = []`** is a private-attribute override on SAQ's `Worker` (`jobs/__init__.py:111`), not a documented public API — brittle to a SAQ upgrade changing that attribute's name or semantics.
