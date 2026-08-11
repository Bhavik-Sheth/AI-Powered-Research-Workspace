# Provenance — Design & Architecture

Provenance turns a claimed quote into a durable, database-backed `quote_anchors` row: given a
paper id, a quote, and optional prefix/suffix context, it fuzzy-locates the quote in the paper's
parsed full text, disambiguates between repeated occurrences using the surrounding context, and
writes the winning span as an anchor. Every consumer that needs to prove a quote is real —
highlights, extracted paper-card fields, matrix cells, and inline chat citations — calls through
this one function (`validate_and_anchor`) rather than re-implementing substring or fuzzy matching
itself.

---

## Storage / data model

`quote_anchors` (`backend/db/models.py:228`, `QuoteAnchors`):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK | server-generated (`gen_random_uuid()`) |
| `paper_id` | UUID, FK → `papers.id`, `ON DELETE CASCADE` | |
| `quote` | String, not null | the literal quote text |
| `prefix` / `suffix` | String, not null | caller-supplied disambiguation context (can be `""`) |
| `char_start` / `char_end` | Integer, not null | offsets into `paper_content.full_text`; check constraints enforce `char_start >= 0` and `char_end > char_start` (`db/models.py:234-235`) |
| `section_heading` | String, nullable | resolved from `paper_content.sections`, or `None` if no section contains the offset |
| `page_hint` | Integer, nullable | column exists (`db/models.py:248`, migration `0004_phase1_papers_and_related.py:80`) but **nothing in the codebase ever sets it** — always `NULL` |
| `bbox_hint` | JSONB, nullable | same story — column exists (`db/models.py:249`, migration `:81`), never written |
| `validated_at` | TIMESTAMPTZ, not null | set once at anchor creation, never updated |

No unique constraint on `(paper_id, quote, char_start, char_end)` — calling `validate_and_anchor`
twice with the same inputs inserts a second identical row rather than reusing the first. There is
no update or delete path anywhere in the module; anchors are write-once, append-only.

Three tables carry a `quote_anchors.id` foreign key, all `ON DELETE CASCADE`:
- `paper_cards.anchor_id` (`db/models.py:273-275`)
- `matrix_cells.anchor_id` (`db/models.py:379`)
- one more nullable FK at `db/models.py:613` (highlights, per `vault/__init__.py`'s reference to
  Provenance)

`QuoteAnchor` (`backend/provenance/models.py:14`) is the Pydantic wire/return shape — same fields
as the table, used both as the function return type and as the `GET /api/anchors/:id` response
model. `CharSpan` (`provenance/models.py:9`) is just `{start: int, end: int}`, the locator's return
type.

---

## Core mechanics

### The fuzzy locator (`provenance/locator.py`)

`_normalize_with_positions(text)` (`locator.py:12`) builds a normalized string alongside a
parallel `positions` array mapping each normalized character back to its original index. Three
rewrites happen during normalization:
1. **Ligatures** (`ﬁﬂﬀﬃﬄﬆﬅ`) expand to their letter sequences (`fi`, `fl`, …) — each expanded
   character maps back to the *same* original ligature position (`locator.py:20-24`).
2. **Line-wrap hyphens**: a `-` immediately followed by whitespace is dropped, along with all the
   whitespace that follows it — treated as a PDF line-wrap artifact (`"atten-\ntion"` → `"attention"`),
   not a real hyphen (`locator.py:26-33`).
3. **Whitespace runs** (including newlines) collapse to a single space, mapped back to the start
   of the run (`locator.py:34-41`).

`locate(quote, text_stream)` (`locator.py:48`) normalizes both quote and text, does one
`str.find`, and — if found — maps the normalized match window back to original-text offsets via
`positions[index]` and `positions[index + len - 1] + 1`. Exact matches are a special case of this
(normalization is a superset), so there's no separate exact-match fast path. Returns `None` if the
quote is empty, normalizes to empty, or isn't found.

`locate_all(quote, text_stream)` (`locator.py:69`) is the same normalization but loops
`str.find(..., search_from)` collecting every occurrence, advancing `search_from` by 1 each time
(so overlapping matches would in principle be found, though the caller doesn't use them that way).

### `validate_and_anchor` (`provenance/__init__.py:35`)

1. Loads `PaperContent` by `paper_id`; returns `None` if the paper has no parsed content row.
2. Calls `locate_all(quote, content.full_text)`. Returns `None` if the quote resolves nowhere.
3. If there are multiple candidate spans (the quote appears more than once), picks the one whose
   surrounding 40-character window (`_CONTEXT_WINDOW = 40`, `__init__.py:17`) best matches the
   caller-supplied `prefix`/`suffix`, scored by `_context_score` (`__init__.py:20`): the last 40
   chars of `prefix` and the actual 40 chars before the span are compared with
   `difflib.SequenceMatcher.ratio()`, same for `suffix` against the first 40 chars after the span,
   and the two ratios are summed. `max(candidates, key=_context_score)` wins — with a single
   candidate this is a no-op tie-break.
4. Resolves `section_heading` via `_section_for_offset` (`__init__.py:28`), a linear scan of
   `content.sections` for the first entry whose `[char_start, char_end)` contains the winning
   span's start offset. `None` if no section matches.
5. Builds a new `QuoteAnchors` row with a fresh UUID and `validated_at = now()`, `session.add`s it,
   `flush`es (so `row.id` and any DB-side defaults are populated, but does not commit — caller's
   transaction owns that), and returns the matching `QuoteAnchor` Pydantic object.

`page_hint` and `bbox_hint` are never set on the row this function builds — both are left at their
column default (`NULL`).

`validate_and_anchor` never raises for the "quote doesn't resolve" case — it returns `None` and the
docstring is explicit that every caller must treat that as "not stated" / mark the citation
unverified, never propagate an exception.

### `get_anchor` (`provenance/__init__.py:83`)

Pure read-back: `session.get(QuoteAnchors, anchor_id)`, `None` if missing, otherwise reshapes the
ORM row into the same `QuoteAnchor` Pydantic object `validate_and_anchor` returns. No other logic.

### `validator.py` — a second, unused validator

`backend/provenance/validator.py` defines `validate_substring(text, quote, char_start, char_end)`
(`validator.py:9`): a pure boolean check that `text[char_start:char_end] == quote` exactly, with
bounds checks (`char_start >= 0`, `char_end <= len(text)`, `char_start < char_end`). Its docstring
calls it "the last gate before a `quote_anchors` row can exist" — but `validate_and_anchor` does
**not** call it; `provenance/__init__.py`'s imports list `db.models`, `provenance.locator`, and
`provenance.models` only, not `provenance.validator`. The only place `validate_substring` is
referenced anywhere in the repo is its own test file
(`tests/test_provenance_substring_validator.py`). Dead code from the application's point of view —
implemented, tested, but never wired into the one path that creates anchors.

---

## Callers & dependents

Everything below was opened and confirmed on a live call path:

- **`backend/api/highlights.py:21`** — `POST /api/projects/{project_id}/highlights`. Calls
  `validate_and_anchor` on the submitted selection; a `None` result becomes a 422. Router is
  registered in `backend/main.py:172` (`highlights_router`).
- **`backend/api/anchors.py:20`** — `GET /api/anchors/{anchor_id}`. Calls `provenance.get_anchor`
  directly; 404 on miss. Router registered in `backend/main.py:179` (`anchors_router`) — this is
  the endpoint the Companion citation-click path resolves an `anchor_id` through.
- **`backend/papers/__init__.py:501`**, inside `extract_card_job` (`papers/__init__.py:439`) —
  after auxiliary-tier LLM extraction of the five standard card fields, each extracted
  `(quote, prefix, suffix)` is run through `validate_and_anchor`; a `None` result silently drops
  that field ("absence of a row *is* 'not stated'"). `extract_card_job` is enqueued from
  `papers/__init__.py:238` and `:339` and registered as a worker job in `jobs/__init__.py:80,90` —
  live.
- **`backend/harness/__init__.py:332`**, inside `_validate_citations` (`harness/__init__.py:313`) —
  called once per `<cite>`/quote-marked span the model emits, tried against every paper id in
  `paper_ids` in turn until one resolves; called from `run_turn` at `harness/__init__.py:584`, i.e.
  every chat turn's response before it reaches the client. This is the path that turns unverifiable
  model quotes into `<unverified>` spans instead of silently trusting the model.
- **`backend/matrix/__init__.py:132`**, inside `_run_scoped_extraction` (`matrix/__init__.py:108`) —
  a custom matrix column's per-paper LLM extraction is anchored the same way before a
  `matrix_cells` row is written; called from `get_matrix_view` (`matrix/__init__.py:185`) whenever
  a `"custom"`-kind column has no cached cell yet.
- **`backend/vault/__init__.py:277`** references `validate_and_anchor` only in a comment ("Vault
  assumes anchoring already happened") — Vault Writer does not call Provenance itself; the calling
  order is `api/highlights.py` anchors first, then hands the resulting `anchor_id` to
  `vault.write_highlight`.

**Dead / unreachable:** `provenance/validator.py`'s `validate_substring` — implemented, tested, but
never invoked by `validate_and_anchor` or any other application code; it's an orphaned second
validator that isn't wired into the one path that creates anchors.

---

## Open questions / rough edges

- **`page_hint` / `bbox_hint` are schema-complete but write-dead.** Both columns exist with full
  migration and model support, `QuoteAnchor` even carries `page_hint` in its Pydantic shape, but
  `validate_and_anchor` never populates either — every anchor's `page_hint`/`bbox_hint` is `NULL`
  forever. Any UI feature depending on a page number or bounding box to render a highlight
  precisely has no data to read.
- **`validator.py`'s `validate_substring` is unreferenced from the anchor-creation path** despite
  its docstring claiming to be "the last gate before a `quote_anchors` row can exist." Either it's
  meant to be called and isn't, or it's a leftover from an earlier design that `locate_all`'s
  normalization superseded. As written, nothing in `validate_and_anchor` performs an *exact*
  substring check — a normalized fuzzy match is the only bar a quote has to clear.
- **No de-duplication of anchors.** Calling `validate_and_anchor` twice with identical
  `(paper_id, quote, prefix, suffix)` — e.g. re-running `extract_card_job` for a paper — inserts a
  second `quote_anchors` row rather than reusing the first. There's no unique constraint and no
  lookup-before-insert.
- **Context-score tie-break degenerates with empty context.** `_context_score` always picks the
  best-scoring candidate even if both `prefix` and `suffix` ratios are 0 — which is exactly what
  `harness/__init__.py:332` passes (`""`, `""`) for chat citations. In that case every candidate
  ties at score 0.0, so `max()` just returns whichever candidate `locate_all` happened to find
  first (document order). For a quote that repeats, the chat-citation path can't meaningfully
  disambiguate; `papers/__init__.py:501` and `matrix/__init__.py:132` do pass real prefix/suffix
  and so can.
- **`locate_all`'s overlap-scanning step (`search_from = index + 1`)** can produce overlapping
  spans for a quote that stutters (e.g. repeated short phrases) — not exercised by any current
  caller, but a pathological quote would yield overlapping candidates rather than a maximal
  non-overlapping set.
- **`section_heading` resolution silently returns `None` on any gap in `sections`.**
  `_section_for_offset` does a first-match linear scan; if `content.sections` has a gap (an offset
  falling between two sections) or a malformed entry missing `char_start`/`char_end` (defaulted to
  `-1`, which never matches), the anchor is still created successfully, just with
  `section_heading = None` — there's no signal distinguishing "no section metadata available" from
  "offset legitimately outside any section."
