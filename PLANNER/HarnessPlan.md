# Harness Plan — production-grade `backend/harness/`

Companion to `PLANNER/harness_report.md` (the audit). The audit says what is missing; this says what
gets built, in what order, and why. `DECISIONS.md` (D1–D37) remains the architectural authority —
where this plan departs from `TRD.md`, the departure is listed in §9 and the TRD gets amended, not
quietly contradicted.

Decisions taken in this session are labelled **H1–H12**. They are harness-scoped and do not renumber
anything in `DECISIONS.md`.

---

## 1. The premise

The harness exists so the model does not have to be smart. Concretely:

- It assembles the right context and keeps it inside a **fixed budget**, so no large context window is required.
- It hands the model a **small** tool list — the right ones, not all of them.
- It supplies **skills**: written playbooks that make execution repeatable without a frontier model.
- It **delegates** to subagents for wide, noisy work so the main context stays clean.
- It measures itself, so "better" is a number and not an opinion.

### H1 — Model floor: ~20–30B class

The harness must make a `gpt-oss-20b` / `qwen3-30b` class model work well, local or cheap-hosted.
Everything below follows from this. Three consequences are binding:

1. **Assume instructions longer than a paragraph are half-ignored.** Skills are checklists, not essays.
2. **Assume tool arguments are sometimes hallucinated** — a UUID that never existed, an enum value not in the list. Every tool validates and returns a *correcting* `model_view`, never an exception.
3. **Assume tool choice degrades past ~10–15 visible schemas.** Hence H4.

The existing code already documents this class's failure mode at `harness/__init__.py:90` — "a 20B
model routinely writes a fabricated quote". Design for that model, not around it.

---

## 2. Target package layout

`MODULES.md:858` already names the intended internals. This plan fills them in and adds four files.
Nothing outside `backend/harness/` may import any of these except through the package entry point.

```
backend/harness/
  __init__.py        public entry point: begin_turn · run_turn · interrupt · resume_orphans
  loop.py            the control loop (moved out of __init__.py)
  context.py         assembly, budgeting, eviction
  compaction.py      rolling conversation summary
  streaming.py       the cite-aware streaming state machine
  registry.py        ToolSpec + @tool decorator + schema generation
  approval.py        approval_request/response, per-turn budgets
  subagents.py       subagent runner + specs
  trace.py           turn_traces writer
  resume.py          orphaned-turn reconstruction
  result_store.py    result_store reads/writes (today only search_papers uses it)
  models.py          wire models: UIState, TurnEvent, ToolResult, ...
  tools/             one module per tool group — discovery.py, papers.py, notes.py,
                     experiments.py, navigation.py, memory.py
  skills/            *.md playbooks with YAML frontmatter
  mcp/               client.py (stdio + HTTP/SSE) · bridge.py (MCP tool → ToolSpec)
```

`harness/__init__.py` is 617 lines doing loop, context, citations, dedup and cancellation. Splitting
it is a precondition for most of what follows, not a cosmetic step.

---

## 3. Primitive-by-primitive design

### 3.1 Control loop — *present, needs budgets*

Keep the cooperative cancel flag, the synchronous `begin_turn` slot reservation, and the graceful
stop at the cap. They are correct and hard-won; do not touch them.

Add three bounds the loop does not have today:

| Bound | Value | Behaviour on hit |
|---|---|---|
| Iterations | 8 (existing `_MAX_ITERATIONS`) | Graceful stop (existing) |
| Wall clock | 180 s per turn | Graceful stop, same message path |
| Assembled context | 24 000 tokens | Eviction (§3.2), never truncation |

A wall-clock bound matters more with a local model than an iteration bound does: eight iterations of
a 30B model on CPU can run for minutes.

### 3.2 Context assembly — `context.py`

**H3 — Budget: 24 000 tokens total, ~3 000 of it never evicted.** Chosen for headroom on a
32k-context local model; a settings value, not a constant, so a 128k model can raise it.

Token counting must not import `litellm` outside `llm/` (existing invariant). Expose
`llm.count_tokens(messages, model) -> int` and use it everywhere; the char/4 approximation in
`memory/chunking.py:3` stays where it is and is not reused for budgeting.

Blocks, in eviction order — **first to go, last to go**:

| Band | Block | Evictable |
|---|---|---|
| 4 | Per-turn retrieval rows | first |
| 3 | Conversation history (verbatim turns) | second — into the summary (§3.3) |
| 2 | Working set (ids + titles of active items) | third |
| 1 | System prompt · provenance rules · tool schemas · **skill index** · **active skill body** · live UI state · paper evidence for open papers | **never** |

Two audit gaps close here:

**Working set and live UI state.** `UIState` (`harness/models.py:22`) is only `selection` +
`open_paper_ids`. TRD Node 2 requires the Zustand snapshot *including the active tab*. Extend to:

```python
class UIState(BaseModel):
    selection: Selection | None = None
    open_paper_ids: list[UUID] = []
    active_view: Literal["library","reader","notes","matrix","graph","feed","experiments"] | None = None
    active_id: UUID | None = None          # the thing open in that view
    open_panes: list[str] = []
    working_set: list[WorkingSetItem] = [] # {kind, id, title} — the handles the model manipulates
```

**Mid-turn merge.** `ws:164` updates `session.ui_state`, but a running turn captured its own copy, so
the update lands on the *next* turn. Fix: `run_turn` re-reads `session.ui_state` at the top of every
iteration and rebuilds band-1's UI block. Paper evidence is rebuilt only when the open-paper set
actually changed, so opening a tab mid-turn does not cost a re-read of every paper.

**Paper evidence stays deterministic.** `_paper_evidence` (`:207`) — extracted card quotes, else a
4 000-char excerpt — is band 1 and never evicted. This is the block `llm/_fit_to_budget` can
currently amputate; once `context.py` owns the budget, `_fit_to_budget` becomes a
never-should-fire backstop and gets a log line when it does.

### 3.3 Compaction — `compaction.py`

**H4 — Rolling summary into `conversations.summary`.**

Both `conversations.summary` and `conversations.summarised_through_seq` exist in the schema and are
**dead**: `memory/__init__.py:119` reads `summary`, nothing ever writes it. This one mechanism
revives both and closes two audit gaps at once.

```
when history tokens > 60% of the history band:
    turns = messages where seq > summarised_through_seq, up to the last K (K = 6) kept verbatim
    new_summary = auxiliary_tier_summarise(existing_summary, turns)
    write conversations.summary = new_summary
    write conversations.summarised_through_seq = last summarised seq
    enqueue chunk_and_embed_job(source_type="conversation_summary", source_id=conversation_id)
```

Rules:

- **Compaction is a window operation, never forgetting.** Every verbatim row stays in `messages`.
- Runs **between** iterations, never mid-stream.
- Auxiliary tier, with a timeout. On failure: fall back to dropping oldest turns for this turn only, log it, retry next turn. A failed summary never fails a turn.
- Re-indexing replaces the conversation's prior `project_chunks` rows for that `source_id` — today `chunk_and_embed_job` appends duplicates on re-run (`memory/__init__.py:147`). This is the first caller that re-runs, so delete-then-insert lands here.

### 3.4 Tool layer — `registry.py` + `tools/`

**The keystone.** Dispatch is an `if`-chain (`tools.py:131`) with a hand-written schema literal
(`tools.py:40`). Two edits in two places for every tool, free to drift. This blocks skills, MCP,
permissions and subagents simultaneously.

```python
@tool(
    name="get_paper",
    group="discovery",
    kind="query",                 # query | action — subagents may only hold `query`
    tier="auto",                  # auto | confirm
    per_turn_budget=None,         # int → max calls per turn
    core=True,                    # in the always-visible list?
)
async def get_paper(ctx: ToolContext, args: GetPaperArgs) -> ToolResult: ...
```

- **JSON schema is generated from `GetPaperArgs`** (a Pydantic model). Never hand-written again.
- Arguments are **parsed and validated** by that model. A validation error is caught and returned as a `model_view` that tells the model exactly what was wrong — a correction, not a crash. This is the single highest-leverage change for a 20–30B model.
- `ToolResult` grows the field TRD Node 3 specifies and the code lacks:

```python
class ToolResult(BaseModel):
    model_view: str                       # the ONLY field entering LLM context
    ui_view_result_id: str | None = None
    refs: list[Ref] = []                  # stable handles: {kind, id, title}
    ui_actions: list[dict] = []
```

`refs` is how a hallucinated UUID stops happening: every result that names an object hands back a
handle, and the working set (§3.2) is built from the refs the turn has seen.

**Result store for everything.** Today only `search_papers` writes to `result_store`
(`api/search.py:55`). Every Query tool with a renderable payload writes one.

**H5 — Core set + skill-unlocked tools.**

Core (always visible, ~9): `search_papers` · `get_paper` · `query_memory` · `open_paper` ·
`save_note` · `add_paper` · `log_experiment` · `open_view` · `load_skill`.

Everything else — `compare`, `refine_results`, `open_reference`, `mark_relevant`,
`create_highlight`, `scroll_to`, `highlight_span`, `propose_cell`, `run_all`, `read_run`,
`build_matrix`, `find_related`, `deep_research`, all MCP tools — is non-core, and enters the schema
list for a turn only when a loaded skill declares it. The model never sees 30 schemas.

Catalog completion against `TRD.md` §4.3 phase 1 is its own build phase (§8, H-Phase 2).

### 3.5 Memory & retrieval

**H6 — `query_memory` becomes a real tool; the pre-turn LLM gate is deleted.**

Delete `_maybe_retrieve` (`harness/__init__.py:172`). It costs an auxiliary completion before every
turn to answer a question the agent is better placed to answer mid-loop, and it makes retrieval
impossible after iteration 1.

**Retrieval bug — fix first, it is live today.** `query_memory` (`memory/__init__.py:177`) reranks 40
candidates and returns **all 40**. `_format_memory_rows` dumps every one into a system message. At
the 1 600-char chunk budget that is up to ~64 000 characters — roughly **16k tokens from one
retrieval, on a 32k model**. The RRF fusion beneath it is sound; the output stage simply does not cut.

```python
_RERANK_CANDIDATES = 40      # unchanged — recall stage
_RETURN_K          = 6       # NEW — hard cut after rerank
_MIN_RERANK_SCORE  = <calibrated>   # NEW — absolute floor; below it, return nothing
```

Returning nothing must be a **normal** outcome with an honest `model_view` ("no relevant material in
this project"), so the agent says so instead of inventing. The threshold is calibrated against the
eval fixture (§3.9), not guessed.

Other RAG work, in order of expected payoff:

1. **Top-k + threshold** (above). Biggest win, smallest change.
2. **Conversation summaries actually indexed** — falls out of §3.3. Past sessions become recallable for the first time.
3. **Per-type balancing.** A 12-section paper can occupy all 6 slots. Cap per `source_type` (e.g. max 3 from one paper) so notes and past conversations are not crowded out.
4. **Section-heading in the embedded text.** Chunks embed bare body text; prefixing `"{section_heading}: "` measurably improves matching on questions phrased by section ("what's their evaluation setup").
5. **Chunk budget review.** 1 600 chars / 200 overlap (`chunking.py:7`) is untuned. With `_RETURN_K = 6`, 1 600-char chunks cost ~2.4k tokens per retrieval. Test 800/150 against the eval set before assuming bigger is better.

**H7 — no retrieval subagent.** Considered and rejected: a tool call blocks the loop whether or not
a subagent runs inside it, so delegation makes retrieval *slower*, and D24 forbids the only thing
that would justify it — a subagent that paraphrases what it read produces text that resolves against
no row, so every claim built on it renders `⚠ unverified`.

### 3.6 Skills — `skills/`

**H2 — Model picks from an always-injected index. No classifier.**

A skill is a Markdown file with frontmatter:

```markdown
---
name: compare_papers
description: Use when the user asks how two or more papers differ, agree, or which is better.
tools: [compare, get_paper, create_highlight]
---

1. Confirm every paper named is in the open-papers list. If one is not, say so and stop.
2. `get_paper(paper_id, include=[card])` for each.
3. For each dimension the user asked about, quote one verbatim span per paper inside <cite> tags.
4. If a paper's card has no field for a dimension, write "not stated" — never infer it.
5. End with one sentence of your own judgement, outside any <cite> tag.
```

Mechanics:

- At startup the harness parses every skill's frontmatter into an **index** — `name: description`, one line each. ~200 tokens for 8 skills. Injected into band 1 every turn.
- The model calls `load_skill(name)`. The harness then (a) appends the body as a **system** message, not a tool message — system is more salient to a small model, and (b) adds the skill's `tools` to this turn's visible schema list.
- The loaded body sits in band 1 for the rest of the turn: never evicted, dropped at turn end.
- One skill at a time. Loading a second replaces the first, and its tools with it.

**Skill descriptions are load-bearing prompt engineering.** A skill the model never selects is dead
code, so every skill gets an eval scenario asserting it is chosen for its trigger phrasing.

Starting set (4 — resist more until each is measured):

| Skill | Why it earns a slot |
|---|---|
| `literature_review` | Multi-step, order matters, the model reliably skips steps without a checklist |
| `compare_papers` | Cross-paper citation discipline (US4) is exactly what a small model drops |
| `summarize_to_note` | High-frequency, and the note format should be consistent every time |
| `find_related_work` | The entry point for `deep_research` (§3.7) |

### 3.7 Subagents — `subagents.py`

**H8 — Subagents are tools. Query tools only. They return proposals, never mutations.**

Per TRD Node 1, never top-level orchestration. Contract:

```python
class SubagentSpec(BaseModel):
    name: str
    system_prompt: str
    tools: list[str]              # registry enforces every one is kind="query"
    max_iterations: int = 6
    timeout_s: int = 120
```

- **Fresh context.** The subagent gets its task string, the working set, and its own tools. It does *not* inherit conversation history — that is the whole point.
- **Own loop, own caps.** Reuses `loop.py` in a subagent mode: no citation validation pass (it returns rows, not prose claims), no persistence to `messages`, no `text_delta` upward.
- **Emits `status` upward only**, tagged with the subagent name, so the user sees "searching for related work — 3 of 6 queries" rather than a frozen pill.
- **Interruptible.** Inherits the parent's `cancel_flag`; the parent's wall-clock bound covers it.
- **Returns** a `ToolResult`: `model_view` = a short cited report, `refs` = the candidates as handles, `ui_view_result_id` = the full findings for the UI.
- **Cannot mutate.** Registry enforcement, not convention: a spec naming an `action` tool fails at import.

First subagent: `deep_research(question, max_sources=10)` — runs several query variants across
`search_papers` and `query_memory`, judges candidates, returns the survivors. The main agent or the
user then calls `add_paper`. Your library never changes without a message explaining why.

**Traced like a turn.** Every subagent run writes its own `turn_traces` row with a `parent_turn_id`,
or it will be a black box the first time it misbehaves.

### 3.8 Streaming — `streaming.py`

**H9 — Prose streams live; only quoted spans buffer through validation.**

Today `harness/__init__.py:603` accumulates the whole response, validates, then re-splits and emits.
The user watches a status pill for 15–30 s on a local model. D24 requires that nothing unvalidated
reaches the screen — it does not require that nothing *validated* reaches it early.

A three-state machine fed raw deltas:

| State | Enter on | Behaviour |
|---|---|---|
| `PROSE` | default | Emit immediately |
| `IN_TAG` | `<cite>` | Buffer to `</cite>`, validate, emit as `cite` or `unverified` |
| `IN_QUOTE` | `"` or `“` | Buffer to the closer; if ≥ 20 chars, validate as today's untagged-quote path; if shorter, emit as prose |

Details that will bite if missed:

- A delta can end **mid-delimiter** (`...<ci`). Hold any trailing partial-delimiter prefix; do not emit it.
- An **unclosed** span at turn end (interrupt, cap, error) flushes as `unverified`. Partial results, never well-formed ones — TRD §3.
- Validation is async (Provenance hits the DB), so the state machine takes an async validator callback and the loop awaits it inline. Cost is one substring lookup per span.
- `_strip_tag_markup` and the malformed-tag handling from Bug Fix Phase 3.1 move into the machine unchanged. That logic was earned by real failures; port it, don't rewrite it.
- The final persisted `messages.content` and `citations` are built from the same machine, so what was streamed and what was stored can never disagree.

### 3.9 Approval & permissions — `approval.py`

**H10 — Registry-declared tiers, plus per-turn per-tool budgets.**

Rules.md invariant 5 already requires this for code execution ("the sandbox and the consent gate are
independent controls and both are required"); today the gate exists but the agent cannot reach it —
`run_all` is a REST route (`api/experiments.py:104`), not a tool.

Mechanism (built once, spent sparingly):

- New wire events: **down** `approval_request {request_id, tool_name, args, summary, risk}` · **up** `approval_response {request_id, approved}`. Both go in `TRD.md` §2.4 Node 5's verbatim list.
- The loop awaits a `Future` keyed by `request_id`, raced against `cancel_flag` and a 300 s timeout — the same three-way race `_dispatch_tool_bounded` already implements. Reuse that shape.
- Denied or timed out → a `tool_result` of "the user declined this", not an error. The agent adapts and keeps going.

Policy:

| Tier | Tools |
|---|---|
| `confirm` | `run_all` (spawns a container, only path to `source: measured`) · any future delete · **every MCP tool by default** |
| `auto` | everything else — all reversible, all visible in the transcript |

Budgets: `per_turn_budget` on the spec, counted per turn including subagent calls. Exceeding it
returns "you have already called `add_paper` 5 times this turn" as a `model_view` — the agent adapts
rather than the turn failing. Defaults: `add_paper` 5, `save_note` 3, `search_papers` 6,
`deep_research` 1.

`run_all` also becomes a real tool, wired to the existing `mint_confirmation` / `_consume_token`
gate (`sandbox/__init__.py:329`). The DB invariant (`experiment_runs.approved_at NOT NULL`) stays the
last line of defence — the approval event is a UI affordance, not a replacement for it.

### 3.10 Observability & evaluation — `trace.py` + `tests/eval/`

**H11 — `turn_traces` table plus a golden-scenario suite.**

There is currently **zero logging inside `harness/`** and no `usage` capture in `llm/`. Both are
prerequisites, not nice-to-haves: none of the tuning above is verifiable without them.

`turn_traces` — one row per turn, **created at turn start with `status='running'`** and finalized at
the end. This makes it double as turn state, which §3.11 needs:

| Column | Notes |
|---|---|
| `turn_id` · `conversation_id` · `project_id` · `parent_turn_id` | `parent_turn_id` set for subagent runs |
| `status` | `running \| complete \| interrupted \| failed` |
| `iterations` · `total_ms` | |
| `prompt_tokens` · `completion_tokens` · `model` | needs `usage` capture added to `llm/` |
| `context_blocks` JSONB | `{block_name: tokens}` after eviction — this is how you see bloat |
| `tool_calls` JSONB | `[{name, ms, ok, denied, budget_blocked}]` |
| `retrieval` JSONB | `[{query, candidates, returned, top_score}]` |
| `citations` JSONB | `{validated, unverified}` — the D24 health metric |
| `skill` · `error_code` | |

**Two tiers of eval**, and the distinction matters:

1. **Deterministic (runs in CI, every commit).** A scripted stub LLM returns pre-written tool calls, so loop mechanics, eviction order, budget enforcement, approval flow, streaming state machine and resume are all tested with zero model calls and zero flake.
2. **Live (`pytest -m live`, run before merging a prompt or retrieval change).** ~25 scenarios against a seeded fixture project and a real 20–30B endpoint, asserting **structural** properties only:

- the expected tool was called (and no mutating tool was called on a read-only question)
- the expected skill was loaded for its trigger phrasing
- every `<cite>` validated — `citations.unverified == 0`
- assembled context stayed under budget
- iterations under N
- retrieval returned the fixture chunk that holds the answer

No LLM judge. Structural assertions are cheap, deterministic given a fixed seed, and they catch the
regressions that actually happen: a prompt tweak that stops a skill from firing, a retrieval change
that drops the right chunk.

### 3.11 Extensibility — `mcp/` + resume

**H12 — MCP adapter built, stdio and remote HTTP/SSE.**

- `mcp/client.py` — two transports behind one interface: subprocess/stdio, and HTTP+SSE.
- `mcp/bridge.py` — each discovered MCP tool becomes a `ToolSpec` with `tier="confirm"`, `core=False`, and a **prefixed name**: `mcp__<server>__<tool>`. Prefixing is not cosmetic; two servers will eventually both ship a `search`.
- Server config lives in `settings`. Connection happens at startup and lazily on reconnect.
- **A dead server degrades, never fails a turn.** Its tools simply are not in the catalog, and the readiness strip says so.
- **Security note, stated once.** Remote servers bring OAuth, token storage and network failure modes into the tool loop, and a third-party tool *description* is untrusted text entering a context that has DB-mutating tools available. `tier="confirm"` by default plus per-turn budgets are the containment. Do not relax that default per-server without a deliberate decision.

**Resume — `resume.py`.** Cheaper than it first looks, because the transcript already holds the loop
state. `tool_call` and `tool_result` rows share a `turn_id`, and `dispatch_tool` runs on the **same**
`db_session` as those rows (`harness/__init__.py:529-553`) — so a tool's DB mutation and its
`tool_result` row commit or roll back **atomically**. A tool with a persisted result never re-runs.

```
on session connect:
    for each turn_traces row with status='running' and no assistant message:
        rebuild llm_messages from messages rows (user, tool_call, tool_result, assistant partials)
        if the last tool_call has no matching tool_result:
            tier == auto     → re-dispatch (safe: it never committed)
            tier == confirm  → re-emit approval_request, never auto-replay
        resume the loop from that point
```

The only genuine hazard is a tool with side effects **outside** Postgres — `run_all` spawning a
container — and those are exactly the `confirm`-tier tools that are never auto-replayed. A turn that
cannot be rebuilt is marked `interrupted` and `turn_complete(interrupted=true)` is emitted, so the
status pill unsticks either way.

---

## 4. Schema changes

| Change | Phase |
|---|---|
| **new table** `turn_traces` (§3.10) | H-Phase 1 |
| `conversations.summary` / `summarised_through_seq` — no DDL, they exist; they start being **written** | H-Phase 4 |
| `chunk_and_embed_job` gains delete-then-insert per `source_id` (re-index, not append) | H-Phase 4 |
| `settings` gains `mcp_servers` JSONB and `context_budget_tokens` | H-Phase 10 / 3 |

Alembic revisions named `<rev>_phase<N>_<subject>.py` per Rules.md.

## 5. Wire contract changes

| Direction | Event | Phase |
|---|---|---|
| down | `approval_request {request_id, tool_name, args, summary, risk}` | H-Phase 7 |
| up | `approval_response {request_id, approved}` | H-Phase 7 |
| up | `ui_state` — payload extended (§3.2); event name unchanged | H-Phase 3 |
| down | `status` — now also carries subagent progress | H-Phase 9 |

Existing event names are unchanged. `TRD.md` §2.4 Node 5's verbatim list gets the two new ones.

---

## 6. Phased build plan

Dependency-ordered. Each phase ends green with its own tests, per Rules.md.

| Phase | Contents | Depends on |
|---|---|---|
| **H1 — Foundations** | Split `__init__.py` into `loop.py`/`context.py`. `registry.py` + `@tool` + schema-from-Pydantic; port all 6 existing tools; delete the `if`-chain. `ToolResult.refs`. `usage` capture in `llm/` + `llm.count_tokens`. `turn_traces` table + `trace.py`. Eval scaffolding: fixture project, scripted stub LLM, first 5 deterministic scenarios. | — |
| **H2 — Catalog completion** | TRD §4.3 phase-1 tools: `get_paper`, `compare`, `refine_results`, `open_reference`, `mark_relevant`, `create_highlight`, `update_note`, nav tools (`scroll_to`, `highlight_span`, `open_view`). Result-store writes for every Query tool. Core/non-core flags set. | H1 |
| **H3 — Context & budget** | `context.py`: bands, 24k budget, fixed eviction order. Extended `UIState` + working set from `refs`. Mid-turn `ui_state` merge per iteration. Wall-clock bound. `_fit_to_budget` demoted to a logged backstop. | H1 |
| **H4 — Compaction & conversation memory** | `compaction.py` rolling summary → `conversations.summary` + `summarised_through_seq`. Re-index (delete-then-insert) in `chunk_and_embed_job`. Past conversations become retrievable. | H3 |
| **H5 — Retrieval & RAG tuning** | `query_memory` as a registry tool. **Hard top-k + score threshold.** Delete `_maybe_retrieve`. Per-`source_type` balancing. Section heading in embedded text. Chunk-size A/B against the eval set. Retrieval eval scenarios. | H1, H4 |
| **H6 — Streaming** | `streaming.py` state machine. Port the Bug Fix 3.1 malformed-tag handling verbatim. Persisted content built from the same machine. | H1 |
| **H7 — Permissions & approval** | Tiers + `per_turn_budget` in the registry. `approval_request`/`approval_response` events + the three-way race. `run_all` as a real tool on the existing consent gate. Frontend approval affordance. | H1, H2 |
| **H8 — Skills** | `skills/` loader + frontmatter. Skill index in band 1. `load_skill` tool. Dynamic per-turn schema list. The 4 starting skills + a selection eval scenario each. | H2, H3 |
| **H9 — Subagents** | `subagents.py` runner, query-only registry enforcement, subagent `status` events, subagent `turn_traces` rows. `deep_research`, wired into `find_related_work`. | H1, H3, H7 |
| **H10 — MCP** | `mcp/client.py` stdio, then HTTP/SSE. `mcp/bridge.py` → `ToolSpec` with prefixed names and `tier="confirm"`. Settings UI. Degrade-on-dead-server. | H1, H7 |
| **H11 — Resume** | `resume.py` reconstruction from transcript. Orphan sweep on session connect. Confirm-tier tools re-ask, never replay. | H1, H7 |

**Suggested order if time is short:** H1 → H3 → H5 → H6 → H2 → H7 → H8 → H9 → H10 → H11.
H1/H3/H5 fix things that are wrong *today*. H6 is the largest perceived-quality win per unit of work.

---

## 7. What is deliberately not being built

| Not building | Why |
|---|---|
| A routing classifier / intent model | D16. The skill index does this job without a pre-turn call. |
| A retrieval subagent | H7 — slower, and D24 forbids the paraphrasing that would justify it. |
| Async / non-blocking tool results | Breaks one-turn-per-session and the linear message list, for a ~1-iteration latency win. |
| LLM-judge evals | Noisy, needs its own calibration, and structural assertions catch the real regressions. |
| Universal undo | More build than the approval gate, and `run_all` is not undoable anyway. |
| Provisional/"suggested" row states | Query-only subagents (H8) remove the need. |

---

## 8. Risks

1. **H10 remote MCP is the largest new attack surface in the app** — untrusted tool descriptions entering a context with mutating tools available. Contained by `confirm`-by-default and per-turn budgets. Do not relax per-server casually.
2. **H11 resume is the biggest item with the smallest payoff** — it fires only on a rare crash. If the plan runs long, this is the first thing to cut; H-Phase 1's `status='running'` column alone already fixes the visible symptom (the hung status pill) via the orphan sweep.
3. **H8 skills can quietly become dead code.** A skill the model never selects costs tokens and returns nothing. The per-skill selection eval is not optional.
4. **H5's score threshold is a calibration, not a constant.** Set it against the fixture set and record the number in the eval, or it becomes a magic value nobody dares change.
5. **The streaming state machine touches D24.** It is the only change in this plan that could put unvalidated text on screen if implemented wrong. Its deterministic tests come before its integration.

---

## 9. Amendments required to existing docs

These lines currently contradict decisions taken here and must be updated, not left to drift:

| Doc | Line / section | Change |
|---|---|---|
| `TRD.md` §2.4 Node 2 | Context assembly | Add the 24k budget, the four bands, and the "never evicted" list including the skill index and active skill body. |
| `TRD.md` §2.4 Node 3 | `ToolResult` | Field is `ui_view_result_id` in code, `ui_view_ref` in the doc. Reconcile to the code name. |
| `TRD.md` §2.4 Node 5 | Event list | Add `approval_request` (down) and `approval_response` (up). |
| `TRD.md` §2.4 Node 5 | Streaming | Note that `text_delta` streams prose live and buffers only cite/quote spans (H9). |
| `TRD.md` §4.3 | Tool catalog | Add the core/non-core split and `load_skill`; note skills unlock non-core tools. |
| `MODULES.md:858` | Harness internals list | Add `skills/`, `subagents.py`, `registry.py`, `approval.py`, `trace.py`, `resume.py`, `compaction.py`, `streaming.py`. |
| `TRD.md` §5.1 | Performance table | Add the 180 s per-turn wall-clock bound. |

`DECISIONS.md` needs no change: nothing here contradicts D1–D37. D18 Node 1's "subagents exist only
as tools" is honoured, D16's "no hardwired intent classifier" is honoured, and D24 is strengthened
rather than relaxed.
