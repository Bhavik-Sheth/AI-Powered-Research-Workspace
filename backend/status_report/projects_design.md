# Project Record — Design & Architecture

`backend/projects/` is two files: `__init__.py` (CRUD over the `projects` and `project_papers`
tables) and `models.py` (Pydantic wire shapes for the Dashboard projection). It owns project
creation, library membership, tab-stack persistence, and per-paper relevance tagging — nothing
else. The Dashboard aggregation itself is *not* implemented here; it lives in
`api/projects.py:get_dashboard`, which calls into this module plus `notes`, `experiments`, `feed`
and `papers` and assembles `DashboardSummary` from their outputs.

---

## Storage / data model

**`projects` table** (`db/models.py:94-118`): `id`, `name`, `slug` (unique), `focus_seed`,
`interest_profile` (JSONB, owned by `feed`, not touched here), `corpus_centroid` (768-dim vector,
also owned/written by `feed._recompute_corpus_centroid`, not this module), `tab_stack` (JSONB
array, default `[]`), `active_tab`, `last_opened_at`, `created_at`, `updated_at`.

**`project_papers` table** (`db/models.py:352-367`): composite PK `(project_id, paper_id)`,
`relevance` (check-constrained to `relevant`/`somewhat`/`not`/`unset`, default `unset`),
`why_relevant`, `added_at`, `resume_position` (JSONB, nullable). This is the only place a paper
becomes project-specific — a paper's actual content is global and computed once
(`models.py:47` docstring cites D25).

`models.py` defines no tables of its own — only Pydantic response models for the Dashboard:
`DashboardStat` (a tile's `total` + an always-actionable `qualifier` string),
`NeedsAttentionItem` (`nudge`/`error` severity, optional `paper_id` + `retry` action),
`CurrentFocus` (`focus_seed` + in-progress experiment hypotheses), `ExperimentProgress` (four
fixed bands: `planned`/`remaining`/`in_progress`/`done`), `PendingExperimentItem`,
`RelevantPaperItem`, and the `DashboardSummary` envelope that bundles all of the above.

---

## Core mechanics

**Project creation** (`__init__.py:30-35`, `create_project`): builds a `Project` with a fresh
UUID and a slug from `slugify.slugify(name)`. `_unique_slug` (`__init__.py:20-27`) loops,
appending `-2`, `-3`, ... until a free slug is found — a plain `SELECT`-then-retry, no DB-level
uniqueness handling beyond the eventual `unique=True` constraint. `focus_seed` is passed straight
through, optional. `session.flush()` only — no commit inside the function (commit happens at the
`db.session()` context-manager boundary in the caller, per `api/projects.py`'s `async with
db.session()` blocks).

**Library membership** — `add_paper_to_project` (`__init__.py:46-56`) is a Postgres
`INSERT ... ON CONFLICT DO NOTHING` on `(project_id, paper_id)`, then a `session.get` to return
the row either way (works whether the insert happened or the pair already existed). It does not
verify the project or paper exists first — callers are expected to have already checked
(`api/papers.py` and `api/projects.py`'s dependents all call `projects.get_project` first and 404
before this). `list_project_papers` (`__init__.py:59-69`) joins `Paper` + `ProjectPapers` and
orders by `added_at desc` — most recently added first.

**Relevance tagging** — `set_paper_relevance` (`__init__.py:89-101`) is a partial update: only
`relevance` or `why_relevant` fields that are non-`None` in the call actually change, so a caller
can patch just one field without clobbering the other. Returns `None` if the membership row
doesn't exist (caller 404s).

**Tab-stack persistence** — `save_tab_stack` (`__init__.py:72-86`) overwrites `tab_stack` and
`active_tab` wholesale (no merge/diff) and, in the same write, bumps `last_opened_at` to
`datetime.now(timezone.utc)`. The docstring is explicit that this piggybacks Dashboard's
"continue where you left off" signal onto the same write path every navigation change already
triggers, rather than tracking "last opened" separately. Returns `None` if the project doesn't
exist.

**Dashboard aggregation** (`api/projects.py:117-228`, `get_dashboard` — outside this module but
the module's only real consumer of its own outputs beyond simple CRUD): pulls
`list_project_papers`, `notes.list_notes` / `list_unlinked_note_ids`, `experiments.list_experiments`,
`feed.get_feed`, all inside one `db.session()` block, then does all counting/mixing in Python
after the session closes:
- `unmarked` = count of papers with `relevance == "unset"`.
- Stalled/failed detection (`_stalled`, `api/projects.py:41-48`) reads four fixed pipeline-stage
  columns (`fetch_state`, `parse_state`, `embed_state`, `extract_state`) and flags a paper as
  stalled if a later stage is `queued` while its prerequisite is already `done` — mirrors the
  frontend's own `needsRetry` check minus the `failed` case (reported separately as an `error`
  row).
- `focus_text` is built by joining `focus_seed` with all in-progress experiment hypotheses, then
  fed to `search.reranker.rerank` against each paper's `"{title}. {abstract}"` string to produce
  the top-5 `relevant_papers`. A `TimeoutError` from `rerank` is caught and degrades to an empty
  list — the dashboard route never fails because of this section.
- `needs_attention` accumulates: one `error` row per paper with any `failed` stage, one `nudge`
  row per stalled paper, one summary `nudge` for unmarked-paper count, one summary `nudge` for
  `remaining`-status experiment count.
- `feed_qualifier` is `"new since {weekday}"` from the max `polled_at` in the feed items, or
  `"nothing new"` if the feed is empty.

Nothing here writes anything — the whole route is a read-only projection, as its own docstring
states (`api/projects.py:119-122`).

---

## Callers & dependents

All confirmed live via grep + read of each call site:

- `api/projects.py` — full REST surface: `POST /api/projects` → `create_project`,
  `GET /api/projects` → `list_projects`, `GET /api/projects/{id}` → `get_project`,
  `GET /api/projects/{id}/dashboard` → `get_project` + `list_project_papers` + the aggregation
  described above, `PUT /api/projects/{id}/tab-stack` → `save_tab_stack`.
- `api/papers.py` — `POST /api/projects/{id}/papers` (`add_paper`, line 31-34) calls
  `get_project` then `add_paper_to_project`; `GET /api/projects/{id}/papers` (line 45-49) calls
  `list_project_papers`; `POST /api/projects/{id}/papers/{paper_id}/promote` (line 119) calls
  `add_paper_to_project` after re-driving the pipeline via `papers.reprocess_paper`;
  `PATCH /api/projects/{id}/papers/{paper_id}` (line 138) calls `set_paper_relevance`.
- `harness/tools.py:152` — the `add_paper` tool's dispatch branch calls
  `projects.add_paper_to_project` after `papers.add_paper`, live in the agent tool-call path
  (`dispatch`, reachable from `TOOL_SCHEMAS`'s `add_paper` entry).
- `feed/__init__.py:317` — `save_item` calls `add_paper_to_project` when a feed item is saved
  into the library, then recomputes `corpus_centroid` itself (that field is owned/written by
  `feed`, not by this module, despite living on the `projects` table). The module's own docstring
  (`feed/__init__.py:14-21`) flags this cross-module reach-through explicitly, alongside the same
  reach-through in `api/papers.py`'s two-step add.

**Nothing found dead.** Every public function in `__init__.py` (`create_project`, `list_projects`,
`get_project`, `add_paper_to_project`, `list_project_papers`, `save_tab_stack`,
`set_paper_relevance`) has at least one live caller reachable from a route or the harness tool
dispatch. The one thing that *looks* unused from inside this module is `project_papers.resume_position`
(`db/models.py:367`) — the column is declared in the ORM model and the migration
(`alembic/versions/0004_phase1_papers_and_related.py:134`) but grep finds no read or write of it
anywhere in `backend/`. It's schema, not code, in this module's own working set.

---

## Open questions / rough edges

- **The module's own docstring flags a gap it doesn't resolve.** `__init__.py:1-7` states
  "MODULES.md does not name an owning module for basic project CRUD; this module fills that gap
  ... Flagged here for reconciliation against MODULES.md, not added silently." That reconciliation
  hasn't happened in the code — the module exists and is load-bearing, but its own header says its
  place in the module map is still unresolved.
- **Dashboard aggregation lives outside the module it's modeled for.** `projects/models.py`
  defines every Dashboard wire shape, but the actual aggregation logic (`get_dashboard`) is in
  `api/projects.py`, not `projects/__init__.py`. That's a ~110-line non-trivial computation
  (stall detection, rerank-based relevance ranking, needs-attention rule set) sitting in the API
  layer rather than the service layer the rest of this module's docstring insists routes should
  defer to ("REST API routes never touch SQL directly ... call a service package").
  `get_dashboard` doesn't touch SQL directly (it calls other modules' service functions), so it
  technically complies, but the business logic — which fields count as "needs attention", what a
  "stalled" paper is — is API-layer code, not `projects`-owned.
- **`add_paper_to_project` trusts its caller for existence checks.** It doesn't verify
  `project_id` or `paper_id` are real rows; every current call site happens to check
  `get_project` first (or, for `paper_id`, has just created/fetched the paper in the same
  transaction), but that invariant is enforced by convention across four different call sites
  (`api/papers.py`, `api/projects.py`'s dependents, `harness/tools.py`, `feed/__init__.py`), not
  by the function itself.
- **`resume_position` on `project_papers` is fully unused.** Column exists in both the ORM model
  and the migration, but nothing in the codebase reads or writes it — dead schema.
- **`_unique_slug`'s retry loop has a race window.** It's a plain read-then-write with no
  transaction-level locking; two concurrent `create_project` calls for the same name could both
  observe the same free slug before either commits, since the uniqueness is only enforced by the
  DB constraint at flush time (which would raise, not retry) — not a bug in single-user/solo usage
  but worth naming since the code has no guard against it.
- **`corpus_centroid` and `interest_profile` live on the `projects` table but are entirely
  written and read by `feed/__init__.py`, never by this module** — `projects/__init__.py` has no
  function that touches either column, even though they're columns on the table this module
  otherwise owns end-to-end.
