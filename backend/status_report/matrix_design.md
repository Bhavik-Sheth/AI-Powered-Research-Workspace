# Literature Matrix — Design & Architecture

Matrix = one Postgres table for the artifact (`matrices`) plus one table for cell state (`matrix_cells`), sitting on top of Paper Pipeline's already-extracted cards. Opening a matrix never re-extracts anything for standard columns — it's a pure projection, read live off `paper_cards`. A custom column is the one place new extraction happens, one scoped LLM query per paper, cached forever after the first run. Editing any cell — standard or custom — writes a `source: user` row into the same `matrix_cells` table and takes priority over the projection/cache without ever overwriting the extracted value underneath it.

---

## Storage / data model

**`matrices`** (`db/models.py:566-583`) — one row per matrix artifact:
- `id`, `project_id` (FK → `projects`, cascade delete)
- `name`
- `selected_paper_ids: UUID[]`, `selected_experiment_ids: UUID[]` — arrays, not join tables, because row order is part of the artifact and the whole thing is written as one `PUT`
- `column_defs: JSONB` — list of `{column_key, label, kind, query}` dicts (see `ColumnDef`, `matrix/models.py:18-22`); `kind` is `"standard" | "custom" | "user"`

**`matrix_cells`** (`db/models.py:586-615`) — one row per stored cell, combining two different roles in one table, distinguished by `source`:
- `matrix_id` (FK), `paper_id` and `experiment_id` (both nullable FKs — exactly one is set, enforced by `matrix_cells_one_row_kind_check`)
- `column_key`, `value` (string, always the plain text shown in the cell)
- `source`: `"extracted" | "user"` (`matrix_cells_source_check`)
- `anchor_id` (FK → `quote_anchors`, nullable) — required whenever `source = 'extracted'` (`matrix_cells_extracted_requires_anchor_check`); always `NULL` for `source = 'user'`
- `cached_at`
- Unique on `(matrix_id, paper_id, experiment_id, column_key)` — this is the cache/override key, but see the Postgres NULL caveat in Core mechanics below.

**Wire/value models** (`matrix/models.py`): `STANDARD_FIELD_KEYS = ("problem", "method", "datasets", "results", "limitations")` — a standard column's `column_key` is always one of these and must match `paper_cards.field_key` exactly for the projection to find anything. `Matrix`, `MatrixRow`, `MatrixCell`, `MatrixView` are the read shapes; `MatrixUpdate` and `UpdateCellRequest` are the write shapes. `MatrixCell.row_id` is overloaded to mean either a paper id or an experiment id depending on `row_kind`.

---

## Core mechanics

**Building a matrix** (`matrix/__init__.py:53-86`): `build_matrix` inserts an empty `Matrices` row (no rows/columns selected yet). `update_matrix` is the only way rows/columns get populated — it's an explicit `PUT`: every field on `MatrixUpdate` is optional, but any array present (`selected_paper_ids`, `selected_experiment_ids`, `column_defs`) fully replaces what's stored, never merges. `get_matrix` / `list_matrices` are straight reads, converted from ORM row to `Matrix` via `_matrix_from_row` (`matrix/__init__.py:42-50`).

**Projecting the view** (`get_matrix_view`, `matrix/__init__.py:149-203`) — the actual pipeline that runs on every open:
1. Load the `Matrices` row; bail (`None`) if it doesn't exist.
2. Load every `MatrixCells` row for this matrix once, into a dict keyed by `(paper_or_experiment_id, column_key)` (`_cell_overrides`, `matrix/__init__.py:100-105`) — one query, not one per cell.
3. For each `paper_id` in `selected_paper_ids`, in array order:
   - Fetch the `Paper` (skip silently if it's gone — a stale id in the array just drops that row, no error surfaced).
   - Fetch that paper's card fields via `papers.get_paper_card` and index by `field_key`.
   - For each `column_def`, in order:
     - If a `matrix_cells` row exists for `(paper_id, column_key)` — from either a user edit or a previously cached custom extraction — use it verbatim, regardless of the column's `kind`. Stored state always wins.
     - Else if `kind == "standard"`: look up the paper's card field by `column_key`; if present, build a `MatrixCell` directly from the card field's `value`, `anchor_id`, `section_heading`, `char_start`, `char_end` — no DB write, no LLM call, purely in-memory projection of already-extracted data.
     - Else if `kind == "custom"`: call `_run_scoped_extraction` (below) — this one path can write a new `matrix_cells` row as a side effect of a `GET`.
     - Else (`kind == "user"`, no override yet): no cell is emitted at all. The frontend renders absence as "not stated" — there is no sentinel row, no placeholder value.
4. For each `experiment_id` in `selected_experiment_ids`: only `matrix_cells` overrides are ever looked up — no projection source exists for experiment rows. Standard and custom columns are defined as paper-scoped, so an experiment row only ever shows user-entered cells.
5. Returns `MatrixView{matrix, rows, cells}` — `cells` is a flat list, not nested per row; the frontend re-associates by `(row_id, column_key)`.

**Custom-column scoped extraction** (`_run_scoped_extraction`, `matrix/__init__.py:108-146`):
1. Load the paper's full text via `papers.get_paper_content`; bail if missing or the column has no `query`.
2. One `complete_structured` call, `tier="auxiliary"`, 60s timeout, forcing verbatim-quote extraction (`_SCOPED_EXTRACTION_PROMPT`) — asks for `quote` + `prefix` + `suffix`, explicitly forbidding paraphrase or inference, over `content.full_text[:60_000]` (same 60k-char bound `extract_card_job` uses).
3. Any `LLMError` subtype or `RuntimeError` from the LLM call is swallowed → returns `None` (no cell, "not stated"; the whole request never fails just because one column's extraction errored).
4. If the model returned an empty `quote` → also `None` (paper legitimately doesn't answer the question).
5. Otherwise, the quote is run through `provenance.validate_and_anchor` (verifies it actually appears in the source text, presumably using prefix/suffix for disambiguation) — if that fails to find/anchor it, also `None`. A quote that can't be anchored back to real text is never stored.
6. On success: insert a new `MatrixCells` row with `source="extracted"`, `value=quote`, the returned `anchor_id`, `cached_at=anchor.validated_at`. This row now satisfies the override lookup on every future view, so the LLM call happens at most once per `(paper, column)` pair for the matrix's lifetime — there is no cache invalidation or re-run path in this module.

**Editing a cell** (`update_cell`, `matrix/__init__.py:206-251`):
1. Load the `Matrices` row; `None` if missing.
2. Determine `row_kind` by checking membership of `row_id` in `selected_paper_ids` / `selected_experiment_ids` — rejects (`None`) edits to rows not currently on the matrix.
3. Manually `SELECT`s for an existing `MatrixCells` row on `(matrix_id, row_column, column_key)` rather than using `ON CONFLICT` — the code comments explain why: the unique constraint includes both `paper_id` and `experiment_id`, and since exactly one is always `NULL`, Postgres never considers two rows with the same non-null id but differing NULL-vs-NULL to conflict, so an upsert against that constraint would just keep inserting duplicates.
4. If found: mutates the existing row in place — sets `value`, forces `source="user"`, and explicitly clears `anchor_id` and `cached_at` to `None` (a user edit permanently detaches that cell from its previous extraction anchor, even if it was previously `"extracted"`).
5. If not found: inserts a fresh `MatrixCells` row with `source="user"`, no `anchor_id`.
6. Returns the resulting `MatrixCell`.

Extracted values are never mutated by this path — the original `paper_cards` row (standard columns) or a prior cached custom-extraction row is either overwritten in-place by the same override row (if it existed) or left alone with a separate override sitting on top of it via the priority order in step 3 of `get_matrix_view`.

---

## Callers & dependents

**Live path — `backend/api/matrix.py`**, registered in `backend/main.py:38,181` behind `require_bearer_token`:
- `GET /api/projects/{id}/matrix` → `matrix.list_matrices`
- `POST /api/projects/{id}/matrix` → `matrix.build_matrix`, then optionally `matrix.update_matrix` in the same call if the POST body already carries selections/columns
- `GET /api/projects/{id}/matrix/{matrix_id}` → `matrix.get_matrix_view` (404 if `None`)
- `PUT /api/projects/{id}/matrix/{matrix_id}` → `matrix.update_matrix` (404 if `None`)
- `PATCH /api/projects/{id}/matrix/{matrix_id}/cells` → `matrix.update_cell` (404 if `None`)

**Live path — frontend**, `frontend/src/matrix/MatrixView.tsx` (467 lines) calls the generated client (`getMatrixViewApiProjectsProjectIdMatrixMatrixIdGet`, etc.) against these exact routes, and is mounted from `frontend/src/app/AppShell.tsx:17,190,198,213,244,623-624` under a "Matrix" tab reachable from the Discover section — this is a reachable, rendered tab in the running app, not a stub.

**Module dependencies, all confirmed live:**
- `papers.get_paper`, `papers.get_paper_card`, `papers.get_paper_content` (`backend/papers/__init__.py:251,287,273`)
- `experiments.get_experiment` (`backend/experiments/__init__.py:47`)
- `provenance.validate_and_anchor` (`backend/provenance/__init__.py:35`)
- `llm.complete_structured` on the `"auxiliary"` tier, same mechanism the rest of the LLM-calling backend uses

**Nothing dead found in this module.** Every public function in `matrix/__init__.py` has a live API route calling it, and every route has a live frontend call site. The `kind == "user"` column type is defined in the model but has no create UI path checked here beyond what `MatrixView.tsx` wires (it calls `putMatrix` with a `column_defs` array that can include any `kind`) — not traced further since it's frontend, not this module.

---

## Open questions / rough edges

- **A `GET` can write to the database.** `get_matrix_view` calls `_run_scoped_extraction` for every custom column with no cached cell yet, which inserts a `matrix_cells` row via `session.flush()`. Nothing here is idempotent-by-HTTP-verb; refreshing the view for a fresh custom column can trigger an LLM call as a side effect of a read.
- **Stale array ids are silently dropped, not reported.** If `selected_paper_ids` or `selected_experiment_ids` contains an id whose `Paper`/`Experiment` row no longer exists, that row just vanishes from the view (`matrix/__init__.py:159-161`, `191-193`) — no error, no placeholder, no way for a caller to know the array itself still references it until they inspect `matrix.selected_paper_ids` directly.
- **No re-extraction path for custom columns, ever.** Once a `(paper_id, column_key)` cell is cached with `source="extracted"`, it is permanent — there's no "re-run this column" or cache-busting operation anywhere in the module. If the underlying paper's card were re-extracted or corrected upstream, a custom column referencing that paper would never reflect it; only editing the cell by hand (which then permanently marks it `source="user"`) can change it.
- **Standard-column projection re-fetches the full card on every row, every view.** `get_matrix_view` calls `papers.get_paper_card` once per selected paper on every single `GET`, with no caching layer of its own (unlike custom columns, which cache in `matrix_cells`) — cost scales with `selected_paper_ids` size on every open, though this is a projection of already-extracted data so it's a DB read, not an LLM call.
- **Column key collisions across kinds are unenforced at the type level.** `ColumnDef.column_key` is a bare string; nothing stops a `"custom"` or `"user"` column from being given a `column_key` matching one of `STANDARD_FIELD_KEYS`, which would make it indistinguishable from a standard column column-wise (though the `kind` on the stored `column_defs` JSONB still governs behavior, so a same-named custom column just behaves like a custom column with a coincidentally standard-looking key).
- **`MatrixCell.row_id` is a single field standing in for two different foreign keys** (`paper_id` or `experiment_id` depending on `row_kind`) both on the wire model and in `_cell_from_row` (`matrix/__init__.py:89-97`) — correct today because the two id spaces are UUIDs from different tables, but there's no type-level guard against, say, accidentally passing an experiment id where a paper id was expected elsewhere in this module (`update_cell`'s membership check on `row_id` is what actually enforces the distinction at write time).
- **Experiment rows can never gain a standard or custom cell**, by design of the loop in `get_matrix_view`, but the `ColumnDef`/`RowKind` types don't encode this restriction — it's implicit in the two separate loops (paper loop checks `column.kind`; experiment loop skips straight to override lookup). A column defined as `"standard"` simply renders empty for every experiment row with no signal to the caller why.
