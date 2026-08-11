# Paper Pipeline — Design & Architecture

TLDR: `backend/papers/` turns a paper reference (DOI/arXiv/OpenAlex/S2 id, from search or a pasted
link) into a fully-processed global `papers` row, deduped by a derived canonical id. Adding a paper
returns immediately with the row in `fetch_state='queued'`; a chain of six background jobs then runs
open-access-only fetch → docling parse → extractive card (5 fields, LLM + Provenance-anchored) →
code/dataset enrichment (3-tier harvest) → memory embedding → reference tracing — each stage tracked
by its own state column on `papers`, each stage's job enqueuing the next. Every extractive/LLM output
is anchored back to a verbatim quote in the parsed text (`quote_anchors`) before being persisted; an
unstated field is never invented, just absent.

---

## Storage / data model

All in `backend/db/models.py`, referenced by `backend/papers/models.py`'s Pydantic wire shapes.

**`papers`** (`db/models.py:155`) — one row per real-world paper, global, keyed by a unique
`canonical_id`. Fields: `canonical_id_source` (`doi`/`arxiv`/`openalex`/`s2`, check-constrained),
`doi`/`arxiv_id`/`openalex_id`/`s2_id`/`pwc_id`, `title`, `abstract`, `metadata` (JSONB, unused by
this module's code), `source_url`, `pdf_path`, `pdf_origin` (check-constrained to
`arxiv`/`unpaywall`/`s2_oa`/`user_upload` — though nothing in this module ever writes
`user_upload`), and five independent pipeline-state columns, each check-constrained to
`queued`/`running`/`done`/`failed`/`degraded` and defaulting to `queued`:
`fetch_state`, `parse_state`, `embed_state`, `extract_state`, `references_state`.

**`paper_content`** (`db/models.py:210`) — one row per paper (`paper_id` is the PK, `ON DELETE
CASCADE`), the docling parse output: `full_text`, `sections` (JSONB list of `{section_id, heading,
level, char_start, char_end}`), `references` (JSONB, column name `references_` in code, overwritten
by `trace_references_job`), `datasets`/`code_links` (JSONB, empty on parse, filled by
`enrich_paper_job`), `parser_version`, `parsed_at`.

**`quote_anchors`** (`db/models.py:228`) — the shared anchor object backing a card field, a
highlight, or a matrix cell: `quote`, `prefix`, `suffix`, `char_start`/`char_end`
(check-constrained `char_end > char_start`), `section_heading`, `page_hint`, `bbox_hint`,
`validated_at`. Written by `provenance.validate_and_anchor` (not in this module, but called from
`extract_card_job:501` and matrix's `_run_scoped_extraction`).

**`paper_cards`** (`db/models.py:253`) — one row per extracted field, `UNIQUE(paper_id,
field_key)`, `field_key` check-constrained to `problem`/`method`/`datasets`/`results`/
`limitations`. Absence of a row is the encoding for "not stated" — there is no boolean flag for it.
`extracted_by_model` records which model produced it.

**Wire models** (`papers/models.py`): `SourceIds`, `PaperInput` (requires at least one of the four
ids — enforced by `resolve_canonical_id` raising `ValueError`, not by a Pydantic validator),
`Paper` (mirrors the five state columns), `SectionInfo`, `ReferenceInfo` (carries the resolved
`paper_id: uuid.UUID | None` once `trace_references_job` has run), `PaperContentView`,
`PaperCardField`.

---

## Core mechanics — the ingest pipeline

**Canonical id** (`papers/__init__.py:99`, `resolve_canonical_id`) — the one function that derives
it, priority DOI → arXiv → OpenAlex → S2, each normalised (`_normalise_doi` strips the URL/`doi:`
prefix and lowercases; `_normalise_arxiv_id` strips `arxiv:` and any `vN` version suffix). Raises
`ValueError` if none of the four ids is present. The `<source>:` prefix of the result also becomes
`canonical_id_source`.

**1. `add_paper`** (`:149`) — dedupes on canonical id (returns the existing row untouched if found);
otherwise inserts a `papers` row with `fetch_state='queued'`, flushes, and enqueues `fetch_pdf_job`
(90s timeout). Returns immediately — the row comes back to the caller before any network fetch
happens. Comment at `:150` notes this used to run the fetch inline on `POST .../papers`, which could
hang the request up to ~80s; that inline path is gone.

**2. `fetch_pdf_job`** (`:184`, timeout 90s) — sets `fetch_state='running'`, calls `_fetch_pdf`
(`:131`) which is OA-only (invariant #3): `resolve_oa_pdf_url` (`fetch.py:16`) tries, in order, a
known `pdf_url` (origin inferred: `arxiv` if an arXiv id, else `s2_oa` if an S2 id, else
`unpaywall`), then a direct arXiv PDF URL if an arXiv id is present, then Unpaywall's API by DOI
(`best_oa_location.url_for_pdf` or `.url`). No OA copy found → `None`, sets `fetch_state='degraded'`.
A found URL is downloaded (`download_pdf`, 60s timeout, `httpx.HTTPError` also degrades) and written
to the vault via `write_paper_asset`; `pdf_path`/`pdf_origin` are set and `fetch_state='done'`. Only
on `done` does it enqueue `parse_paper_job` (600s timeout).

**3. `parse_paper_job`** (`:308`) — sets `parse_state='running'`, runs `parser.parse_pdf` in a
thread (`asyncio.to_thread`, since docling is sync). `parse_pdf` (`parser.py:54`) drives docling's
`DocumentConverter`, walking `document.iterate_items()`: heading-labeled items
(`TITLE`/`SECTION_HEADER`) open/close `sections` entries with running char offsets; `REFERENCE`-
labeled items are collected directly. If docling emitted zero references (noted at `parser.py:92` as
the common case — "docling rarely emits `DocItemLabel.REFERENCE`"), falls back to
`_split_references_section` (`:25`), which locates the References/Bibliography heading via the
already-computed section bounds and splits its body one entry per line, stripping a leading
`[N]`/`N.` marker if present but not requiring one (handles unnumbered author-year styles).
`parser_version` is `docling.__version__`. Back in the job: a `PaperContent` row is inserted
(`datasets`/`code_links` start `[]`), the parsed JSON is also written to the vault
(`write_paper_asset(..., "parsed", ...)`), `parse_state='done'`, and **three jobs are enqueued in
parallel**: `extract_card_job`, `embed_paper_job`, `trace_references_job` — these three stages do
not depend on each other, only on parse having finished.

**4. `extract_card_job`** (`:440`, timeout 180s) — sets `extract_state='running'`. Splits
`full_text` into bounded, section-aligned windows via `_section_windows` (`:383`, ≤20,000 chars
each, merging adjacent sections until the budget would be exceeded, falling back to fixed-size
chunks of the whole text if docling found no sections). For each window, calls `complete_structured`
(auxiliary LLM tier, 60s timeout) against `_ExtractedCard` (five optional `_ExtractedSpan{quote,
prefix, suffix}` fields) with a strict "verbatim quote only, omit if unstated" system prompt. A
window's LLM call failing (`LLMError` or `RuntimeError`) is logged and skipped, not fatal to the
whole paper; only if *every* window fails does the job raise. First window to state a field wins
(`merged` dict, `:490-491`) — later windows never override an already-filled field. Each merged span
is run through `provenance.validate_and_anchor`; a `None` result (span couldn't be re-located in the
text) silently drops that field. A validated span becomes a `PaperCards` row plus, if its
`field_key` is `datasets` or `method` (`_GRAPH_EDGE_FIELDS`, `:77`), an `LLMEdge` queued for
`write_llm_edges` (dataset/method node, `slugify(quote[:80])` as `dst_id`) — written in a *separate*
DB session after the card/state commit, so an edge-write failure can never roll back cards that
already validated. Sets `extract_state='done'` (or `'failed'` + re-raise on exception, so SAQ's
retry/failure tracking still sees it and the Library UI shows "failed" rather than a stuck
"queued"). Always enqueues `enrich_paper_job` (30s timeout) at the end — even for a paper where card
extraction found nothing usable.

**5. `enrich_paper_job`** (`:656`, timeout 30s, no state column of its own — it feeds
`paper_content.datasets`/`code_links` directly) — three-tier code/dataset harvest, each tier only
tried if the previous found nothing:
  - Tier 1, `_harvest_text_links` (`:554`): regexes the paper's own parsed text for
    github/gitlab/huggingface.co/kaggle.com/zenodo.org URLs, classifying zenodo.org or any
    `/datasets/` path as a dataset, everything else as code.
  - Tier 2, `_harvest_huggingface_links` (`:587`), only if tier 1 found nothing and the paper has an
    `arxiv_id`: calls HuggingFace's papers API (`GET /api/papers/{arxiv_id}`), reading
    `linkedModels`/`linkedDatasets` (capped at 3 each). A non-200 or malformed body degrades to `[],
    []`, never raises.
  - Tier 3, `_harvest_firecrawl_link` (`:623`), only if both prior tiers found nothing: a Firecrawl
    search for `"{title}" official implementation`, keeping the first hit whose URL is on
    github.com/gitlab.com. No configured `firecrawl_api_key` means an immediate `[]`, not a failure.
  - If nothing was found across all three tiers, the job returns without writing anything (fields
    stay `[]`). Otherwise `paper_content.code_links`/`datasets` are set and corresponding
    `has_code`/`uses_dataset` `MetadataEdge`s are written via `write_metadata_edges`.

**6. `embed_paper_job`** (`:424`, timeout 120s) — sets `embed_state='running'`, calls
`memory.chunk_and_embed_job` twice, directly (not re-enqueued as separate jobs — the docstring notes
this avoids a two-job race writing `embed_state`), once for `source_type="abstract"` and once for
`source_type="paper_section"`. Any exception sets `embed_state='failed'` and re-raises; success sets
`'done'`.

**7. `trace_references_job`** (`:760`, timeout 30s) — sets `references_state='running'`. API-first:
tries `fetch_openalex_references` (if `openalex_id` present) for the top `_TOP_REFERENCES=5` by
citation count; on empty/`HTTPError`, falls back to `fetch_s2_references` (if `s2_id` present).
Either source's hits are turned into metadata-only reference-stub `papers` rows via
`add_reference_stub` (`:732` — dedupes on canonical id exactly like `add_paper`, `fetch_state`
hard-set to `'skipped'` so no fetch/parse/extract job is ever enqueued for a stub) and a `cites`
`MetadataEdge` per resolved reference, `source_api` set to whichever API actually resolved it. If
neither API produced hits, falls back to the PDF-parsed `content.references_` entries themselves:
`_extract_ids_from_raw_reference` (`:711`) regex-scans each raw reference string for an inline arXiv
id or DOI (explicit text already in the PDF, not invented) and, if found, stubs it the same way —
this fallback's edges carry `source_api=None` since no API was actually queried. Every trace, either
branch, ends by overwriting `paper_content.references` wholesale with the resolved list (a reference
that resolved to no source id at all keeps its raw text but `paper_id=None`). Sets
`references_state='done'`, or `'failed'` + re-raise on any unhandled exception (the one place, per
the job's own docstring, that a bare `except Exception` is intentional — same pattern as
`embed_paper_job`).

**`reprocess_paper`** (`:206`) — the Library's "Retry"/"Promote" action. Walks the five stages in
order; the first non-terminal (`queued`/`running`/`failed`) stage found is reset to `queued` and
re-enqueued, and the function returns without touching downstream stages (each one assumes its
predecessor already produced output). Once `parse_state='done'`, though, `extract_state`/
`embed_state`/`references_state` are each independently checked and re-enqueued if not terminal —
these three don't have a strict order dependency on each other post-parse.

**`get_paper(..., heal=True)`** (`:251`) — the *only* path with a side effect on a plain read: if
`references_state` is still at its `queued` default (papers that predate the trace feature, or a
race with the enqueue in `parse_paper_job`), it enqueues `trace_references_job` once. Every other
caller passes `heal=False` (the default).

---

## Callers & dependents

All live:

- **`backend/api/papers.py`** — the paper HTTP surface. `POST /api/projects/{id}/papers` →
  `add_paper` (`:33`); `GET /api/papers/{id}` → `get_paper(..., heal=True)` (`:75`, the heal path's
  only entry point) + `get_paper_content`/`get_paper_card` gated on the `include` query param;
  `GET /api/papers/{id}/pdf` → `get_pdf_path`; `POST .../reprocess` and `.../promote` both →
  `reprocess_paper` (`promote` additionally adds project library membership since it's invoked from
  a reference stub inside a reader tab).
- **`backend/harness/tools.py`** — `add_paper` tool (`:149`, `dispatch`) calls
  `papers.add_paper` then `projects.add_paper_to_project`; `open_paper` tool (`:163`) calls
  `papers.get_paper` (note: **`heal=False`** here — a chat-triggered open does not retry a stalled
  reference trace, only the HTTP `GET /api/papers/{id}` route does).
- **`backend/harness/__init__.py`** — reads `get_paper`/`get_paper_card`/`get_paper_content`
  (`:194,214,222,446`) to assemble per-turn evidence for an open/selected paper — the "direct DB
  read" path documented in `memory_design.md` as bypassing the embedding/memory path entirely.
- **`backend/feed/__init__.py`** (`save_item`, `:316`) — saving a discovered feed item calls
  `papers.add_paper` then adds it to the project library, same as the API/tool paths; also imports
  `resolve_canonical_id` directly for dedup against already-seen items.
- **`backend/matrix/__init__.py`** — reads `get_paper_content`/`get_paper`/`get_paper_card` to
  populate the comparison matrix's paper rows and standard-column cells from `paper_cards`;
  `_run_scoped_extraction` reuses the same `validate_and_anchor` pattern as `extract_card_job` for
  custom-column per-paper extraction (a parallel, matrix-scoped extraction path, not part of the
  ingest pipeline itself).
- **`backend/jobs/__init__.py:78-93`** — all six job functions (`fetch_pdf_job`, `parse_paper_job`,
  `extract_card_job`, `enrich_paper_job`, `embed_paper_job`, `trace_references_job`) are imported and
  registered as SAQ task handlers — confirmed live, not orphaned.
- **`backend/search/__init__.py`** and **`backend/search/models.py`/`sources.py`** import
  `resolve_canonical_id`/`SourceIds` from this module for search-result dedup — a one-way dependency
  (search depends on papers; papers' `trace_references_job` and `enrich_paper_job` import back from
  `search.sources`/`search.models` **locally inside the function**, explicitly to avoid a circular
  top-level import — noted in comments at `:630` and `:781`).
- **`backend/feed/sources.py`, `backend/feed/models.py`** — import `SourceIds` only (wire-shape
  reuse, not a functional dependency on the pipeline).
- **`backend/api/projects.py`** — imports `papers`/`Paper` type but only for response typing in
  project-scoped listings (confirmed via `LibraryEntry`/`paper_from_row` reuse in `api/papers.py`;
  `api/projects.py`'s own paper-typed responses follow the same pattern).

Nothing found in this pass is dead code, a stub, or unreachable — every exported function and every
job handler has at least one live caller, and every job is registered with the worker.

---

## Open questions / rough edges

- **`add_reference_stub`'s promoted-stub race is silent.** `promote_reference_stub`
  (`api/papers.py:105`) calls `reprocess_paper` to turn a `fetch_state='skipped'` stub into a real
  paper, but `reprocess_paper`'s branch logic (`:222`) treats any non-terminal `fetch_state` as "go
  fetch" — `'skipped'` is not in `_TERMINAL_STAGE_STATES = ("done", "degraded")`, so this works, but
  there is no explicit state transition documented from `skipped` → `queued`; it falls out of the
  general non-terminal check rather than being named.
- **`pdf_origin='user_upload'`** is a valid DB check-constraint value with no code path in this
  module that ever writes it — `resolve_oa_pdf_url` only ever returns `arxiv`/`unpaywall`/`s2_oa`.
  Either a real upload flow lives elsewhere and was never traced from here, or the constraint value
  is currently unused.
- **`metadata` JSONB column on `papers`** (`db/models.py:195`) is never read or written anywhere in
  `backend/papers/` — present in the schema, silent in the pipeline.
- **`enrich_paper_job` has no state column.** Every other stage (`fetch`/`parse`/`extract`/`embed`/
  `references`) has a dedicated `..._state` field on `papers`, but enrichment's success/failure is
  invisible at the `papers` row level — a partial or fully-failed enrichment looks identical to "ran
  and found nothing" (`code_links`/`datasets` both `[]`), and `reprocess_paper` has no branch for it
  at all, so a stuck/never-run enrichment is not retriable via the Library's "Retry" action the way
  the other five stages are.
- **Tier-1 harvest's dataset/code split is a heuristic, not authoritative.** `_harvest_text_links`
  (`:554`) treats any `huggingface.co/.../datasets/...`-shaped or zenodo.org URL as a dataset and
  everything else on the five allowed domains as code — a huggingface.co Space or model URL that
  happens to contain "datasets" as a path segment for unrelated reasons would misclassify (no such
  case is guarded against).
- **`extract_card_job`'s per-window merge is first-wins, not best-wins.** If an early window states
  a field ambiguously and a later window states it more precisely, the later, better answer is
  silently discarded — no re-ranking or preference logic exists.
- **Graph-edge `dst_id` for text-harvested datasets is a truncated slug of a quote, not a stable
  identifier.** `_GRAPH_EDGE_FIELDS`-driven edges from `extract_card_job` use
  `slugify(span.quote[:80])` — two papers whose datasets field states the same dataset with even
  slightly different wording (or a quote that starts identically but differs after 80 chars) will
  not merge to the same graph node, while `enrich_paper_job`'s HuggingFace-sourced dataset edges use
  the real dataset id and do merge correctly (`_dataset_edge_dst_id`, `:648`) — the two dataset-edge
  paths have different identity semantics for what is conceptually the same relation.
