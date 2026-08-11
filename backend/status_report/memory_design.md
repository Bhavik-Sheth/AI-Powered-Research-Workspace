# Memory System — Design & Architecture

Memory = 2 Postgres tables (`paper_chunks`, `project_chunks`), fixed local embedder + reranker, retrieved via a hybrid SQL union only when the harness explicitly asks. Nothing about this is automatic — it's a pull, not a push, into context.

---

## Storage — where project knowledge lives

**Two tables, no more:**

| Table | Scope | What goes in it | `source_type` values |
|---|---|---|---|
| `paper_chunks` | Global (no `project_id` — scoped via `project_papers` join at query time) | Paper abstracts + sections | `abstract`, `paper_section` |
| `project_chunks` | Per-project (`project_id` column) | Notes, experiments, conversation summaries | `note`, `experiment`, `conversation_summary` |

Both tables have identical shape: `text`, `embedding` (768-dim vector), `tsv` (generated tsvector column for lexical search), `char_span` (offsets back into the source artifact), plus a `source_id` pointing at the real row. Every chunk is traceable back to something real — nothing is stored that didn't come from a genuine artifact.

**How a chunk gets there** (`memory/__init__.py`, `chunk_and_embed_job`):
1. Text is split by `chunking.split_span` — 1600-char budget, 200-char overlap, breaks on whitespace (not a real tokenizer, just `~4 chars/token`).
2. Each span is embedded with `Alibaba-NLP/gte-modernbert-base` (fixed, local, `sentence-transformers`, never a provider call) — `embedder.py`.
3. Rows are inserted directly, one per span.

**Who triggers it — and this is the important part:**
- Paper abstract/sections: `papers.embed_paper_job` calls `chunk_and_embed_job` directly, as part of the paper ingest pipeline.
- Notes: `api/notes.py` enqueues `chunk_and_embed_job` on save.
- Experiments: **`chunk_and_embed_job` raises `NotImplementedError`** for `source_type="experiment"` — not wired up despite the DB check-constraint allowing it.
- Conversation summaries: `_chunk_conversation_summary` is a legal no-op until `conversations.summary` is set — **and nothing in the codebase ever sets `conversations.summary`**. So this path exists in code but is dead; past conversations never enter memory.

**No re-indexing path.** Re-running the job for the same artifact just appends duplicate chunk rows — there's no update/delete-then-reinsert.

---

## Retrieval — how it comes back out

`memory.query_memory(project_id, query, types=None)`:

1. Embed the query text (same fixed model).
2. `db.hybrid_retrieve` (`db/__init__.py:122`) — two independent SQL arms:
   - **Arm A** (`paper_chunks`): joined through `project_papers` to scope to this project's papers.
   - **Arm B** (`project_chunks`): scoped directly by `project_id`.
   - Each arm runs dense (pgvector cosine `<=>`) and lexical (`tsvector` + `plainto_tsquery`) search independently, fuses them with **reciprocal rank fusion** (`1/(60+rank)`), and returns up to 50 candidates.
   - The two arms are then merged in Python (not SQL) and truncated to 40 for reranking.
3. Cross-encoder rerank (`cross-encoder/ms-marco-MiniLM-L-6-v2`, also fixed/local) scores all 40 candidates against the query, sorted descending.
4. Returns `CitedRow[]` — text + source pointer, no filtering by score threshold, no cap on the final list size beyond whatever was reranked.

**Who calls `query_memory` today — and this is the gap that matters for context-assembly planning:**
`harness._maybe_retrieve` (`harness/__init__.py:172`) is the *only* caller in the live turn path. It's a **pre-turn gate**, not a tool:
- One `complete_structured` call (cheap/auxiliary tier) decides yes/no + generates a search query, based only on the user's raw message text (not the accumulating tool-loop state).
- Runs once, before the iteration loop starts.
- If it errors, memory is silently skipped for the turn (no memory beats failing the turn).
- The agent **cannot** decide mid-loop "actually I need to search now" — that decision is made and locked in before the loop begins, and it's not in `TOOL_SCHEMAS` at all.

---

## Implications for context assembly

- Ambient context per turn currently = system prompt + selection + **direct DB reads** of open/selected papers (`_paper_evidence`, bypassing the chunk/embedding path entirely and reading raw `paper_content.full_text` or extracted card fields) + memory rows from the one pre-turn gate.
- So there are actually **two parallel retrieval paths** feeding one turn: raw evidence pull for open papers (deterministic, not embedding-based) vs. `query_memory` (embedding-based, gated, project-wide). Any redesign needs to account for both, not just the memory one.
- Memory itself has no notion of recency, no token budgeting on the returned rows, and conversation history never joins it — so "what did we discuss last week" is structurally unanswerable today.
- Nothing computes or persists a running token count for the assembled context — sizing only happens defensively at `llm._fit_to_budget`, after everything's already been built.
