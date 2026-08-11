# Agent Harness — Design & Architecture

`backend/harness/` is the single-agent tool-calling loop behind every Companion turn: it assembles
context from open papers, highlighted text and memory, streams a typed event sequence to whatever
called it, dispatches model-requested tool calls through its own catalog, structurally re-validates
every citation the model produced against real source text, and persists the whole transcript to
`messages`. It is transport-agnostic (`SessionRef` is an opaque project/conversation handle) and is
driven today by exactly one live caller, `backend/ws/__init__.py`'s WebSocket session handler.

---

## Storage / data model

**`harness/models.py`** — wire and DB-adjacent shapes:

- `SelectionState` (`paper_id` + `QuoteAnchorInput` anchor) and `UIState` (`selection` +
  `open_paper_ids: list[UUID]`, `models.py:22-24`) — the only ambient UI context the harness knows
  about. `activeTab`/`openPanes`/`workingSet` are noted as unshipped.
- `Citation = AnchorCitation | MemoryCitation` (`models.py:27-49`) — `AnchorCitation` points at a
  `quote_anchors` row (clickable, scrolls the reader); `MemoryCitation` points at a `query_memory`
  row id + `source_type` (not clickable, no reader position).
- `TurnEvent` union (`models.py:120`): `StatusEvent`, `TextDeltaEvent`, `ToolCallEvent`,
  `ToolResultEvent`, `UIActionEvent`, `TurnCompleteEvent`, `ErrorEvent`. `ToolResultEvent.model_view`
  is what re-enters the LLM loop; `result_id` (when set) is a `result_store` handle the frontend
  fetches separately — the rich payload never enters context. `TurnCompleteEvent.citations` mirrors
  what just got written to `messages.citations`, since no earlier event on the stream carries it.
  `ErrorEvent` carries `recoverable` and `what_still_worked` for the frontend to render gracefully.

**`harness/tools.py`** — `ToolResult` (`model_view`, `ui_view_result_id`, `ui_actions: list[dict]`,
`tools.py:27-37`) is the dual-channel return shape every dispatched tool produces.

**DB writes** (`db.models.Messages`, confirmed at `backend/db/models.py:400-421`): every turn writes
one `user` row, one `assistant` row, and a `tool_call`/`tool_result` pair per dispatched tool call,
all sharing one `turn_id` and an incrementing `seq` per conversation. `citations` is a JSONB column
holding the same list shape `_validate_citations` returns; `interrupted` records whether the turn's
final answer was cut short. `role` is constrained to exactly `user`/`assistant`/`tool_call`/
`tool_result` — there is no `system` role stored (system context is assembled in memory per turn,
never persisted).

---

## Core mechanics

### Context assembly (`run_turn`, `harness/__init__.py:385-473`)

1. **Duplicate-turn guard** (`_duplicate_turn_id`, `:269-285`): if the conversation's most recent
   row is a `user` message with the *exact same text*, persisted within `_DUPLICATE_TURN_WINDOW_S`
   (5.0s), the call is folded into that turn's id and returns a `TurnCompleteEvent` with no new LLM
   call or persistence — defense-in-depth behind Session Transport's socket-eviction fix.
2. The user message is persisted immediately (`role="user"`), before any LLM call.
3. **Evidence papers**: selected paper (`ui_state.selection.paper_id`) first, then every
   `open_paper_ids` tab, de-duplicated, order preserved (`:438-453`). For each, `_paper_evidence`
   (`:207-227`) prefers the extracted paper card's verbatim quoted fields; falls back to a
   `_PAPER_EXCERPT_CHARS`-bounded (4000 char) excerpt of `paper_content.full_text`; else states no
   text is available. This is a **direct DB read**, not an embedding/chunk-based path.
4. **Memory retrieval** (`_maybe_retrieve`, `:172-184`): one `complete_structured` call
   (`tier="auxiliary"`, 20s timeout) decides yes/no + generates a search query from the raw user
   message text alone (not the accumulating tool-loop state), run once before the iteration loop.
   Any `LLMError`/`RuntimeError` is swallowed — memory is silently skipped rather than failing the
   turn. If yes, `memory.query_memory(project_id, query)` runs once. `query_memory` is not in
   `TOOL_SCHEMAS` — the model can never decide mid-loop to search memory again.
5. System prompt (`_SYSTEM_PROMPT`, `:151-157`) mandates `<cite>` tags around every verbatim quote
   or specific fact. Then, conditionally: highlighted-selection block, evidence blocks, an
   open-papers cross-reference block (only if `len(open_papers) > 1`), a memory-rows block, then
   full conversation history (`_history`, user/assistant rows only — `tool_call`/`tool_result` rows
   are excluded), then the new user message.

### Iteration loop (`:475-582`)

- Hard cap `_MAX_ITERATIONS = 8` (`:85`). Each pass: `yield StatusEvent("thinking…")`, stream
  `complete(llm_messages, tools=TOOL_SCHEMAS, tier="primary", timeout=90s)`, accumulate text deltas
  and tool-call deltas by index. `cancel_flag.is_set()` is checked *between yielded chunks only* — a
  stream that never yields cannot be interrupted here (documented as intentional/known gap).
- `LLMError` or `RuntimeError` (not-configured) during completion ends the turn immediately with an
  `ErrorEvent` + `TurnCompleteEvent(interrupted=False)`, no persistence of a partial assistant
  message.
- If interrupted or no tool call was requested, the loop breaks with `full_text` as the answer.
- Otherwise: the assistant's tool-call message is appended to `llm_messages`, then each tool call is
  dispatched via `_dispatch_tool_bounded` (`:358-382`), which races `dispatch_tool` against both
  `_TOOL_DISPATCH_TIMEOUT_S` (60s) and `cancel_flag` using `asyncio.wait(..., return_when=
  FIRST_COMPLETED)`; whichever loses is cancelled and awaited out in a `finally` so nothing keeps
  running unobserved. A `tool_call` row is written *before* dispatch, a `tool_result` row *after* —
  both inside one `db.session()` transaction with the dispatch itself, so a timeout mid-dispatch
  rolls the pair back together. Timeout or interruption here ends the turn with `ErrorEvent` +
  `TurnCompleteEvent(interrupted=True)` and returns early, skipping citation validation entirely for
  that turn (no assistant row is written).
- On success, `ToolResultEvent` and any `UIActionEvent`s are yielded, and the tool's `model_view` is
  appended back into `llm_messages` as a `role="tool"` message for the next pass.
- If the loop exhausts `_MAX_ITERATIONS` without a tool-call-free final answer (the `while/else`
  clause, `:577-581`), a fixed "reached my step limit" message is substituted only if `full_text` is
  empty — otherwise whatever partial text the last pass produced is used as-is.

### Tool dispatch (`harness/tools.py`)

`TOOL_SCHEMAS` lists 6 tools: `search_papers`, `add_paper`, `open_paper`, `save_note`,
`log_experiment`, `update_experiment` — Discovery/Navigation/Mutations groups only; `query_memory`
is deliberately not a tool (see above). `dispatch` (`tools.py:131-212`) is a single if/elif chain,
one implementation per tool name, each calling into `search`/`papers`/`projects`/`vault`/
`experiments` modules and returning a `ToolResult`. Malformed ids/missing identifiers return a
`model_view` error string rather than raising. Unknown tool name falls through to
`ToolResult(model_view=f"Unknown tool: {tool_name}")` — never an exception.

### Citation validation (`_validate_citations`, `:313-346`)

Runs once, after the loop ends, over the accumulated `full_text` (not per-chunk, not per-iteration).
`_CITE_OR_QUOTE_PATTERN` (`:94-102`) matches both explicit `<cite>...</cite>` spans and untagged
straight/curly-quoted spans ≥20 chars (`_MIN_UNTAGGED_QUOTE_CHARS`) — the system prompt's tagging
discipline is advisory only, so untagged quotes are validated the same way. For each match:
`_strip_tag_markup` first removes any literal `<cite>`/`</cite>`/`<unverified>`/`</unverified>` text
the model itself typed (guards against doubled/quote-wrapped tags corrupting the re-wrap, per the
Phase 3.1 note in the module docstring) from both the captured span and the surrounding text. The
quote is then checked against every `citation_paper_id` (selected paper + open tabs) via
`provenance.validate_and_anchor`, first match wins; if none, checked against `memory_rows` by plain
substring (`_matching_memory_row`, `:288-289`, `quote in row.text`). A hit re-wraps as
`<cite>...</cite>` and appends a structured citation dict (`kind: anchor|memory`); a miss re-wraps as
`<unverified>...</unverified>` with nothing added to the citation list. The cleaned text and
citations list are what gets persisted to `messages.assistant.content`/`.citations`, and text deltas
are re-emitted post-validation (`:598-605`), split on tag boundaries — the frontend never sees raw,
unvalidated model output for the final answer.

### Interrupt / cancellation

- `begin_turn` (`:136-149`) reserves an `asyncio.Event` in the module-level `_in_flight` dict
  **synchronously**, in the same call stack as the message that starts the turn, before
  `asyncio.create_task` schedules `run_turn` — closing a race where an `interrupt` could arrive
  before the task gets a turn on the event loop. Returns `None` (turn refused) if one is already
  in flight for that `(project_id, conversation_id)` key.
- `interrupt` (`:612-617`) just sets the flag if present; a no-op otherwise.
- Cancellation is cooperative, not `Task.cancel()` — checked between streamed completion chunks and
  raced explicitly inside `_dispatch_tool_bounded` during tool dispatch. Partial results are kept:
  an interrupted completion still goes through citation validation and persistence with
  `interrupted=True`.
- The `_in_flight` slot is always released in `run_turn`'s top-level `finally` (`:608-609`),
  covering every early-return path (error, timeout, interruption, normal completion).

---

## Callers & dependents

- **`backend/ws/__init__.py`** — the only live caller. `handle_message` (`:163-195`) calls
  `harness.begin_turn` synchronously then spawns `harness.run_turn` as a background `asyncio.Task`
  (`_run_turn`, `:156-160`), broadcasting each yielded `TurnEvent` over the WebSocket via
  `session.websocket.send_json`. `InterruptEvent` calls `harness.interrupt` directly (`:171`). This
  router is mounted in `backend/main.py:185` (`app.include_router(ws_router)`, no bearer-token
  dependency — the WS endpoint authenticates inline via a `token` query param, `ws/__init__.py:110`)
  — confirmed live end to end.
- **`backend/api/conversations.py:13,22`** imports `harness.models.Citation` only, to type a
  response field (`citations: list[Citation]`) for a REST endpoint that reads persisted messages —
  not a call into the harness itself, just shape reuse.
- **`backend/sandbox/__init__.py`** and **`backend/sandbox/models.py`** reference
  `harness.models.TurnEvent` only in docstrings/comments explaining why Sandbox's own run-log events
  are deliberately *not* added to that union — no actual import or call.
- No other module imports `harness` or calls `run_turn`/`interrupt`/`begin_turn`. Nothing found in
  step 2 is dead code on the harness side — the harness itself is fully reachable through the one
  live path.

---

## Open questions / rough edges

- **Cancellation has a real blind spot.** The `cancel_flag` check inside the completion loop only
  runs between yielded chunks (`:495`); the module docstring itself calls out that a stream which
  never yields even once cannot be interrupted there. `_COMPLETION_TIMEOUT_S` (90s) is the only
  backstop, and it's a per-read timeout in LiteLLM, not a hard cap on total stream duration, so a
  long but still-yielding response is unaffected either way.
- **Memory is locked in before the loop starts.** `_maybe_retrieve` runs once, pre-loop, from the
  raw user message only — the model cannot request a memory search mid-loop (`query_memory` isn't a
  tool). A multi-step tool-calling turn that only realizes partway through that it needs project
  memory has no way to get it.
- **Tool-call history sent to the model is asymmetric.** `_history` only pulls `user`/`assistant`
  rows (`:254`) — the persisted `tool_call`/`tool_result` rows from earlier turns in the same
  conversation are excluded from context on later turns, even though they're written to `messages`.
  Tool activity is legible only within the turn that produced it.
- **Iteration-cap fallback text can mask a real partial answer.** The `while/else` branch (`:577-581`)
  only substitutes the "reached my step limit" message when `full_text` is empty; if the last pass
  produced any text at all — even a stray fragment from a tool-call-only response — that fragment
  becomes the final answer with no signal to the user that the step limit was actually hit (aside
  from `TurnCompleteEvent.iterations == 8`, which the frontend must interpret itself). Also note this
  branch predates `interrupted`'s default of `False` — if the cap is hit without an interrupt,
  `interrupted` stays `False` in the persisted row even though the turn didn't reach a genuine
  stopping point.
- **Duplicate-turn guard is a heuristic, not a true idempotency key.** It matches only on exact text
  equality within a 5s window against the *single* most recent row — two different legitimate users
  typing the identical short message ("yes") within 5 seconds on the same conversation would also
  match, though `(project_id, conversation_id)` scoping and the short window make this narrow.
- **`_matching_memory_row` is a bare substring check** (`quote in row.text`, `:289`) with no
  normalization (whitespace, case, punctuation) — a quote that matches semantically but not
  byte-for-byte (e.g. differing internal whitespace from generation) silently falls through to
  `<unverified>` even though the source row genuinely supports it.
- **Tool dispatch has no partial-effect rollback story across tools.** `_dispatch_tool_bounded`
  cancels the dispatch coroutine on timeout/interrupt, but if a tool like `add_paper` already
  committed a DB write via its own inner session before the outer wait times out, that write is not
  itself undone — only the `tool_call`/`tool_result` message pair around it rolls back (via the
  shared `db.session()` transaction), leaving persisted-paper vs. persisted-message state
  potentially inconsistent. `add_paper`'s own transaction boundaries weren't traced further here —
  flagged as worth checking against `papers.add_paper`'s session handling.
- **`open_paper_ids` staleness is silently absorbed.** `_open_papers` (`:187-197`) drops any paper id
  that no longer resolves without any signal back to the model or the transcript — a tab closed or a
  paper deleted mid-turn just vanishes from the evidence set with no trace.
