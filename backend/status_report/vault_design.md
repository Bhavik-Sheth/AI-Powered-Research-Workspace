# Vault Writer — Design & Architecture

Vault Writer is the sole plain-file writer of the vault: a folder tree on disk (root path from
`settings.get_vault_path()`) holding notes, manuscripts, paper assets, highlights mirrors, and
experiment notebooks/logs as real files, each paired with a DB index row written in the same
operation. Two files make up the whole module — `backend/vault/__init__.py` (the writer functions)
and `backend/vault/models.py` (the wire-shape Pydantic models). It owns no DB schema of its own;
every write lands in a table another module owns (`notes`, `documents`, `highlights`, `experiments`).

---

## Storage / data model

The module defines no SQLAlchemy tables. It writes to a fixed on-disk layout under the vault root
and to columns on tables owned elsewhere:

- **Layout** (`backend/vault/__init__.py:26`, `_LAYOUT`): `library/papers`, `projects`,
  `.research-os` — the three top-level folders `ensure_layout()` guarantees exist.
- `library/papers/<canonical-id>/` — global, shared across every project a paper belongs to
  (`write_paper_asset`, `backend/vault/__init__.py:240`). Holds `paper.pdf` and `parsed.json`.
- `projects/<slug>/notes/<slug-of-title>.md` — one Markdown file per note, YAML frontmatter (`id`,
  `title`) + `---` delimiter + body (`write_note`, `:76`, `_render_markdown` at `:56`).
- `projects/<slug>/manuscript/<slug-of-title>.tex` (+ sibling `.pdf` when Tectonic compiled it) —
  `write_document`, `:148`.
- `projects/<slug>/manuscript/assets/<slug>.ext` — inserted images/diagrams/dataviz exports,
  `write_manuscript_asset`, `:212`.
- `projects/<slug>/papers/highlights/<canonical-id>.json` — one JSON array file per paper per
  project, appended to on each highlight (`write_highlight`, `:260`).
- `projects/<slug>/experiments/<exp-slug>/notebook.ipynb` + `requirements.txt` +
  `runs/<run_id>/stdout.log` — `write_experiment_files`, `:343`.

DB rows that point at these paths: `Notes.file_path`, `Documents.file_path`,
`Experiments.notebook_path`, `experiment_runs.stdout_ref` (per the `RunArtifacts`/
`ExperimentFilesWritten` models, `backend/vault/models.py:139-176`), and `experiment_runs.artifacts`
(a `[{path, kind, bytes}]` list of already-on-disk files vault does not itself write bytes for).
`highlights` rows carry no file path — the JSON mirror is keyed by paper canonical id, not indexed
per-highlight.

---

## Core mechanics

**Startup**: `ensure_layout()` (`:40`) creates the three `_LAYOUT` folders under the vault root,
then probes writability by creating and deleting `.research-os/.write-check`. Raises `OSError` if
the resolved path exists but isn't writable — callers (only `main.py:110`) treat that as the
`vault` readiness capability failing, not a crash.

**Write path, all artifact types follow the same two-phase shape**: write the file to disk first,
then update/insert the DB row, both inside the caller's already-open `db.session()`. If the file
write fails: `VaultWriteFailed` from an `OSError`, nothing committed. If the file write succeeds
but the DB step raises: `VaultWriteFailed` is raised anyway, and per the module's own docstring
(`:31-37`) the file that's already on disk becomes an "accepted orphan" — the caller's
`db.session()` rolls back the DB side, but there is no reconciliation pass that would clean up or
re-adopt the orphaned file.

- `write_note` (`:76`): create (no `frontmatter_id`) generates a new UUID, computes a unique
  slugified path via `_unique_note_file_path` (`:61`, collision-checked against `Notes.file_path`
  in DB, suffixes `-2`, `-3`, …), and writes frontmatter `{id, title}` + body. Update (frontmatter_id
  present) overwrites the file at its unchanged `file_path` — id and location never move on edit.
- `write_document` (`:148`): same create/update shape, `.tex` extension, path under
  `manuscript/`. When `compiled_pdf` bytes are supplied (Tectonic escape-hatch compile), writes the
  PDF to the `.tex` path's sibling with `.pdf` suffix in the same call — no separate DB column for
  the PDF path, it's derived from `file_path`.
- `manuscript_dir` (`:198`): resolves and `mkdir`s `projects/<slug>/manuscript/` — the one place
  that owns this path shape, so a Tectonic compile mount and an asset-insert write agree on the same
  directory without re-deriving it.
- `write_manuscript_asset` (`:212`): slugifies the filename stem, keeps the original suffix
  lowercased, resolves collisions by appending `-2`, `-3`, … (checked against the filesystem, not
  the DB — there's no assets index table). Returns the vault-relative path for the caller to inline
  as `\includegraphics`; no DB row is created for it.
- `write_paper_asset` (`:240`): writes `paper.pdf` (raw bytes) or `parsed.json`
  (`json.dumps(..., indent=2)`) under the paper's canonical-id folder. No DB write inside this
  function at all — no index row, no `VaultWriteFailed` on this path since it can raise a plain
  `OSError`/`FileNotFoundError`-style failure straight through (only paper-not-found raises `ValueError`).
- `write_highlight` (`:260`): reads the existing JSON array from the paper's mirror file (or starts
  a new list), appends one entry with `id`, `quote`, `prefix`/`suffix`, `char_offsets`,
  `section_heading`, `comment`, `color`, `note_id`, `created_at`, rewrites the whole file, then
  inserts a `Highlights` row. Note: this read-modify-write on the mirror file is not atomic across
  concurrent highlight writes to the same paper — two concurrent calls both read the same on-disk
  list state and each write clobbers the other's own read (last write wins, dropping one entry). No
  locking or optimistic-concurrency check guards it.
- `write_experiment_files` (`:343`): always (re)writes `notebook.ipynb` at a fixed path derived from
  the experiment's slug (not versioned — every call overwrites in place) and creates an empty
  `requirements.txt` if absent (created once, never populated afterward by this function). When a
  `run: RunArtifacts` is passed, additionally writes `runs/<run_id>/stdout.log` from the captured
  bytes and returns its path as `stdout_ref`. `run.artifacts` is metadata only — this function does
  not move or copy those files' bytes; the caller's run container already wrote them directly into
  the same mounted directory.

**Read path**: none. This module exposes only writers — every artifact type it manages is read by
its owning module reading `file_path`/`notebook_path`/`stdout_ref` columns directly (e.g. sandbox's
`if experiment.notebook_path is not None and (get_vault_path() / experiment.notebook_path).exists()`
at `backend/sandbox/__init__.py:272`), not through any function in `vault/`.

---

## Callers & dependents

All are live, confirmed by tracing each call site to a mounted router or an in-process call chain:

- `main.py:110` — `vault.ensure_layout()` runs at app startup, gates the `vault` readiness capability.
- `api/notes.py:28,43` — `POST`/`PATCH /api/projects/{project_id}/notes` call `write_note`; router
  mounted at `main.py:171`.
- `harness/tools.py:172` — the `save_note` tool (agent-invocable mid-turn) calls `write_note`
  directly, independent of the notes API route.
- `writing/__init__.py:94,101` — manuscript save path calls `write_document`, one branch passing
  `compiled_pdf` after a Tectonic compile, the other without. Reached via `api/writing.py`, router
  mounted at `main.py:183`.
- `writing/__init__.py:77-78` — calls `vault.manuscript_dir` to get the mount point before invoking
  `writing/tectonic.py:compile_tex`.
- `writing/__init__.py:124` — asset-insert path calls `write_manuscript_asset`.
- `api/highlights.py:27` — `write_highlight` called from the highlights POST endpoint; router
  mounted at `main.py:172`.
- `papers/__init__.py:141,335` — paper ingest pipeline calls `write_paper_asset` for both the raw
  PDF and the parsed JSON, unconditionally on ingest.
- `sandbox/__init__.py` — four live call sites, all reached from the experiments/sandbox flow:
  `:284` (cell edit, saves notebook after inserting a code cell), `:532` (after a measured run
  completes, writes notebook + stdout + artifact list), `:759` (lazily creates an empty notebook the
  first time a live Jupyter server starts for an experiment with no `notebook_path` yet), `:868`
  (persists the notebook when a live server session stops).

Nothing found wired to nothing. Every public function in `vault/__init__.py` (`ensure_layout`,
`write_note`, `write_document`, `manuscript_dir`, `write_manuscript_asset`, `write_paper_asset`,
`write_highlight`, `write_experiment_files`) has at least one confirmed live caller.

---

## Open questions / rough edges

- **`write_highlight`'s JSON mirror is a classic read-modify-write race.** Two concurrent
  highlights on the same paper in the same project can both read the file before either writes,
  and the second write silently discards the first's new entry — there's no file lock, no
  optimistic version check, and the DB `Highlights` row for the lost entry would still exist,
  orphaned from the mirror file that's supposed to contain it.
- **Orphan files have no reconciliation pass.** The module's own docstring (`:31-37`) names this
  explicitly: if the DB step fails after the file write succeeds, the file stays on disk forever
  unless the same frontmatter id is written again. There is no sweep, no consistency checker, no
  startup repair — `VaultWriteFailed` is raised and that's the end of it.
- **`write_paper_asset` has no `VaultWriteFailed` semantics at all**, unlike every other writer in
  the module — it doesn't wrap `OSError` and it never touches the DB, so its failure mode is a bare
  exception straight to the caller (`papers/__init__.py`), not the two-phase pattern the rest of the
  module documents as its own convention.
- **`requirements.txt` is created once, empty, and never written to again** by this module —
  nothing in `vault/` populates it; whatever's meant to fill it (if anything) lives outside this
  component entirely, or the field is currently vestigial.
- **Manuscript asset filenames are deduplicated against the filesystem, not the DB**
  (`write_manuscript_asset`, `:226-230`), the opposite of `write_note`/`write_document`, which
  dedupe against DB rows — an inconsistency in how the same "avoid collision" problem is solved
  twice in the same file, presumably because there's no DB table for assets to check against.
- **`write_experiment_files` always overwrites `notebook.ipynb` in place with no versioning** — a
  crash or a bad write from a live Jupyter server mid-session (`sandbox/__init__.py:868`) has no
  prior version to fall back to; only `stdout.log` per run is preserved historically under
  `runs/<run_id>/`.
