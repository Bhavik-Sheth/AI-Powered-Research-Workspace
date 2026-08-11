# Conversations — Design & Architecture

`backend/conversations/` is a single 33-line file (`backend/conversations/__init__.py`) with one job: given a `project_id`, find that project's most recent `conversations` row and return its `messages` rows in order. It is the read side of the transcript only — creating a conversation row and writing messages both happen elsewhere.

---

## Storage / data model

Two tables, defined in `backend/db/models.py`, both created by `backend/alembic/versions/0005_phase1_highlights_conversations_messages.py`.

**`conversations`** (`db/models.py:386-397`):
- `id` (uuid pk), `project_id` (fk → `projects.id`, cascade delete)
- `title: str | None`
- `summary: str | None`
- `summarised_through_seq: int | None`
- `last_message_at: datetime | None`
- `created_at`

**`messages`** (`db/models.py:400-421`) — the verbatim transcript:
- `id`, `conversation_id` (fk → `conversations.id`, cascade delete), `seq` (unique per `conversation_id`), `turn_id`
- `role` — check-constrained to `user | assistant | tool_call | tool_result`
- `content`, `tool_name`, `result_id` (fk → `result_store.result_id`, `SET NULL` on delete), `citations` (JSONB, default `[]`), `interrupted` (bool), `input_modality` — check-constrained to `text | voice`, `created_at`

Of `conversations`'s five non-key columns, only `project_id` and `created_at` are ever populated or read anywhere in the codebase (see below). `title`, `summary`, `summarised_through_seq`, and `last_message_at` are never assigned a value by any code path — `grep` for `.title`, `.summary` (write), `summarised_through_seq`, and `last_message_at` outside migrations/model turns up nothing that sets them. `conversations.summary` is read once, in `backend/memory/__init__.py:125-139`, but never written.

---

## Core mechanics

**`backend/conversations/__init__.py`** exposes:

- `_latest_conversation_id(session, project_id)` (`conversations/__init__.py:17-20`) — `SELECT conversations.id WHERE project_id = :pid ORDER BY created_at DESC` and takes the first (via `session.scalar`, so only the newest row is used — no explicit `LIMIT 1` needed, `scalar()` on a multi-row result just takes the first).
- `list_messages(session, project_id)` (`conversations/__init__.py:23-32`) — calls `_latest_conversation_id`; if `None`, returns `[]` (no conversation yet is legal, not an error). Otherwise `SELECT * FROM messages WHERE conversation_id = :cid ORDER BY seq`, returned as a plain list of ORM rows.

That is the entire module: one lookup query, one ordered fetch, no writes, no side effects, no caching.

**Read path (live):** `GET /api/projects/{project_id}/conversation` (`backend/api/conversations.py:32-49`) opens a `db.session()`, calls `conversations.list_messages`, and maps each `Messages` row into a `MessageOut` Pydantic model (`id`, `role`, `content`, `citations`, `interrupted`, `input_modality`, `created_at`), wrapped in `ConversationResponse`.

**Write path (lives entirely outside this module):**
- Conversation row creation/lookup: `backend/ws/__init__.py:84-94`, `_get_or_create_conversation` — runs the identical `SELECT ... ORDER BY created_at DESC` query as `_latest_conversation_id` (duplicated, not shared code), and if no row exists, inserts a bare `Conversations(project_id=project_id)` (all optional columns left `None`). Called from `handle_connect` (`ws/__init__.py:115`) on every new WebSocket session.
- Message rows: written directly by `backend/harness/__init__.py` at four points in `run_turn` — the user message (`harness/__init__.py:428`), a `tool_call` row and a `tool_result` row per dispatched tool call (`harness/__init__.py:532`, `544`), and the final `assistant` row (`harness/__init__.py:587`). `seq` values come from a harness-local `_next_seq` helper, not from anything in `conversations/`.

So `backend/conversations/` never creates a conversation and never writes a message — it is purely a project_id → latest-conversation_id → ordered-messages read, used once per `GET /api/projects/{id}/conversation` call.

---

## Callers & dependents

**Live:**
- `backend/api/conversations.py:11,35` imports `conversations` and calls `list_messages` inside the `GET /api/projects/{project_id}/conversation` route handler — this is the only caller of the module's public function, and the route is registered normally (no evidence it's excluded from the app).
- `backend/db/models.py` defines the `Conversations`/`Messages` ORM classes this module reads.

**Related but not part of this module (found while tracing dependents):**
- `backend/ws/__init__.py:84-94` (`_get_or_create_conversation`) duplicates `conversations/__init__.py`'s "find latest conversation for a project" query rather than importing it, per this module's own docstring, which frames itself as read-only counterpart to that lookup, not a shared implementation. Live — called from `handle_connect` on every WS connect.
- `backend/harness/__init__.py` writes `Messages` rows across a turn — live, this is what populates the table `list_messages` reads back.
- `backend/memory/__init__.py:119-157`, `_chunk_conversation_summary` — reads `conversation.summary` to build memory chunks with `source_type="conversation_summary"`. This function is reachable code (called from `memory`'s job dispatcher when `source_type == "conversation_summary"`), but since nothing in the codebase ever sets `conversations.summary`, the guard `if conversation is None or not conversation.summary: return` (`memory/__init__.py:125`) is always true in practice — the path is a no-op today. This is a `backend/memory/` finding, not a `backend/conversations/` one, but it directly concerns the `summary` column this module's table owns.

**Dead/unused inside `conversations`'s own table:** the `title`, `summary`, `summarised_through_seq`, and `last_message_at` columns on `conversations` — schema exists (migration + ORM model), nothing populates them, and only `summary` is even read (by `memory/`, not by this module).

---

## Open questions / rough edges

- **Duplicated query logic.** `_latest_conversation_id` (`conversations/__init__.py:17-20`) and `_get_or_create_conversation`'s lookup half (`ws/__init__.py:86-88`) are the same `SELECT ... ORDER BY created_at DESC` written twice in two modules, acknowledged in this module's own docstring as intentional (write side needs create-if-missing, read side must not create) — but the query itself is still copy-pasted rather than shared.
- **No `LIMIT 1` guard.** Both queries rely on `session.scalar()` implicitly taking the first row of a potentially multi-row result rather than expressing "one conversation per project" as a `LIMIT 1` or a DB constraint. Nothing in the schema prevents a project from ever accumulating more than one `conversations` row (there's no unique constraint on `project_id`), so the "one conversation per project" invariant is enforced only by convention (`_get_or_create_conversation` always reusing the latest) — a bug or race that inserts a second row for the same project wouldn't be caught by the schema, only by this ordering convention silently picking the newest.
- **Four unused columns.** `title`, `summary`, `summarised_through_seq`, `last_message_at` are fully modeled (including a `conversation_summary`-specific consumer wired up in `memory/`) but never written anywhere, making the summarization/titling feature entirely inert — the schema anticipates it, nothing produces it.
- **No pagination, no filtering.** `list_messages` returns every message in the latest conversation unconditionally — no limit, no cursor, no way to fetch an older conversation if a project ever ends up with more than one.
