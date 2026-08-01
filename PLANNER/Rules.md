# Rules — Research Companion OS (v1)

Guardrails for whoever or whatever writes the code. `DECISIONS.md` (D1–D37) is the authority on
architecture; `TRD.md` and `Schema.md` are its implementation-level restatement. These rules are
checkable: if code violates one, it is wrong, not "a style difference".

**Vocabulary (binding).** The build units are **Phase 1 … Phase 5** plus the cross-cutting Voice
layer. The word **"Slice" is retired** — it must not appear in code, comments, commit messages,
docs, tickets or variable names. `DECISIONS.md` "Slice N" reads as "Phase N".

**The five invariants. Never soften, never make configurable, never add an opt-out.**

1. The embedding model is `Alibaba-NLP/gte-modernbert-base`, 768-dim, fixed forever. Not a config
   key, not an env var, not a settings field. **Never route embeddings through Ollama or vLLM**
   because a local server happens to be running. Chat models swap freely; this one never does.
2. Claude Pro/Max and ChatGPT/Codex subscriptions are not LLM access — no API surface, ToS forbids
   it, **including via a local CLI shim**. BYO pay-as-you-go key or a local endpoint only.
3. Never fetch a paywalled PDF. Open-access fetch (arXiv, Unpaywall, S2 OA link) and user upload
   only; otherwise degrade to abstract + `source_url` with **no fabricated card**.
4. All code execution happens in a Docker container — always, no opt-out, agent-written and
   user-written alike. No subprocess path runs user or agent code on the host, not even in tests.
5. The agent never executes code without explicit user approval. **The sandbox and the consent gate
   are independent controls and both are required.**

---

## Naming Conventions

**Python (3.11+, backend/)**
- `snake_case` for modules, functions, variables; `PascalCase` for classes and Pydantic models;
  `UPPER_SNAKE` for module-level constants (`EMBEDDING_MODEL_ID`, `MAX_LOOP_ITERATIONS`).
- Pydantic model names match the wire shape exactly: `ToolResult`, `QuoteAnchor`, `UIState`,
  `CitedRow`, `ProjectPaper`, `KernelStatus`. One name per concept across API, harness and DB layer.
- No `async_` prefix on coroutines. Job handlers end in `_job` (`embed_chunks_job`,
  `docling_parse_job`); queue-facing functions live in `backend/jobs/`.
- Files mirror the packages in `TRD.md` §2.1: `harness/context.py`, `harness/tools.py`,
  `provenance/validator.py`, `provenance/locator.py`, `vault/writer.py`, `sandbox/kernel.py`.

**Database (Postgres 16 + pgvector)**
- `snake_case`, plural table names (`paper_chunks`, `experiment_runs`); FKs are
  `<singular>_id` (`paper_id`, `experiment_id`, `run_id`).
- Timestamps end `_at` (`validated_at`, `approved_at`, `polled_at`). Processing states end `_state`
  (`fetch_state`, `parse_state`, `embed_state`, `extract_state`). Hashes end `_hash`
  (`reqs_hash`, `notebook_hash`). Rendering hints end `_hint` (`page_hint`, `bbox_hint`).
- Enumerated values are `TEXT` + `CHECK`, never Postgres `ENUM`. **Reproduce the values in
  `Schema.md` byte-for-byte** — including `in-progress` with a hyphen in `experiments.status`. Do
  not "normalise" a stored value; the CHECK list is the contract.
- Every vector column is `VECTOR(768)`. Never parameterise the dimension.
- Alembic revisions are named `<rev>_phase<N>_<subject>.py` and stage tables by the phase
  `Schema.md` assigns them.

**TypeScript (frontend/, desktop/, packages/api-client/)**
- `PascalCase` for components and types, `camelCase` for values and functions, hooks are `useThing`.
  Component files `PascalCase.tsx`, hook files `useThing.ts`, design tokens in
  `frontend/src/design/`.
- **Never hand-declare a type that the generated client already exports.** Request/response and enum
  types are imported from `packages/api-client/`; a duplicated interface is a bug.
- **Enum wire values never carry display copy.** Relevance is `relevant | somewhat | not | unset` on
  the wire, in the DB and in the generated client. `not` → "not relevant" and `unset` → "unmarked"
  are render-time UI copy, produced by one `label` map per enum in `frontend/src/design/`. Rendering
  the raw value in a control, or sending a display string as a payload value, is a bug.

**Vault paths.** Global paper artifacts live under `library/papers/<canonical-id>/`; project
artifacts under `projects/<project-slug>/...`. Canonical ids are produced by one function
(DOI → arXiv → OpenAlex/S2) and never re-derived inline.

---

## Code Structure

Four packages, npm workspaces at the root: `backend/`, `frontend/`, `desktop/`,
`packages/api-client/`.

**`desktop/` holds zero logic.** It may only: spawn the sidecar, signal/kill it, read the bound port
from stdout, open native file dialogs, show/hide the window, and pass `{port, token}` to the preload.
No HTTP calls beyond liveness, no data access, no AI, no business rules. **Logic leaking into
`desktop/` is the named failure mode of this build** — treat any new import there as a review stop.

**Fat backend, thin frontend (D17).** The renderer may not decide which tool to call, assemble LLM
context, validate provenance, rank or re-rank results, re-derive a `ui_view`, or hold authoritative
state the sidecar does not also hold. It captures input and UI state, and renders events.

**Backend layering.**
- `backend/api/` routers validate, depend on the bearer-token dependency, call a service package,
  and return a Pydantic model. **No SQL and no LLM calls in a route handler.**
- Raw SQL lives only in `backend/db/` (hybrid pgvector/tsvector retrieval, recursive-CTE graph
  traversal). Everywhere else uses SQLAlchemy 2.x async. All SQL is parameterised — no f-strings.
- `backend/harness/` is a self-contained extractable package. Nothing outside it imports its
  internals; callers use its public entry point only.
- `backend/vault/` is the **sole writer of the vault**. No other module opens a vault path for
  writing. The file write and the index update happen in **one operation**: write the file, update
  the index in the same DB transaction, commit only after the file write succeeds; a failed index
  update rolls back and reports rather than leaving a half state.
- **No file watcher, no debounce, no hash-diffing, no conflict detection, no startup
  reconciliation.** All retired (Appendix A). The app is the sole writer and may assume so.
- **Notes are keyed by the stable YAML-frontmatter UUID, never by file path.** `notes.file_path` is
  a locator; querying or joining on it is forbidden.
- `backend/llm/` is the only place LiteLLM is imported. **No native provider SDK** (`openai`,
  `anthropic`, `google-generativeai`, …) appears anywhere in the repo.
- The embedding model is loaded in one module behind `sentence-transformers`, from a module
  constant. It is never reached through `backend/llm/`.
- **`backend/voice/` is the only module that may import an STT/TTS library, name an engine, or know
  a model exists** — public surface is `transcribe(audio_bytes, *, lang)` and
  `synthesize(text, *, voice)` plus the engine registry. The single exception outside it is the
  `api_keys.voice_engine` column value. `frontend/src/voice/` is the mirror module and exposes one
  hook; **no other component touches `getUserMedia` or an audio element.**
- CPU-bound work — embed, docling parse, rerank — runs on the D9 Postgres queue and never blocks the
  event loop. I/O-bound steps are awaited inline.
- **One-time setup never sits in a request path.** Vault layout, `docker compose up`, Alembic
  migrations and the catch-up-on-launch scheduler pass belong to the FastAPI lifespan. ML model
  loads are lazy, on first use, guarded by an `asyncio.Lock`.
- **Cache expensive derived artifacts globally by canonical paper id — compute once, ever.** Global
  tables (`papers`, `paper_content`, `paper_cards`, `paper_chunks`, `paper_edges`, `quote_anchors`)
  carry no `project_id`; project scope is the `project_papers` membership filter at query time,
  never duplicated data.
- **No second datastore** — no Redis, no Qdrant, no Neo4j — until a Postgres query actually measures
  slow. The queue stays Postgres-backed.
- **Every capability is reachable as a typed tool** (D19), so UI, text and voice share one path.
  Do not add a UI-only capability the agent cannot call, or an agent capability with no UI surface.

**The generated client.** `packages/api-client/` is regenerated from FastAPI's OpenAPI schema on
every backend change and committed in the same commit. A backend field rename must surface as a
**frontend compile error**, never a runtime `undefined`.

---

## Error Handling

- **One envelope, everywhere:** `{ code, message, recoverable, what_still_worked }` — returned by
  every REST error and carried by the WebSocket `error` event. It is built by a single FastAPI
  exception handler; routers do not construct it by hand. `what_still_worked` is required because
  the error card must say what still worked.
- **Partial source failure degrades, it does not fail.** One literature source timing out returns
  `200` with the hits that arrived and names the failed source in `what_still_worked`. Never a `500`.
- **A failed provenance validation is not an error.** The field is dropped, no `paper_cards` row is
  written, and the UI renders `not stated in this paper`. Do not raise, do not log at `error`, and
  never render it as unverified prose. A failing cited span in a message is stripped and its claim
  carries `⚠ unverified`, recorded in `messages.citations`.
- **Cancellation is caught in exactly one place** — the harness turn loop. It flushes partial
  results, persists them (`messages.interrupted = true`) and emits `turn_complete(interrupted=true)`.
  Partial results are never rolled back. No other layer catches `CancelledError`.
- Never catch bare `Exception` except at the FastAPI exception handler and the job-worker boundary.
  Never catch-log-rethrow. Never swallow an error to make code "work".
- **Logging.** stdlib `logging` to stdout (Electron main captures it), structured `key=value`, one
  line per event. Job logs carry job kind + row id, never payloads.
- **Never logged, ever:** provider API keys, their ciphertext, nonce or plaintext; the per-launch
  bearer token; request/response bodies of `/api/settings/models`; parsed PDF full text; audio
  bytes; embedding vectors. A key may only ever appear as its `…last4`.
- Container stdout/stderr goes to `experiments/<exp>/runs/` on disk and is referenced by
  `experiment_runs.stdout_ref`. It does not go into the database or the application log.

---

## Validation Rules

- **API boundary:** Pydantic v2 models on every request and response. No bare `dict` returns, no
  `response_model=None`. Enum fields are `Literal[...]` of the exact wire values, because those
  strings become compile-time types in the generated TS client.
- **Database:** the `CHECK` constraints in `Schema.md` are the second gate and are authoritative.
  `experiment_metrics.source` is `CHECK IN ('user','measured')` — **`llm` is unrepresentable and the
  CHECK is never widened.** `paper_cards.anchor_id NOT NULL` and
  `CHECK (source <> 'measured' OR run_id IS NOT NULL)` are structural provenance; do not relax
  either to make a write succeed.
- **Provenance (D24):** every extracted field is `{value, quote, char_offsets, section_heading}`. A
  **deterministic, non-LLM substring validator** in `backend/provenance/` confirms the quote resolves
  at the claimed offsets against `paper_content.full_text` **before** any `quote_anchors` or
  `paper_cards` row is written. No code path writes a card row without a validated `anchor_id`. This
  is enforced by mechanism, never by prompting.
- **Consent (D31):** `propose_cell` writes a cell and never executes. `run_all` requires a
  `confirmation_token` minted by a human UI interaction that displayed the code **and** the
  container spec (image, mounts, network, GPU). `experiment_runs.approved_at` is NOT NULL. There is
  no auto-run, no trusted-experiment mode, no blanket per-project approval.
- **Measured gate (D29):** `source: measured` requires `run_kind = 'clean_run_all'`, `exit_code = 0`,
  and a present image digest, `reqs_hash`, `notebook_hash` and timestamp. Interactive or
  out-of-order runs are recorded and never promoted.
- **The frontend does not re-validate.** No client-side provenance check, no client-side ranking, no
  client-side canonical-id derivation.
- **Schema changes are Alembic migrations.** No manual DDL, no `create_all` in application code.

---

## Testing Requirements

The primary gate is the **per-phase manual acceptance checklist** (PRD §13), signed off before the
next phase starts.

**pytest exists at exactly four points**, where correctness is invisible to the eye:

| Suite | Target |
|---|---|
| D24 provenance substring validator | A claimed quote resolves at the claimed offsets, or the field is dropped to `not stated`. |
| D25 canonical-id dedup | DOI → arXiv → OpenAlex/S2 priority, each path plus collision cases, all source ids retained. |
| D33 fuzzy quote locator | Whitespace, hyphenation and ligature variants locate across both the docling text stream and the PDF.js text layer. |
| D29 `measured` gate | Only a clean restart-and-run-all that exited 0 produces `measured`, always carrying `run_id`, image digest, `reqs_hash`, `notebook_hash`, timestamp. |

- **Mocking policy:** all four are pure functions over recorded inputs — fixture JSON for source API
  payloads, fixture text for parsed documents, a fixture run record for the gate. No network, no
  Postgres, no Docker, no LLM call in the test suites.
- **No coverage target, no CI service, no e2e framework, no frontend test runner, no broader
  strategy.** Do not add a fifth suite, a conftest for other subsystems, or a test that requires the
  compose stack. Everything else a human verifies by eye on the checklist.
- Tests never execute code outside Docker — invariant #4 has no test escape.

---

## Code Generation Rules (strict — non-negotiable)

**Resolution order.** Before writing any logic, stop at the first hit:
1. language builtin / syntax feature → 2. standard library → 3. a dependency already in
`backend/pyproject.toml` (Python) or the workspace `package.json` (TypeScript) → 4. a
well-established third-party library → 5. custom code (last resort).
Writing custom code requires one line stating which of 1–4 you checked and why each failed.

**Hard prohibitions.**
- Never reimplement what the stdlib provides: randomness, UUIDs, date/time parsing and arithmetic, timezone math, JSON/CSV/XML parsing, URL parsing, path manipulation, sorting, hashing, base64, regex, HTTP clients, temp files, arg parsing, string casing/padding/trimming, set/dict operations, deep copy, retries.
- Never hand-roll cryptography, auth, or password hashing — use the vetted library.
- Never write a parser, tokenizer, or state machine for a format that has a standard parser.
- Never add config flags, hooks, plugin points, base classes, or interfaces for use cases absent from the current code.
- Never swallow errors in try/except to make code "work".
- Never invent an API, function name, or parameter. If unsure it exists, say "unverified" and stop.

**Abstraction.** Rule of three: no abstraction until 3 real call sites exist; 2 duplications stay duplicated — duplication is cheaper than the wrong abstraction. No wrapper that only forwards arguments. No single-method stateless class — use a function. No inheritance without ≥2 concrete subclasses today. Max 1 layer of indirection between caller and the work.

**Dependencies.** Add one only if all hold: actively maintained (release within ~12 months), widely used (not a single-author micro-package for a 3-line task), solves a real non-trivial problem, license-compatible. Never add a dependency for what a builtin does in ≤3 lines. Prefer 1 well-scoped library over 3 narrow ones.

**Generality and correctness.** Solve the problem as stated, not a speculated future version — but be correct across the whole stated input domain, with no special-casing of the given examples. Never hardcode values that are inputs, config, or environment: paths, URLs, keys, limits, locales, dates, magic numbers — take a parameter or read config. Name genuinely-fixed constants instead of inlining literals. Prefer the idiomatic construct (comprehension, iterator, context manager, pattern match) over manual loops and flags where it is clearer.

**Size discipline.** Over ~15 lines → re-run the resolution order before continuing. If a stdlib or library one-liner exists, the one-liner is the only acceptable answer. No dead code, unused parameters, unreachable branches, or commented-out code.

**Uncertainty.** Never guess. If a library's behaviour, an API signature, or a requirement is unclear: state the ambiguity and ask, or check the official docs. Never produce plausible-looking code. Cite official docs when using a non-obvious API.

**Pre-output checklist** — all must pass. Comply silently; do not narrate the checklist.
1. Does the stdlib or an existing project dependency already do this?
2. Can this be shorter without losing correctness?
3. Any abstraction with <3 current call sites? → inline it.
4. Any hardcoded value that should be a parameter?
5. Does it handle the full stated input range, not just the example?
6. Any API used that has not been verified to exist?

---

## Module Rules (strict — non-negotiable)

**Objective.** Minimise complexity — anything that makes code hard to understand or change. It shows up as change amplification (a simple change edits many places), cognitive load (you must learn a lot to change anything), or unknown unknowns (it is not obvious what must change). Both causes are dependencies and obscurity. Complexity accrues one increment at a time; reject each increment as it appears.

**Module.** Any unit with an interface and an implementation — class, file, service, function. The interface is everything a caller must know: signature plus the informal contract (side effects, ordering, units, invariants, error behaviour). The implementation is everything else, and callers must be able to ignore all of it. A module's value is complexity hidden minus complexity exposed.

**Depth.** A module is deep when its interface is far simpler than its implementation; shallow when the two are comparable. Deep is the goal. Prefer few large deep modules over many thin ones. Never create a unit solely to make units smaller — length is not a design target. Never expose a field through a getter/setter pair; that hides nothing. Never let an interface enumerate the module's internal steps. Test: state what it does in one sentence without naming internals.

**Information hiding.** Each design decision — file format, wire protocol, eviction policy, index scheme, retry strategy — lives in exactly one module. If changing one decision edits two modules, it has leaked; restructure. Hide decisions inside methods as well as inside classes. Do not add a config parameter for a value the module can determine or measure itself.

**Decomposition.** Decompose by knowledge, not execution order — code that reads a format and code that writes it belong together. Each layer must offer a different abstraction than the one below it; a layer that resembles its neighbour is cost without benefit. Never pass a value through a layer that does not use it. Combine two units when they share information, are always used together, overlap in code, or neither is understandable alone. Separate them when special-case logic is polluting a general mechanism.

**Pull complexity downward.** Given a choice, the module absorbs the difficulty rather than its callers — one implementer pays, every caller is spared. Prefer an interface that handles the hard case internally over one that documents it for callers. Never raise a decision to the caller because deciding inside is inconvenient.

**Errors.** Apply the earliest strategy that works: (1) define the error out of existence — change the semantics so the condition is legal, so deleting a missing key succeeds and a substring past the end clips; (2) mask it inside the module; (3) aggregate many sites under one handler high in the stack; (4) crash fast on unrecoverable state. An exception may exist only if the caller will act differently because of it. Never throw what the module can absorb, never try/catch at a layer that cannot recover, never catch-log-rethrow, never catch broad types, never wrap-and-rethrow at each layer, never swallow silently, never re-check preconditions the caller guarantees, never write a recovery path that cannot be tested.

**Procedure for every new module.** (1) Write the interface comment first — purpose, inputs, outputs, invariants, error behaviour — before any implementation exists. (2) Read it back: if it is long, exception-laden, or leaks internal steps, revise the interface, not the comment. (3) Name precisely — a name must state exactly what the thing is or does; a name that is hard to pick means the abstraction is confused, so return to step 1. (4) Implement against the frozen interface: do not widen the signature, do not add public methods. (5) Check the result against the defect list below and report any violation with its fix.

**Defects — every item below is a defect. Do not produce it; remove it where it exists.**
- Structure: shallow module; information leakage (one decision in two modules); temporal decomposition (structure mirrors execution order); overexposure (the common case forces the caller to learn rare options); pass-through method (forwards with nearly the same signature); pass-through variable (crosses layers that never read it); repetition; special-general mixture (special-case code inside a general mechanism); conjoined methods (neither readable or changeable without the other).
- Documentation: a comment that restates the code; an implementation detail in an interface comment; nonobvious code whose behaviour cannot be predicted on first read.
- Naming: a vague name; a name that was hard to pick; an entity whose doc comment is long or full of exceptions.

**Comments.** Record what code cannot express: intent, units, invariants, constraints, rationale. Write interface comments before the code, as a design instrument. Interface comments describe what a caller needs and nothing more. Implementation comments describe what and why — the code already states how. Documentation lives in the code, not in commit messages or external documents. Update a comment in the same edit that changes the code it describes.

**Consistency.** Do similar things in similar ways. Follow the conventions in the surrounding code — naming, layout, error strategy, interface shape. Never introduce a second way to do something already done here; if the existing convention is wrong, change every instance or none.

**Priority.** When these rules conflict, resolve in order: correctness, then a simpler interface, then a simpler implementation, then performance — and performance only once measurement identifies a critical path. Never trade interface simplicity for unmeasured performance.

**Boundaries are fixed at design time.** `PLANNER/MODULES.md` is the authority on which modules exist, what each is responsible for, what it hides, and what its public surface is. Implement against it. If the code needs a module or a public symbol that MODULES.md does not have, stop and say so — do not add it and move on.

---

## AI Agent Behaviour Rules

**Authority order.** A direct instruction in the user's prompt → `DECISIONS.md` →
`Research Companion Workspace OS.md` (vision) → `UI_DESIGN.md` (look-and-feel only; it never
outranks a decision on behaviour, data, screens or flows). `TRD.md`, `Schema.md` and this file
implement `DECISIONS.md` and never contradict it.

- **Consult `DECISIONS.md` before changing anything architectural** — the stack, a module boundary,
  the data model, a wire contract, the harness shape. If a change touches one of the five
  invariants, stop and flag it.
- **Never re-derive an Appendix A retired path.** Supabase · hosted or multi-tenant deployment ·
  auth/OAuth/JWT/RLS/`owner_id`/demo door · $0 hosted deployment · "Postgres is truth, PDFs are a
  cache" · BYO Drive/OneDrive blob storage · Web Speech API · hosted WebRTC voice · local
  speech-to-speech as the architecture · Claude/ChatGPT subscriptions as LLM access · hardwired
  intent classifier or regex fast path · hosted-core/desktop-harness partition · Tauri ·
  SQLite + `sqlite-vec` or embedded Postgres binaries · Monaco · file watching / conflict detection
  / startup reconciliation · multi-machine sync · GPU arbitration · packaged distribution (AppImage,
  installers, signing, auto-update) · rate limiting · `FRONTEND_BRIEF.md` · warm sepia palette.
  These are dead with stated reasons. Proposing one as if new is a defect.
- **Out of scope by decision — do not add, and do not raise as an objection:** auth, multi-tenancy,
  `owner_id`, RLS, multi-machine sync, GPU arbitration, rate limiting, code signing, notarization,
  cross-OS builds, installers, auto-update, packaging pipelines, horizontal scale, SLAs, ops burden.
  Solo developer, Linux only, one user, student scope.

**Free — no confirmation needed.**
- Add functions, endpoints, components, jobs and queries **inside an existing module** whose
  responsibility `MODULES.md` already covers.
- Write an Alembic migration for a table or column already specified in `Schema.md`.
- Add cases to the four existing pytest suites.
- Regenerate `packages/api-client/` after a backend change.

**Ask first.**
- Deleting any existing function, file or migration.
- Any change to a `Schema.md` table, column, `CHECK` list, cascade rule or index.
- Adding or upgrading a dependency in `backend/pyproject.toml` or any `package.json`.
- Any edit inside `desktop/`.
- Adding a tool to the D19 catalog, renaming a WebSocket event, or changing the error envelope.
- Anything touching an invariant, or any work outside the phase currently being built.

**Banned outright — no code path may exist.**
- Writing prose or paper sections. The AI verifies, organises, checks citations and finds missing
  ones; the researcher is the author. `documents.body` is never AI-authored.
- Authoring a metric value, or adding `llm` to the `experiment_metrics.source` CHECK.
- Making the embedding model configurable, or routing embeddings through Ollama, vLLM or any LLM
  endpoint.
- A shim, CLI wrapper or proxy that uses a Claude or ChatGPT subscription as LLM access.
- A paywall fetch, scraper, cookie jar or institutional-proxy path.
- Executing code outside a Docker container, or any auto-run / trusted-mode / blanket-approval path
  around the consent gate.
- A file watcher, debounce, hash-diff, conflict detector or startup reconciliation pass.
- A UI-only capability with no corresponding tool.

---

## Security Rules

- **Network exposure.** The sidecar binds `127.0.0.1` on an ephemeral port (`port 0`) and prints the
  bound port on stdout. Binding `0.0.0.0` or any host interface is a bug, not a configuration
  option. Kernel ZMQ ports are published to loopback only.
- **Access control.** A per-launch bearer token, generated by Electron main with
  `crypto.randomBytes`-grade randomness, passed to the sidecar by environment variable, mandatory on
  **every** REST request and on the WebSocket upgrade — `401` before any router runs. Never
  persisted, never written to the vault or `api_keys`, never logged. There is **no user auth**: no
  `users` table, no `owner_id`, no JWT, no sessions, no RLS. The OS login is the auth boundary.
- **Secrets.** The master key lives in the OS keyring (`libsecret` via `keyring`). Provider keys are
  AES-256-GCM ciphertext in `api_keys.providers`, decrypted **in memory at call time only**; no
  plaintext value outlives the call. `GET /api/settings/models` returns `…last4` and never
  ciphertext, nonce or plaintext. Keys are validated on save with a live test call. Local providers
  (Ollama, vLLM) store a `base_url` and **no key** — the UI must not demand one.
- **No hardcoded values.** Never hardcode an API key, provider base URL, vault path, port or model
  string. The pinned embedding model id is the one named constant, and it is a constant precisely
  because it is not configurable.
- **Container isolation.** `--network none` by default; mounts exactly
  `projects/<slug>/experiments/<exp>/` read-write and `library/` read-only — **never the whole
  vault, never `$HOME`**. CPU quota, memory cap, idle timeout and per-cell wall-clock timeout are
  always set. `--gpus` only when `experiments.gpu_optin` is true. `network_enabled` and `gpu_enabled`
  are recorded on every run record.
- **Renderer hardening.** `contextIsolation: true`, `nodeIntegration: false`, no remote module. The
  preload exposes `{port, token}` and the native dialog proxies and nothing else.
- **Untrusted input.** Paper text, LLM output and literature-API payloads are untrusted. Never
  interpolate them into SQL (parameterised queries only, including the raw pgvector and CTE SQL),
  never render them as HTML (no `dangerouslySetInnerHTML`), never pass them to a shell (drive Docker
  through an argument list, never a shell string). A prompt-injected paper is the threat model; the
  consent gate is the control that stops it, not the container.

---

## Commit Conventions

- Conventional commits with the package as scope: `feat(backend/harness): …`,
  `fix(frontend/voice): …`, `chore(api-client): regenerate`. Scopes are limited to
  `backend/<package>`, `frontend/<area>`, `desktop`, `api-client`, `schema`.
- Subjects say **Phase N**, never "Slice".
- A commit implementing a decision carries the trailer `Decision: D24` (one or more ids).
- The regenerated `packages/api-client/` ships in the **same commit** as the backend change that
  caused it. An Alembic migration ships in the same commit as the model change it applies.
- Never commit `.research-os/`, vault contents, a `.env`, an API key, or a model weight file.
