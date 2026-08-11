# Harness Architecture Audit

Your harness is `backend/harness/` — a real single-agent tool-calling loop, wired to a WebSocket, with citations validated structurally. It matches D18 nodes 1, 5, 7 well.

- **Strongest:** model gateway, interrupt/cancellation, event contract, sandbox.
- **Weakest:** no compaction, no tool registry, no MCP, no subagents, no permission model, and the agent can't reach the approval gate.
- **Of 11 primitives: 5 present, 4 partial, 2 missing.**
- **Note:** your docs define D18's **7 nodes**, not 11 primitives. This audit uses the standard agent-harness primitive list and maps your 7 nodes onto it.

---

## 1. Current Architecture

```
Renderer (React)  ──ws──►  ws/            ──►  harness/          ──►  llm/     ──► providers
  CompanionPane            Session         run_turn (async gen)      complete
  UIState snapshot         broadcast()     begin_turn/interrupt      complete_structured
       ▲                        │                  │
       └──── typed events ──────┘                  ├──► harness/tools.py  ──► search/papers/vault/experiments
                                                   ├──► memory/  (pgvector + tsvector + rerank)
                                                   ├──► provenance/ (quote → anchor)
                                                   └──► db/  (messages, result_store)

Out of band:  jobs/ (SAQ on Postgres) ──► parse/embed/extract/feed/run_experiment
              sandbox/ (Docker + Jupyter) ──► approval-gated execution
```

### Turn lifecycle (`backend/harness/__init__.py:385`)

1. `ws.handle_message` reserves the in-flight slot **synchronously** via `begin_turn` (`:136`), then spawns `run_turn` as a task — this closes the interrupt race.
2. Dedup check against the last `messages` row within 5s (`:269`), then persist the user message.
3. Build context: system prompt → highlighted selection → per-paper evidence blocks → open-papers read set → memory rows → full history.
4. Loop up to 8 iterations (`:482`): stream completion → accumulate tool-call deltas → dispatch each tool (bounded by 60s + cancel flag) → persist `tool_call`/`tool_result` rows → feed `model_view` back.
5. Validate every `<cite>` span and every untagged long quote against paper anchors or retrieved memory rows (`:313`); unresolvable ones become `<unverified>`.
6. Persist the assistant message, emit the validated text as `text_delta` pieces, then `turn_complete`.

### Load-bearing invariants

- One turn per session.
- The LLM only ever sees `model_view` (rich payloads live in `result_store`, fetched by id).
- No citation reaches the UI unvalidated.
- `litellm` is imported in exactly one file.

---

## 2. The 11-Primitive Checklist

| # | Primitive | Status |
|---|---|---|
| 1 | Control loop | ✅ Present |
| 2 | Context assembly & compaction | ⚠️ Partial |
| 3 | Tool layer & dispatch | ⚠️ Partial |
| 4 | Memory & retrieval | ⚠️ Partial |
| 5 | Model gateway | ✅ Present |
| 6 | Event stream & transport | ✅ Present |
| 7 | Interrupt & cancellation | ✅ Present |
| 8 | State persistence & recovery | ⚠️ Partial |
| 9 | Approval / permissions | ⚠️ Partial (not agent-reachable) |
| 10 | Sandboxed execution | ✅ Present |
| 11 | Extensibility & delegation (MCP, subagents) | ❌ Missing |

---

## 3. Detailed Assessment

### 1. Control loop — ✅ Present

- `harness/__init__.py:482`. Single agent, no orchestrator. Cap = 8 (`_MAX_ITERATIONS:85`).
- Graceful stop on cap (`:577`) — emits "I reached my step limit", never raises. Matches D18 node 1.
- Tool-call deltas accumulated by `index` across stream chunks (`:490`), so multi-tool turns work.
- **Gap:** no per-turn wall-clock or token budget — only the iteration count bounds a turn.

### 2. Context assembly & compaction — ⚠️ Partial

- **Ambient (built fresh each turn, `:461`):** system prompt (`:151`) · highlighted selection · `_paper_evidence` per open/selected paper — extracted card quotes, else a 4000-char excerpt (`:207`) · open-papers read set (`:230`) · memory rows (`:242`).
- **History:** `_history` (`:250`) loads **every** user/assistant row, unbounded.
- **Missing — the whole of D18 node 2's second half:** no compaction, no token budget, no eviction order (working set → history → retrieval). The only defence is `llm/_fit_to_budget` (`llm/__init__.py:205`), a last-resort binary-search truncation of the single longest message. That will silently amputate your paper evidence block on a long conversation.
- **Missing:** the working set. `UIState` is only `selection` + `open_paper_ids` (`harness/models.py:22`) — no `activeTab`, no `openPanes`.
- **Missing:** mid-turn `ui_state` merge. `ws:164` updates `session.ui_state`, but the running turn already captured its own copy — the update lands on the *next* turn, not the current iteration.

### 3. Tool layer & dispatch — ⚠️ Partial

- **Contract is right:** `ToolResult{model_view, ui_view_result_id, ui_actions}` (`harness/tools.py:27`). Only `model_view` re-enters the LLM.
- **Result store is real:** `result_store` table (`db/models.py:77`) with JSONB `ui_view` + `expires_at`, served by `GET /api/results/:id` (`api/search.py:55`). **But only `search_papers` writes to it** — every other tool returns a `None` ref.
- **Catalog is 6 tools:** `search_papers`, `add_paper`, `open_paper`, `save_note`, `log_experiment`, `update_experiment`.
- **Missing from D19:** `get_paper`, `compare`, `refine_results`, `query_memory` (as a tool), `mark_relevant`, `create_highlight`, `open_reference`, and all pure-nav tools (`scroll_to`, `highlight_span`, `open_view`), plus `propose_cell`/`run_all`/`read_run`.
- **Architectural weak point:** `dispatch` is a hardcoded `if tool_name == ...` chain (`tools.py:131`) with schemas as a hand-written literal list. No registry, no decorator, no schema-from-signature. Every new tool = two edits in two places that can drift. **This is what blocks primitive 11.**

### 4. Memory & retrieval — ⚠️ Partial

- **Retrieval is solid:** `memory/query_memory` (`memory/__init__.py:177`) — query-time union `paper_chunks ∪ project_chunks`, hybrid fusion, cross-encoder rerank, returns `CitedRow`. Matches D25.
- **Write path:** papers (abstract + sections), notes, experiments all chunked and embedded through the job queue.
- **Missing — D18 node 4's conversation half:** `conversations.summary` is **read** by `_chunk_conversation_summary` (`:119`) but **never written** by anything. `summarised_through_seq` is dead. So past conversations are verbatim in `messages` but never enter the index — the agent cannot recall an earlier session.
- **Architectural oddity:** memory is not a tool. `_maybe_retrieve` (`:172`) is a pre-turn LLM gate — one `complete_structured` call decides yes/no + a query, before the loop starts. The agent can't decide mid-loop that it needs to search. That was a Phase-1.7 shortcut that the Phase-2.3 tool loop never replaced.

### 5. Model gateway — ✅ Present (the most mature part)

- `llm/__init__.py`, the only file importing `litellm`. 10 provider prefixes.
- Tiers: `primary` / `auxiliary`, auxiliary falls back to primary (D11/D18 node 6).
- `complete` (streaming, tools) and `complete_structured`.
- **Schema repair:** one corrective retry showing the model its rejected output + the JSON schema (`:412`) — chosen over a flattened fallback schema after live comparison.
- **Rate limiting:** per-provider `RateLimiter` at 2.5s between call starts (`:321`), `num_retries=0` on every litellm call so `call_with_retry` is the sole retry authority, reading the provider's stated wait time.
- **Self-healing budgets:** parses the real TPM ceiling out of a 429 body, persists it to settings, refits and retries once (`:283`).
- **Gaps:** no token/cost accounting anywhere (no `usage` capture); mid-stream failures are never retried (correctly — can't un-yield); the prompted-structured-output fallback for models without native tool-calling is delegated to litellm rather than owned here, so a weak local model's tool-calling failure has no harness-level fallback.

### 6. Event stream & transport — ✅ Present

- `ws/__init__.py` — one WebSocket per project over loopback, token-authenticated.
- **All 7 down events shipped:** `status`, `text_delta`, `tool_call`, `tool_result`, `ui_action`, `turn_complete`, `error`. **All 3 up events:** `user_message`, `ui_state`, `interrupt`.
- `ErrorEvent` carries `recoverable` + `what_still_worked` — good discipline.
- Dropped socket leaves the session live; broadcast failure is logged, the turn keeps persisting (`:146`).
- **Caveat worth naming:** `text_delta` is not live streaming. Text is accumulated in full, validated, then re-emitted split on tag boundaries (`harness:603`). The user sees nothing until the turn is essentially done. This is a deliberate consequence of D24 (never show unvalidated text) — but it means "streaming" is cosmetic today.

### 7. Interrupt & cancellation — ✅ Present

- Cooperative cancel flag per session (`_in_flight:129`), not `Task.cancel()` across the transport boundary — so Session Transport never needs a task handle.
- `begin_turn` reserves the slot in the same call stack as the message (`:136`), closing the "interrupt arrives before the task is scheduled" race.
- Flag checked between stream chunks (`:495`); tool dispatch raced against both the flag and a 60s timeout (`_dispatch_tool_bounded:358`), with the losing coroutine cancelled and awaited out.
- Partial results always persisted; `turn_complete(interrupted=true)` always emitted. Cancellation is caught in exactly one place, per Rules.md.

### 8. State persistence & recovery — ⚠️ Partial

- Every turn writes 4 row kinds — `user`, `tool_call`, `tool_result`, `assistant` — sharing a `turn_id`, written incrementally as the turn runs.
- Duplicate-turn guard (`:269`) as defence-in-depth behind the socket-eviction fix.
- **Missing:** no resume. A crash mid-turn loses the loop; the transcript survives, but nothing replays or continues it, and the UI has no way to know the turn died.
- **Missing:** `_in_flight` is process-local and in-memory — correct for a single sidecar, but it means a sidecar restart mid-turn leaves the frontend's status pill orphaned.

### 9. Approval / permissions — ⚠️ Partial, and disconnected from the agent

- **The gate itself is well built:** `sandbox.mint_confirmation` / `_consume_token` (`sandbox/__init__.py:329`, `:341`), token bound to a hash of the run spec, `experiment_runs.approved_at NOT NULL` as the DB-level invariant. Exactly one path to a `source: measured` metric.
- **But the agent cannot reach it.** `run_all` is a REST route (`api/experiments.py:104`), not a tool in `TOOL_SCHEMAS`. D19 says `run_all` is the one tool that cannot complete without a human — today it isn't a tool at all.
- **Missing:** any general permission model. All 6 tools auto-execute, including the three that mutate state (`add_paper`, `save_note`, `log_experiment`). There's no notion of a tool needing confirmation, no allowlist, no per-tool risk tier.

### 10. Sandboxed execution — ✅ Present

- `backend/sandbox/` (960 lines) — Docker client, container lifecycle, log streaming to the WS, free-port picking, HTTP-readiness wait, notebook save-and-verify, orphan sweeping, per-project container ceiling, shutdown draining.
- Long-running Jupyter servers survive navigation (Phase 6.7); `run_all` executes in a clean restart-and-run-all container.
- Wired to the job queue via `run_experiment_job`.

### 11. Extensibility & delegation — ❌ Missing

- **MCP: zero.** Not one occurrence of "mcp" in any Python file. TRD §2.4 calls the MCP adapter "built as the extension seam" — it isn't built. (Shipping zero MCP *servers* in v1 is the decision; not building the *adapter* is a gap against the doc.)
- **Subagents: zero.** No `deep_research`, no subagent-as-tool mechanism.
- **No hooks, no skills, no plugin surface.**
- **The blocker is primitive 3:** with an `if`-chain dispatch and a literal schema list, there is nowhere for an external tool source to register.

---

## 4. Cross-Cutting (Not in the 11, but worth flagging)

- **Observability — thin.** `logging.basicConfig` to stderr, structured `event=` lines in `main.py` and `jobs/`. **Zero logging inside `harness/`** — no per-turn trace, no iteration/latency/token records. When a turn misbehaves you have the transcript rows and nothing else.
- **Autonomy lane exists but the harness never uses it.** `jobs/` runs SAQ on Postgres with real scheduled jobs (`feed_poll`, `interest_profile_reextract`). D18 says the feed is "a scheduled harness job" — today those jobs are plain functions; the harness only ever runs from a `user_message`.

---

## 5. Recommended Next Moves (Dependency Order)

1. **Tool registry** to replace the `if`-chain — unblocks primitives 3, 9, and 11.
2. **Context compaction** with the fixed eviction order — this is the one that will bite you in real use.
3. **`query_memory` as a real tool**, retiring `_maybe_retrieve`.
4. **`run_all` as an approval-gated tool.**
