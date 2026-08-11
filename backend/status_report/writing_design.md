# Writing (Manuscript) — Design & Architecture

Manuscript owns one Postgres table (`documents`) mirroring a LaTeX `.tex` file that Vault Writer
actually persists to disk, plus two pieces of logic Vault Writer is deliberately kept ignorant of:
compiling that `.tex` through a sandboxed, offline Tectonic-in-Docker container, and running a
missing/unsupported citation check over its text. Everything is invoked synchronously from six
FastAPI routes in `backend/api/writing.py` — there is no background job, no queue, nothing else in
the codebase calls into `backend/writing/`.

---

## Storage / data model

**One table, `documents`** (`backend/db/models.py:618-639`):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | server-default `gen_random_uuid()` |
| `project_id` | UUID FK → `projects.id`, `ondelete="CASCADE"` | |
| `title` | String | |
| `file_path` | String | unique per `(project_id, file_path)`; empty string `""` when a Tectonic compile fails on first save (`writing/__init__.py:86`) since no file was ever written |
| `body` | String | the `.tex` source — an indexed mirror; the docstring at `writing/__init__.py:1-7` and `db/models.py:620` both call the on-disk `.tex` file the actual truth |
| `citation_findings` | JSONB, default `[]` | serialized `CitationFinding[]` from the *last* `check_citations` run — stale until re-run, not recomputed on save |
| `last_compiled_at` | TIMESTAMPTZ, nullable | `None` until a Tectonic compile succeeds |
| `last_compile_engine` | String, nullable, CHECK IN `('swiftlatex','tectonic')` | `None` for a never-compiled or SwiftLaTeX-only document |

Wire models in `backend/writing/models.py`: `CitationFinding` (`kind: "missing"|"unsupported"`,
`span`, optional `note`), `Reference` (`paper_id`, `cite_key`, `title`, `authors`, `year`),
`CompileResult` (`success`, `engine: "tectonic"`, `log`, `document`), `AssetInserted` (`path`).
`Document`/`DocumentInput` are not owned here — they're `vault.models` types re-exported
(`writing/models.py:1-16`) because Manuscript depends on Vault Writer, not the reverse.

There is no `references` table: `Reference` rows are computed on every call from the project's
existing `papers`/`project_papers` join (`writing/references.py:27-50`) — nothing about citations
is persisted except the `cite_key`-bearing findings inside `documents.citation_findings`.

---

## Core mechanics

### Document save (`writing.save_document`, `writing/__init__.py:59-101`)
- `engine=None` or `"swiftlatex"`: pure passthrough to `vault.write_document` — Manuscript never
  touches Docker; the SwiftLaTeX preview is presumed to run client-side in WASM.
- `engine="tectonic"`: resolves the manuscript directory via `vault.manuscript_dir`, runs
  `tectonic.compile_tex`, then branches:
  - **Failure**: returns `CompileResult(success=False, ...)` carrying the engine log. The `.tex`
    on disk is *not* touched — write only happens after a successful compile. If the document
    doesn't exist yet, a throwaway in-memory `Document` with `file_path=""` is constructed just to
    satisfy the response shape.
  - **Success**: `vault.write_document` persists `.tex` + the compiled PDF bytes in one call, then
    `last_compiled_at`/`last_compile_engine` are stamped directly on the ORM row and flushed.

### Tectonic compile (`writing/tectonic.py:50-99`)
- Writes `tex` to a UUID-scratch-named file (`.compile-<uuid>.tex`) inside the manuscript's own
  directory — never the real `<slug>.tex` — so `\includegraphics{assets/...}` resolves against
  the same tree an asset-insert wrote into, and a crash never corrupts the persisted source.
- Runs `docker_client.containers.run(TECTONIC_IMAGE, command=["-b", BUNDLE_PATH, "--only-cached",
  "--untrusted", "--chatter=minimal", tex_path.name], network_mode="none", mem_limit="1g",
  detach=True)`, mounting the manuscript dir at `/workspace` and a persistent cache dir
  (`.research-os/tectonic-cache/`) at `/cache`.
- `container.wait(timeout=120)`; reads `container.logs()`; always force-removes the container in a
  `finally`; always deletes the scratch `.tex`/`.pdf` in an outer `finally` regardless of outcome —
  the returned `pdf_bytes` is a copy read into memory first.
- `docker.errors.ImageNotFound` is caught explicitly and turned into a `(False, "...", None)`
  result rather than propagating — "image not built yet" is treated as an ordinary compile failure,
  not a 500.
- Global module-level singleton `_docker_client`, lazily created via `docker.from_env()` inside
  `asyncio.to_thread` (the `docker` SDK is synchronous).

### Citation check (`writing.check_citations`, `writing/__init__.py:138-150`, logic in
`writing/citation_check.py`)
- Loads the `Documents` row; `None` if it doesn't exist (route turns this into 404).
- Pulls the project's live reference set via `references.list_references` (recomputed, not cached).
- Two independent passes over `row.body`, concatenated:
  - **Missing** (`citation_check.py:43-49`, no LLM): regex `\\cite\{([^}]*)\}`, splits each match
    on `,` for multi-key `\cite{a,b}`, flags any key not in the known `cite_key` set.
  - **Unsupported** (`citation_check.py:52-65`): strips LaTeX commands with a blunt regex
    (`\\[a-zA-Z]+(\{[^}]*\})?` → space), truncates to 20,000 chars, and asks
    `llm.complete_structured` (tier `"auxiliary"`, 60s timeout) to list up to 5 verbatim
    claim-sentences lacking a citation. Every returned sentence is checked with a plain `in tex`
    substring test — actually against `tex` the raw input in the current code (`sentence in tex`
    at `citation_check.py:65`, not against the stripped `plain` text the LLM was actually shown) —
    dropped silently if it doesn't literally occur. Any `LLMError` subtype or `RuntimeError` from
    the LLM call is caught and swallowed, returning `[]` for that pass rather than failing the
    whole check.
- Results are persisted: `row.citation_findings = [f.model_dump() for f in findings]`, flushed —
  this is the only place `citation_findings` is ever written; nothing updates it automatically on
  save, so it reflects the last time a user explicitly asked for a check, not the current `body`.

### References / autocomplete / BibTeX (`writing/references.py`)
- `list_references`: joins `papers` to `project_papers` for the project, orders by title, and
  derives each `cite_key` deterministically from `surname + year + firstTitleWord` (first author's
  lowercased surname, stripped to letters; first 4+ letter word in the title; `"anon"`/`"paper"`
  fallbacks). Collisions within one call get `a`/`b`/`c` suffixes in title order — stable across
  calls only as long as the underlying paper set and titles don't change.
- `autocomplete_citations`: recomputes the full reference list, then does a plain case-insensitive
  substring match on `cite_key`/`title`/any author against the prefix. Explicitly *not* routed
  through `memory.query_memory` — the docstring at `references.py:54-58` argues an embedding-based
  retrieval path is the wrong tool for a live per-keystroke 1-2 char prefix match.
- `format_bibtex`: always emits `@misc` entries (never `@article`/`@inproceedings`) since paper
  metadata doesn't reliably carry a venue; includes `author`, `year`, and `howpublished = \url{...}`
  only when those fields are present on the paper.

### Asset insert (`writing.insert_asset`, `writing/__init__.py:119-125`)
Pure passthrough to `vault.write_manuscript_asset`, which slugifies the filename, de-dupes it
against `projects/<slug>/manuscript/assets/`, writes the bytes, and returns the vault-relative path
for the caller to inline as `\includegraphics{...}`. No DB row for the asset itself — the `.tex`
source referencing it is the only "index."

### PDF path resolution (`writing.get_compiled_pdf_path`, `writing/__init__.py:104-116`)
Returns `None` if the document has never compiled (`last_compiled_at is None`) or if the file
`file_path.with_suffix(".pdf")` doesn't actually exist on disk under the vault root — otherwise
returns that relative path for the `/pdf` route to serve via `FileResponse`.

---

## Callers & dependents

Everything in `backend/writing/` is reached exclusively through `backend/api/writing.py`, mounted
in `backend/main.py:47,183` (`app.include_router(writing_router, dependencies=[Depends(require_bearer_token)])`)
— every route requires a bearer token. Confirmed live routes and their writing calls:

| Route | Calls |
|---|---|
| `GET /api/projects/{id}/documents` | `writing.list_documents` |
| `POST /api/projects/{id}/documents` | `writing.save_document` (create) |
| `GET /api/projects/{id}/documents/{doc_id}` | `writing.get_document` |
| `PUT /api/projects/{id}/documents/{doc_id}` | `writing.save_document` (update) |
| `GET /api/projects/{id}/documents/{doc_id}/pdf` | `writing.get_compiled_pdf_path` |
| `POST /api/projects/{id}/documents/{doc_id}/assets` | `writing.insert_asset` |
| `POST /api/projects/{id}/documents/{doc_id}/check_citations` | `writing.check_citations` |
| `GET /api/projects/{id}/references` | `writing.autocomplete_citations` |
| `GET /api/projects/{id}/bibtex` | `writing.export_bibtex` |

A grep across the rest of `backend/` (`check_citations`, `save_document`, `autocomplete_citations`,
`export_bibtex`, `insert_asset`) turned up no other call sites — no background job, no other
package, and no scheduled task reaches into `backend/writing/`. There is nothing dead or
unreachable inside the module itself: every public function in `__all__`
(`writing/__init__.py:27-42`) is exercised by exactly one route above.

Dependencies going out: `vault` (document persistence, manuscript dir resolution, asset writes),
`db.models.Documents`/`Paper`, `llm.complete_structured`/`LLMError` (auxiliary tier, for the
unsupported-claim pass only), and the `docker` SDK (Tectonic container).

---

## Open questions / rough edges

- **`citation_check.py:65`** checks `sentence in tex` (the raw, LaTeX-laden source) rather than
  `sentence in plain` (the stripped text the LLM actually saw and quoted from). Since the LLM is
  prompted against `plain`, a genuinely verbatim claim sentence can still get dropped here if any
  LaTeX markup happened to sit inside the quoted span before stripping — the substring check is
  validating against the wrong string relative to what was shown to the model.
- **`citation_findings` goes stale silently.** It's written only inside `check_citations`, never
  invalidated or recomputed on `save_document`. A user can edit a document, and its persisted
  `citation_findings` will keep describing the pre-edit text indefinitely until the check endpoint
  is called again — the `Document` API response has no field indicating whether the findings are
  current.
- **Route/docstring mismatch**: `backend/api/writing.py:1-4`'s module docstring says
  `GET .../documents/bibtex`, but the actual route is `GET /api/projects/{project_id}/bibtex`
  (`writing.py:104`) — no `documents/` in the path.
- **No engine-mismatch guard.** `save_document` accepts any `document_id` with `engine="tectonic"`
  even on a document whose last successful compile used `"swiftlatex"` (or none) — nothing checks
  the CHECK-constraint's implied lifecycle, it's purely "did *this* compile succeed."
- **Reference cite_key stability is only per-call.** `list_references` recomputes tie-break
  suffixes (`a`/`b`/`c`) from scratch every call in title order; if a new paper is added whose
  title sorts before an existing one with the same base key, the *existing* paper's suffix can
  shift between calls — a previously exported `.bib` key could stop matching a freshly
  autocompleted one for the same paper.
- **Tectonic image absence is swallowed into a compile failure**, not surfaced as a distinct
  "misconfigured server" error — `docker.errors.ImageNotFound` produces the same `CompileResult`
  shape as a genuine LaTeX syntax error, so the editor's compile-error panel can't distinguish
  "your document is broken" from "the ops setup is broken."
- **`insert_asset` route ignores `document_id`.** The path parameter `document_id` in
  `POST .../documents/{document_id}/assets` (`api/writing.py:79-86`) is accepted but never passed
  to `writing.insert_asset`, which only takes `project_id`/`filename`/`content` — assets land in
  the project's single shared `manuscript/assets/` folder regardless of which document the route
  was called against, since Manuscript is (per the module docstring) one `.tex` tree per project's
  manuscript directory rather than per-document.
