# Session Transport (`backend/ws/`) — Design & Architecture

`backend/ws/` is a single-file module (`backend/ws/__init__.py`, 229 lines) — one WebSocket route, one in-memory per-project session registry, and a typed event pipe between the frontend and `harness.run_turn`. It knows nothing about tools, context assembly, or turn logic — it authenticates the upgrade, keeps at most one live session per project, parses upstream JSON into one of three Pydantic event types, and streams whatever the harness yields back down the same socket. A second, unrelated caller (`sandbox/`) reuses the same per-project registry to push run-log/status events down the same socket outside of any Companion turn.

---

## Storage / data model

No database tables of its own. State lives in one process-local dict:

```python
_sessions: dict[uuid.UUID, "Session"] = {}   # ws/__init__.py:40
```

**`Session`** (`ws/__init__.py:43-49`, a dataclass, one instance per connected project):
- `project_id: uuid.UUID`
- `websocket: WebSocket`
- `conversation_id: uuid.UUID` — resolved once at connect time (see below), never re-resolved for the life of the socket
- `ui_state: UIState = UIState()` — the harness's own `UIState` model (`harness/models.py:22`), mutated in place as `ui_state` events arrive
- `turn_task: asyncio.Task | None = None` — the currently-running `_run_turn` task, if any; stored but never read back or awaited anywhere in this module

**Upstream event types** (client → server, `ws/__init__.py:52-81`), dispatched by a `raw["event"]` string key:
- `UserMessageEvent` — `text`, `ui_state: UIState`, `input_modality: "text" | "voice" = "text"`
- `UIStateEvent` — `selection: SelectionState | None`
- `InterruptEvent` — `turn_id: uuid.UUID | None` (accepted on the wire but never read — see Open Questions)

`_UPSTREAM_EVENT_TYPES` (`ws/__init__.py:77-81`) maps the three `event` strings to their model classes; anything else raises `ValueError` in `_parse_upstream` (`ws/__init__.py:198-202`).

**Downstream events** (server → client) are not defined in this module — they're `harness.models.TurnEvent`, a union of `StatusEvent | TextDeltaEvent | ToolCallEvent | ToolResultEvent | UIActionEvent | TurnCompleteEvent | ErrorEvent` (`harness/models.py:120`), plus `ErrorEvent` constructed directly by this module for one local error case, plus (as of Phase 2.2, per the module docstring) `sandbox`'s own `RunLogEvent`/`RunStatusEvent` models broadcast through the same function without being folded into `TurnEvent`'s union.

---

## Core mechanics

### Connection lifecycle (`handle_connect`, `ws/__init__.py:97-123`)
1. Token check: `token != get_config().bearer_token` → `websocket.close(code=4401)`, return `None`. This runs before `websocket.accept()`, so the FastAPI route (`session_endpoint`, `ws/__init__.py:205-209`) just returns without entering the receive loop.
2. `websocket.accept()`.
3. `_get_or_create_conversation(project_id)` (`ws/__init__.py:84-94`): selects the most-recently-created `Conversations` row for the project; if none exists, inserts one and flushes to get its id. This is the *only* place `conversation_id` is ever picked for the session — it happens once, at connect.
4. Builds a new `Session` and does eviction: reads any existing `_sessions[project_id]` into `evicted`, immediately overwrites the registry entry with the new session, then closes `evicted`'s socket via `_close_evicted` if one existed. The registry write happens *before* the old socket is closed — deliberately, per the docstring, to close the "double-connect leaves a stray live socket registered nowhere" race (Bug Fix Plan Phase 3.4) rather than merely reduce its odds.

### `_close_evicted` (`ws/__init__.py:126-135`)
Best-effort `await session.websocket.close()`; a `RuntimeError` (socket already closing) is caught and logged at `info`, not raised — closing an already-gone socket is the intended outcome, not a failure.

### `get_session` (`ws/__init__.py:138-143`)
Plain `_sessions.get(project_id)` lookup, exposed as a function specifically so `sandbox/__init__.py` can find a project's live socket without importing `_sessions` directly.

### Receive loop (`session_endpoint`, `ws/__init__.py:205-229`)
After a successful `handle_connect`:
- Loops `websocket.receive_json()` → `_parse_upstream(raw)` → `handle_message(session, event)`.
- A parse failure (`ValidationError` or the `ValueError` from an unknown `event` key) is logged at `warning` and the loop `continue`s — the bad message is dropped, the socket stays open.
- `WebSocketDisconnect` breaks the loop and is logged at `info`.
- `finally`: pops the session from `_sessions` **only if** `_sessions[project_id] is session` still — an identity check, not a bare `pop`, so a stale receive loop unwinding after its session was already evicted by a newer connect can't delete the new session's registry entry.

### `handle_message` dispatch (`ws/__init__.py:163-195`)
Three branches on the parsed event's runtime type:
1. **`UIStateEvent`** → `session.ui_state = UIState(selection=event.selection)` and return. Note: this reconstructs `UIState` from `selection` alone, dropping any prior `open_paper_ids` the session had (there is no `open_paper_ids` field on `UIStateEvent` at all — only `user_message`'s embedded `UIState` carries it).
2. **`InterruptEvent`** → builds a `SessionRef(project_id, conversation_id)` and calls `await harness.interrupt(session_ref)`, which looks up the in-flight cancel flag for that project/conversation pair and sets it (`harness/__init__.py:612-617`). The event's own `turn_id` field is not consulted.
3. **`UserMessageEvent`** (the fallthrough) →
   - `session.ui_state = event.ui_state` (full replace, unlike the `UIStateEvent` branch).
   - `cancel_flag = harness.begin_turn(session_ref)` — reserves the in-flight slot synchronously, in the same call stack, before any task is scheduled (so a same-instant `interrupt` can't race a task that hasn't run yet). Returns `None` if a turn is already in flight for this project/conversation pair.
   - If `None`: broadcasts a local `ErrorEvent(code="turn_in_progress", recoverable=True, ...)` and returns — no task is created.
   - Otherwise: `session.turn_task = asyncio.create_task(_run_turn(...))` — spawned, not awaited, so the receive loop keeps running and can still receive a follow-up `interrupt` for this same turn while it streams.

### `_run_turn` (`ws/__init__.py:156-160`)
```python
async for turn_event in harness.run_turn(session_ref, text, ui_state, input_modality, cancel_flag):
    await broadcast(session, turn_event)
```
Pure relay: iterates whatever `harness.run_turn` yields and broadcasts each event in order. No buffering, no transformation, no error handling beyond what `broadcast` itself does.

### `broadcast` (`ws/__init__.py:146-153`)
```python
async def broadcast(session: Session, event: TurnEvent | BaseModel) -> None:
    try:
        await session.websocket.send_json(event.model_dump(mode="json"))
    except RuntimeError:
        logger.info(...)
```
Type hint widened from `TurnEvent` to `TurnEvent | BaseModel` specifically so `sandbox`'s non-`TurnEvent` models can reuse it (module docstring, `ws/__init__.py:13-17`). A `RuntimeError` (socket already closed) is swallowed and logged — the turn itself keeps running and persisting via the harness regardless of whether anyone is listening; there is no propagation back to the harness that the client is gone.

---

## Callers & dependents

**Mounted in `main.py`:**
```python
from ws import router as ws_router   # main.py:49
...
app.include_router(ws_router)        # main.py:185
```
Registered alongside the REST routers but, unlike them, carries its own token check inside `handle_connect` rather than the shared `require_bearer_token` dependency (`api/deps.py`) used by REST endpoints — auth here is a query-string `token` param compared to `get_config().bearer_token`, checked before `websocket.accept()`.

**Depends on `harness/` for:** `harness.run_turn` (the actual turn/tool loop, streamed event by event), `harness.begin_turn` / `harness.interrupt` (in-flight turn bookkeeping, keyed by `(project_id, conversation_id)`), and the shared models `SessionRef`, `TurnEvent`, `ErrorEvent`, `SelectionState`, `UIState` from `harness.models`. This module never imports anything from `harness` beyond these — confirms the module docstring's claim that it "knows nothing about tools or context assembly."

**Depends on `db`/`db.models`** only for `_get_or_create_conversation` (`Conversations` table lookup/insert).

**Reused by `sandbox/__init__.py`** (confirmed live, not speculative) — six call sites:
- `ws.get_session(experiment.project_id)` (`sandbox/__init__.py:485`, `881`) to find a project's live socket.
- `ws.broadcast(ws_session, RunStatusEvent(...))` / `RunLogEvent` / `NotebookServerStoppedEvent` (`sandbox/__init__.py:487, 495, 559, 883`) to push run-log/status/notebook-lifecycle events to the same socket a Companion turn would use, entirely outside `harness.run_turn`. Confirms the module docstring's Phase 2.2 note: these event types are not part of `harness.models.TurnEvent`'s union, and `broadcast`'s signature was loosened specifically to accommodate them.

**Frontend side** (`frontend/src/companion/CompanionPane.tsx`, `wsTypes.ts`): opens the socket, sends `user_message` (`CompanionPane.tsx:281-285`, `324-328`, including `input_modality`), sends `interrupt` on the Stop button click (`CompanionPane.tsx:370`) — no `turn_id` is sent, matching the backend's disregard of that field. `wsTypes.ts`'s hand-written `UpstreamEvent` type (`wsTypes.ts:47-50`) is stale relative to the runtime code: its `user_message` variant omits `input_modality`, even though `CompanionPane.tsx` sends it and the backend's `UserMessageEvent` model expects it (defaulted to `"text"` if absent). This is a type-declaration gap, not a runtime bug — the field just isn't type-checked at the call site.

**Session eviction and turn-in-progress errors are the two paths that reach `ErrorEvent` from inside this module** — every other `TurnEvent` on the wire originates in `harness.run_turn`, not here.

---

## Open questions / rough edges

- **`InterruptEvent.turn_id` is parsed, validated, and then never read.** The docstring at `ws/__init__.py:66-71` explains this was a deliberate fix for a prior bug (frontend sending `""` as a placeholder, which failed UUID validation and silently dropped the Stop button's message) — but the fix was to stop requiring the field, not to wire it up. `harness.interrupt` cancels by session (`project_id`+`conversation_id`), not by turn, so there is currently no way to interrupt a *specific* turn if that ever became necessary (e.g. a stale interrupt arriving after the targeted turn already finished and a new one started).
- **`Session.turn_task` is stored but never awaited, cancelled, or read anywhere in this module.** It's assigned at `ws/__init__.py:193` and never referenced again — dead weight on the dataclass, or an incomplete piece of some intended cleanup/cancellation path that never got wired up.
- **`UIStateEvent`'s handling silently narrows `UIState`.** `session.ui_state = UIState(selection=event.selection)` (`ws/__init__.py:165`) drops `open_paper_ids` back to its default (empty list) every time a bare `ui_state` event arrives, even if a prior `user_message`'s `UIState` had populated it. Whether the frontend ever sends a `ui_state` event with a populated `open_paper_ids` expectation wasn't checked here, but the code as written cannot preserve that field across a `ui_state`-only update.
- **No backpressure or ordering guarantee across concurrent `broadcast` callers.** `sandbox/` and a running Companion turn can both call `ws.broadcast(session, ...)` for the same session concurrently; `send_json` is not internally serialized by this module, so interleaving is left to Starlette's own socket-write behavior.
- **A socket dropping mid-turn doesn't stop the turn.** By design, per the `broadcast` comment (`ws/__init__.py:150-153`) — the turn keeps running and persisting, it just has nobody to stream to. There's no reconnect-and-catch-up path visible in this module for a client that comes back mid-turn; the next `handle_connect` gets a fresh `Session` with no memory of the turn that was streaming to the old one.
- **`_parse_upstream` failures are silently dropped**, not surfaced to the client as an `error` event — the client gets no feedback that its message was malformed, only a server-side `warning` log.
