# Research Feed — Design & Architecture

Research Feed is a scheduled, per-project background poller: once a day (and weekly for profile reconciliation), it fetches new papers from arXiv/OpenAlex/Semantic Scholar by category, scores them deterministically against a project's editable interest profile, folds in a corpus-centroid cosine and a cross-encoder rerank, dedupes against a persistent seen-set, and writes surviving candidates to `feed_items` for the user to save or dismiss via the API. Nothing in the scoring path calls an LLM; the only LLM calls in the module extract/reconcile the interest profile itself (`categories`/`keywords`), never rank an item.

---

## Storage / data model

**`projects` table** (`db/models.py:94`) carries two feed-owned columns directly on the project row rather than a side table:
- `interest_profile` (JSONB, default `{"categories":[],"keywords":[]}`, `db/models.py:108`) — the `{categories, keywords}` shape.
- `corpus_centroid` (`Vector(EMBEDDING_DIM)`, nullable, `db/models.py:111`) — mean of the project library's abstract-chunk embeddings; `NULL` until the library has at least one chunked abstract.
- `focus_seed` (nullable string, `db/models.py:107`) — the free-text project description the profile is lazily seeded from.

**`feed_items`** (`db/models.py:642`) — one surfaced candidate paper per project per poll:
- `id`, `project_id` (FK, cascade delete), `canonical_id` (string, **no FK** — deliberately not yet a `papers` row until saved), `title`, `metadata` (JSONB: source ids, abstract, venue, source_url, pdf_url, published_at), `score` (float), `why_relevant` (JSONB), `state` (`new`/`saved`/`dismissed` via CHECK), `polled_at`.
- Unique on `(project_id, canonical_id)`.

**`seen_set`** (`db/models.py:664`) — the dedup ledger, composite PK `(project_id, canonical_id, reason)`, `reason` CHECK-constrained to `read`, `library`, `surfaced`, `dismissed`. No FK on `canonical_id` for the same reason as `feed_items`.

**Wire/value models** (`feed/models.py`):
- `RawFeedHit` — one source's un-deduped hit for one category: `source_ids`, `category`, `title`, `abstract`, `venue`, `source_url`, `pdf_url`, `published_at`. A local shape distinct from `search.RawHit` (Feed does not depend on Search Federation).
- `InterestProfile` — `{categories: list[str], keywords: list[str]}`.
- `WhyRelevant` — `{matched_keywords, matched_categories, similarity}`; every `FeedItem` carries one, and by construction (`feed/__init__.py:229`) an item with none of these non-empty never reaches `feed_items`.
- `FeedItem` — the row shape returned to callers.

## Core mechanics

### Interest profile: read, seed, edit
`get_interest_profile` (`feed/__init__.py:81`) loads `project.interest_profile`. If it's still the untouched empty default **and** `project.focus_seed` is set, it lazily seeds the profile by calling `complete_structured` (auxiliary tier, 20s timeout) with `_SEED_PROMPT` (`feed/__init__.py:52`), asking for arXiv-style category codes plus keywords/synonyms, and persists the result back onto the project row (`feed/__init__.py:89-93`). Any LLM failure (`LLMError`/`RuntimeError`) is swallowed and returns an empty `InterestProfile()` rather than failing the read. Every later read is a no-op once real content is stored. `update_interest_profile` (`feed/__init__.py:97`) is a direct overwrite (user edits via `PUT /api/projects/:id/interest-profile`).

### Poll: `poll_feed_job` (`feed/__init__.py:206`)
Runs as a SAQ job, never in a live request path.
1. Loads the profile and the project's current `corpus_centroid`. Returns immediately if the profile has no categories.
2. `_fetch_all_categories` (`feed/__init__.py:106`) fans out, per category, to all three source fetchers in parallel (`asyncio.gather(..., return_exceptions=True)`) — a failing source/category pair silently drops its hits rather than failing the whole poll.
3. `_dedupe` (`feed/__init__.py:119`) keeps the first hit per `resolve_canonical_id(hit.source_ids)` (from `papers.resolve_canonical_id`); a hit with no resolvable id is dropped.
4. `db.filter_unseen` (`db/__init__.py:241`, anti-join against `seen_set` **and** `project_papers`/`papers.canonical_id`) filters out anything already surfaced, saved to the library, read, or dismissed for this project. `_select_unseen` (`feed/__init__.py:132`) re-keys the result back onto the deduped hit dict.
5. `score_candidate` (`feed/__init__.py:143`) computes `score = len(matched_keywords) + len(matched_categories) + similarity` by lower-cased substring match of each profile keyword against `title\nabstract`, plus exact match of `hit.category` against `profile.categories`. Candidates matching neither are dropped (never rendered without a reason).
6. `_add_centroid_similarity` (`feed/__init__.py:166`) is a no-op if `corpus_centroid` is `NULL`; otherwise it embeds each surviving candidate's `title\nabstract` via `memory.embedder.embed` (the one shared fixed embedding model — reused directly per the module's own docstring, not duplicated) and adds cosine similarity against the centroid to the running score.
7. `_rerank_top` (`feed/__init__.py:180`) sorts by score so far, cross-encoder-reranks only the top `_RERANK_TOP_N=50` (query = profile keywords, falling back to categories) against `f"{title}\n{abstract}"` using `feed/reranker.py`'s own lazy-loaded `cross-encoder/ms-marco-MiniLM-L-6-v2` instance (a deliberate duplicate of `search/reranker.py` and `memory/reranker.py`'s copies, per MODULES.md boundary — Feed must not depend on Search Federation or reach into Memory Index's internals). Tail beyond the top 50 is appended unranked, un-boosted.
8. Writes one `FeedItems` row (`state="new"`) plus one `SeenSet` row (`reason="surfaced"`) per surviving candidate.

Source fetchers (`feed/sources.py`) are category-driven, not keyword-driven, windowed by `since`:
- `fetch_arxiv_by_category` — `cat:` + `submittedDate:[from TO to]` query against `export.arxiv.org/api/query`, sorted by `submittedDate` descending, XML Atom parsing.
- `fetch_openalex_by_category` — free-text `search` on the category name + `from_publication_date` filter, `api.openalex.org/works`.
- `fetch_s2_by_category` — free-text `query` on the category name against `/graph/v1/paper/search`; since that endpoint has no documented server-side date filter, results are windowed client-side (`if published_at < since: continue`).

### Actioning items
`get_feed` (`feed/__init__.py:268`) returns `state='new'` rows, best score first.

`save_item` (`feed/__init__.py:308`) — looks up the `FeedItems` row (404 via `ValueError` if missing/wrong project), calls `papers.add_paper` then `projects.add_paper_to_project` (both confirmed live functions: `papers/__init__.py:149`, `projects/__init__.py:46`) to add it to the library, upserts a `SeenSet(reason="library")` row (`ON CONFLICT DO NOTHING`), sets `item.state = "saved"`, and recomputes `project.corpus_centroid` via `_recompute_corpus_centroid` (`feed/__init__.py:289` — mean of `PaperChunks.embedding` for `source_type="abstract"` chunks across the project's papers, one vector per paper; `None` if the library has no chunked abstract yet). Idempotent — re-saving is a harmless no-op path (upsert + same state).

`dismiss_item` (`feed/__init__.py:330`) — sets `state = "dismissed"`, upserts `SeenSet(reason="dismissed")`. Same idempotency pattern.

### Weekly reconciliation: `interest_profile_reextract_job` (`feed/__init__.py:351`)
Pulls every `(title, abstract)` in the project's current library, concatenates to a corpus string (capped at `_MAX_REEXTRACT_CHARS=40_000` chars), and if non-empty, calls `complete_structured` with `_REEXTRACT_PROMPT` to re-derive categories/keywords from what the library has actually grown into. Result is **unioned** (`set(...) | set(...)`, sorted) into the existing profile — additive, so a manual user edit is never silently discarded, and it's a no-op on LLM failure (keeps the existing profile).

## Callers & dependents

All call paths found are live:

- **`backend/jobs/__init__.py`** — `poll_feed_job` and `interest_profile_reextract_job` are both registered SAQ job functions (`_job_functions()`, line 75-98) and both have `scheduled_jobs` cadence entries: `feed_poll` daily (`_FEED_POLL_INTERVAL_S = 86400`, initial lookback 14 days) and `interest_profile_reextract` weekly (`_INTEREST_PROFILE_REEXTRACT_INTERVAL_S`). `run_catchup_pass()` (called from `main.py:136` at app startup) ensures every project has a `scheduled_jobs` row per kind and enqueues anything overdue — confirmed reachable from boot.
- **`backend/api/feed.py`** — `GET/PUT /api/projects/:id/interest-profile` (calls `feed.get_interest_profile`/`update_interest_profile`) and `GET/POST /api/projects/:id/feed` (calls `feed.get_feed`, `feed.save_item`, `feed.dismiss_item`). Router is mounted in `main.py:184` (`app.include_router(feed_router, ...)`), behind bearer-token auth — live.
- **`backend/api/projects.py`** — the project dashboard summary route (`import feed`, line 11) calls `feed.get_feed` (line 132) purely as a read-only projection to compute `DashboardStat(total=len(feed_items), qualifier=...)` and a "new since {weekday}" qualifier (line 216). No writes; read-only dependent, live.
- **`memory.embedder.embed`** — reused directly by `_add_centroid_similarity`, not wrapped; confirmed as the intended reach-through per the module's own header docstring.
- **`papers.add_paper` / `papers.resolve_canonical_id` / `projects.add_paper_to_project`** — all confirmed to exist and match the call signatures used (`papers/__init__.py:99,149`, `projects/__init__.py:46`).

Nothing found in this module is dead code or a stub — every function defined has at least one live caller.

One related rough edge found while tracing `seen_set`, not in feed's own code: `seen_set.reason` is CHECK-constrained to include `'read'` (`db/models.py:671`), but grepping the whole backend for any write of `SeenSet(..., reason="read")` (or equivalent) outside `feed/__init__.py` turns up nothing — no module ever inserts a `read` row, so that reason value is schema-legal but never produced.

## Open questions / rough edges

- **Deterministic score has no fixed scale.** `score_candidate` sums a keyword-match count, a category-match count (0 or 1), a cosine similarity (roughly [-1,1] but centroid is `NULL` for new libraries), and a cross-encoder logit (unbounded, can be large negative or positive). These are added directly with no normalization, so the eventual `FeedItems.score` ordering is dominated by whichever term happens to have the largest magnitude for a given item — e.g. a project with many keyword hits but a `NULL` centroid ranks purely on integer keyword+category counts plus rerank logits, not comparable across projects or across polls where the centroid appears/disappears.
- **`_rerank_top`'s tail is not actually reranked but is still returned and stored.** Anything beyond the top 50 by pre-rerank score keeps its pre-rerank score verbatim and is inserted into `feed_items` unchanged (`feed/__init__.py:192`) — so a feed with more than 50 keyword/category-matching candidates in one poll will silently store dozens of items whose score never saw the cross-encoder, mixed into the same `ORDER BY score DESC` list as reranked ones.
- **`seen_set.reason = 'read'` is schema-legal but write-unreachable** anywhere in the current codebase (see above) — either a planned-but-unbuilt "mark as read without saving" action, or a stale constraint value.
- **OpenAlex hits never carry an abstract.** `fetch_openalex_by_category` (`feed/sources.py:80-111`) hard-codes `abstract=None` for every hit (OpenAlex's inverted-index abstract format is not parsed at all), so OpenAlex-sourced candidates are permanently disadvantaged in `score_candidate`'s keyword match (only the title is searchable) and in the centroid/rerank steps (embedding/rerank text is just the title with an empty second line).
- **Semantic Scholar's category fetch degrades to a bare keyword search.** `fetch_s2_by_category` (`feed/sources.py:114`) sends the arXiv-style category code itself (e.g. `cs.CL`) as the free-text `query` param — the code's own docstring flags this as a deliberate compromise (no verified category-code mapping exists for that endpoint), but it means S2 results for a category are only as good as full-text-matching the literal code string, not a real subject filter.
- **`_recompute_corpus_centroid` runs fully synchronously inside `save_item`**, scanning all `PaperChunks` for the project's whole library on every single save — no caching or incremental update; a large library means every save pays an O(library size) chunk scan plus a Python-side mean over embedding vectors.
- **No cap on `feed_items` growth.** Nothing in the module prunes or expires old `state='new'` rows — a project that never acts on its feed accumulates every surfaced item indefinitely, and `get_feed` returns the entire unbounded set ordered by score.
