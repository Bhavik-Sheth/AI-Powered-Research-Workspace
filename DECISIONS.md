# Research Companion OS — Architecture Decisions

**Status: fully specified. D1–D37 are all resolved. Nothing blocks building.**

Decided across grilling sessions on 2026-07-21, 2026-07-23 and 2026-08-01; **rewritten
2026-08-01** into a single statement of current truth. Companion to
`Research Companion Workspace OS.md` (the product vision). That file is the **what**; this file
is the **how**, and **this file decides scope**.

**How to read this.** Every decision below states what is true *now*. There are no "amended"
or "superseded" layers to unwind — dead paths were removed from the body and are listed once in
**Appendix A**, with the reason each died, so no future session re-derives them. Decisions were
renumbered 1..37 in this rewrite; **Appendix B** maps the old IDs (D1–D38, Q3–Q48) to the new
ones.

**Precedence, highest first:**
1. A direct instruction from the user in a prompt.
2. **This file** — scope, behaviour, data, architecture.
3. `Research Companion Workspace OS.md` — product vision. Where it is broader than this file,
   this file wins; it does not expand scope on its own.
4. `UI_DESIGN.md` — **look-and-feel only** (colour, type, spacing, component shape). It is
   inspiration-rank and never outranks this file on behaviour, data, screens, or flows.

---

## What this is, in one screen

**Research Companion OS is a local desktop application for one researcher.** An Electron shell
supervises a Python (FastAPI) sidecar. Your data is plain files in a folder you own. An agent —
the Companion — lives beside your work, can search the literature, read papers with you,
remember everything in the project, and write experiment code that **you** approve before it
runs in a Docker container.

**It is:**
- **Local.** Runs on your machine. No hosting, no server, no account, no network dependency
  beyond the literature APIs and (optionally) an LLM endpoint.
- **Single-user.** One person, one machine. Not multi-tenant, not shared, not synced.
- **Linux-targeted, solo-built, student-scope.** The author is the user.
- **Evidence-first.** Nothing is presented as coming from a paper unless a verbatim quote in
  that paper backs it.
- **Author-preserving.** The researcher writes the notes and the paper. The AI finds,
  organises, verifies, remembers, and navigates.

**It is not, by decision:**
- A hosted web app, a multi-user product, or a SaaS. No auth, no `owner_id`, no RLS, no billing.
- A Windows/macOS product. No code signing, no notarization, no auto-update, no installer.
- A sync product. One device. Point Syncthing or git at the vault folder if you ever want more.
- A writing assistant. It never drafts prose or paper sections.
- An autonomous agent. It never executes code without an explicit human approval.

**Scope discipline (binding).** Solo developer, Linux only, one user. Code signing,
notarization, cross-OS builds, update servers, multi-tenancy, scale, and ops burden are **out of
scope by decision** and are not to be raised as objections. Effort goes to the core app.

---

## Invariants

Five constraints that look like ordinary configuration and are not. Each was considered and
locked with reasons. Flag any proposal that touches one.

1. **The embedding model is fixed forever** — the pinned local `gte-modernbert-base` (D14).
   Chat models are freely swappable; the embedding model never is. Changing it silently
   invalidates every vector in the index.
2. **Claude Pro/Max and ChatGPT/Codex subscriptions cannot be used** as LLM access for this app.
   No API surface exists and the ToS forbids it — including via a local CLI, now that a local
   process exists. BYO pay-as-you-go API key or a local model (D11).
3. **Never scrape paywalled PDFs** (D23). Open-access fetch and user upload only; degrade to
   abstract-plus-source-link when neither is available.
4. **All code execution is sandboxed in Docker — always, no opt-out** (D30). Agent-written and
   user-written code alike.
5. **The agent never executes code without explicit user approval** (D31). The sandbox and the
   consent gate are *independent* controls and both are required: Docker limits damage, it does
   not decide whether a run should happen.

---

# Part 1 — Product & shape

### D1 — Target: a local desktop app for one researcher

Research Companion OS runs on the user's own machine as a desktop application. There is **no
hosted deployment, no multi-tenancy, and no auth in v1** — the OS login is the auth boundary.

**Why local, and why this was reopened three times.** The product started as a hosted,
multi-tenant web app, then as a single-tenant web app. Both died on one requirement:
**experimentation is not optional in research.** Reading papers and taking notes is half the
loop; running the thing is the other half. A hosted web app cannot touch the user's filesystem
or GPU, and the "agentic OS" direction only means something if the agent can act on the actual
machine. That requirement — not aesthetics — forced the desktop shape.

Multi-user is **not a goal**, not "future scope by design". Nothing is carried in the schema or
the code to accommodate it.

### D2 — App shape: Electron shell + Python sidecar

**All AI stays Python** (docling, `sentence-transformers`, LiteLLM, torch). Electron is Node +
Chromium and **cannot execute Python**, so a separate Python process is a **language boundary,
not a design preference**. Same shape as VS Code (Electron + extension host + language servers),
Obsidian (Electron + main-process file I/O), and Jupyter Lab (web UI + Python server).

| Layer | Tech | Responsibility |
|---|---|---|
| **Shell** | Electron main + preload (`desktop/`) | Spawn/supervise the sidecar, own the window, native file dialogs, tray, lifecycle. **Zero logic.** |
| **Renderer** | React + Vite (`frontend/`) | The window (D32 / `UI_DESIGN.md`). |
| **Sidecar** | FastAPI + the harness (`backend/`) | Everything real: agent loop (D18), retrieval, parsing, embedding, Docker orchestration. |
| **Index** | Postgres + pgvector in Docker | Machine-derived data only (D8). |
| **Truth** | The vault folder | Files on disk (D3). |

**Electron over Tauri** because Tauri delegates to the system webview and this app leans hard on
**PDF.js, KaTeX, and SwiftLaTeX WASM**; a pinned Chromium is worth the bundle size, which is
irrelevant for a Linux-only self-install.

**Transport.** The sidecar binds **`127.0.0.1` on an ephemeral port**; Electron passes the port
plus a **per-launch bearer token** to the renderer. The token is mandatory — any local process,
including any web page in any browser, can otherwise reach a localhost port. The WebSocket
protocol (D18 node 5) is unchanged; it terminates on loopback instead of the internet. REST +
the generated api-client (D10) are unchanged.

**Cold start.** Electron shows the window immediately with a readiness strip; the sidecar
reports **per-capability readiness** as it warms. **ML models load lazily** — importing torch and
the embedding model takes 5–15 s and must never block first paint. Search, notes, and the vault
tree are usable before embeddings are. Escape hatch if that irritates in daily use: run the
sidecar as a **systemd user service** so it is always warm and Electron just attaches.

**Distribution and updates:** `git clone` + `make dev`, then `git pull` + rebuild. No AppImage,
no packaging pipeline, no updater.

### D3 — The vault: files are truth

```
~/ResearchOS/
  library/
    papers/<canonical-id>/       paper.pdf, parsed.json      ← GLOBAL, stored once (D25)
  projects/<project-slug>/
    notes/                       *.md            ← project-owned, never shared
    papers/                      symlinks → library/papers/<id>  + papers.md index
      highlights/<canonical-id>.json   ← vault mirror of that paper's highlights (see below)
    experiments/<exp-slug>/      notebook.ipynb, requirements.txt, outputs/, runs/
    manuscript/                  *.tex, figures/
    project.md                   focus seed, interest profile (human-readable)
  .research-os/                  Postgres data, model cache, blob cache  ← REBUILDABLE
```

- **Everything outside `.research-os/` is truth**: notes (`.md`), PDFs, experiment code and
  outputs, manuscripts. Everything inside is derived and may be deleted at any time; the app
  rebuilds it from the vault.
- **Postgres is truth only for machine-derived data**: embeddings, `tsv`, parsed sections,
  extractive cards, graph edges, caches. Cost of a rebuild, stated honestly: re-parsing and
  re-embedding the corpus takes **time (minutes to hours), not data**.
- **`papers.md` per project** answers "which papers were relevant to this project, and why" — a
  human-readable list with the user's relevance note, readable in any text editor with the app
  closed. The `project_papers` DB row is the queryable mirror, not the source.
- **Symlinks, not copies** — one PDF, one parse, one set of embeddings (D25).
- **One blob class.** The vault holds the PDF. No eviction tiering, no content-hash-only class.
  Re-fetch by canonical id remains as a *repair* path for a missing file.
- **"Obsidian-inspired" means the storage *concept*, not interoperability.** What is borrowed:
  your data is plain files in a folder you own, readable and portable without the app, not
  locked in a database. What is **not** intended: running Obsidian against this vault, or
  supporting Obsidian's wikilink/plugin conventions.
- **No multi-machine sync.** No sync layer, no schema concession to a future one. The vault is a
  plain folder — point Syncthing or git at it later without the app knowing.
- **Highlights are user-authored, so they mirror to the vault**, at
  `projects/<project-slug>/papers/highlights/<canonical-id>.json` — one file per paper, keyed by
  the note-identity rule in D4 (stable id, never file path). Postgres stays the queryable copy,
  same as `papers.md`. **Conversations and messages stay DB-only** — they are derived Companion
  output, not user-authored content, so they do not get a vault mirror. (This resolves a prior
  contradiction where highlights were implied to live only in Postgres like conversations; user
  decision 2026-08-01: mirror highlights only.)

### D4 — The app is the sole writer of the vault

**The application is the only writer of the vault, and may assume so.**

- **No file watcher, no debounce, no hash-diffing, no conflict detection, no startup
  reconciliation.** All of it was insurance against a scenario that does not exist (external
  editors were never in scope — see D3). Dropping it removes a genuinely fiddly subsystem.
- The app writes files and updates the index **in the same operation**, so disk and DB cannot
  drift. Staleness is not a failure mode when there is one writer.
- **Ownership and portability were the point**; concurrent external editing never was.

**One piece deliberately retained:** **key notes in the DB by a stable id carried in YAML
frontmatter, never by file path.** It costs nothing now and is the one thing here that is
**painful to retrofit** — path-keyed rows mean every highlight, graph edge, and citation breaks
the first time a file moves, and converting later is a data migration rather than a code change.
It also leaves the door open to external editing without redesigning identity.

*Deferred, not forgotten:* if external editing is ever wanted, the dropped design (watchdog +
SHA-256 gate, never blind-overwrite, file-beats-DB, startup reconciliation) is in this file's git
history at commit `b53bff8` and can be lifted back wholesale.

### D5 — Build order: five slices, text first

| Slice | Contents |
|---|---|
| **1** | Project workspace + AI research search + reader with ask-about-highlight + notes + retrieval over everything read |
| **2** | **Experiments** — notebook UI, Docker sandbox, kernel, consent gate, structured experiment record (D29–D31) |
| **3** | Reader depth + literature matrix |
| **4** | Writing workspace (LaTeX) |
| **5** | Research feed |

- **Text-first.** Slice 1 is built and hardened over **text**, where every harness event is
  debuggable.
- **Voice is a cross-cutting layer added right after Slice 1** (D36) — it only needs the tool
  layer, and it stays behind its module boundary (D37).
- **Experiments are Slice 2** because they are the second-largest subsystem in the app and the
  capability that justified the desktop shape at all. Building them last would mean the reason
  for the pivot arrives last.
- **The knowledge graph accretes across slices** (D26) rather than owning one.

---

# Part 2 — Stack

### D6 — FastAPI (Python) backend + React (Vite, TSX) frontend

API-first. Next.js was rejected — it fights an API-first design. The API-first choice paid off
exactly as intended at the desktop pivot: the React build and the FastAPI backend were unchanged;
the Electron shell simply wraps them (D2).

### D7 — Postgres only

`pgvector` for embeddings, `tsvector` for BM25, join tables + recursive CTEs for the knowledge
graph, JSONB for paper metadata. No Qdrant, no Neo4j. Split a store out only when a query
actually measures slow — it will not at solo-researcher data volumes.

### D8 — Postgres runs locally, in Docker

`docker compose` brings up **Postgres + pgvector**, data under `.research-os/`, started and
health-checked by the sidecar on launch. Chosen over embedded binaries (`pgserver`) and over
SQLite + `sqlite-vec`: **Docker is already a hard dependency for D30**, so this adds one
container and zero new concepts, and it keeps D7, D9, D25 and the whole retrieval design
byte-for-byte unchanged. SQLite would have been simpler to ship but would have forced a rewrite
of the pgvector/tsvector hybrid retrieval and the Postgres job queue.

### D9 — Background jobs: Postgres-backed queue, catch-up-on-launch

**Postgres-backed queue** (SAQ with a Postgres backend, or pgqueuer). No Redis — transactional
enqueue, one less service. Jobs: PDF fetch/parse, embedding, structured extraction, feed
polling, **experiment container runs** (long-running and cancellable, D30).

**Cadence: catch-up-on-launch, not cron.** A desktop app only runs when the user opens it, so
"daily cron" does not exist. On startup the sidecar checks `last_run_at` per scheduled job and
runs anything overdue, once. This covers feed polling and weekly interest-profile re-extraction.

### D10 — Repo: flat monorepo with npm workspaces

```
backend/               FastAPI sidecar (harness, AI, Docker orchestration) — Python only
frontend/              Vite + React renderer
desktop/               Electron main + preload — launcher/supervisor, ~300 lines, no logic
packages/api-client/   TS client generated from FastAPI's OpenAPI schema
```

The generated client is the load-bearing part: a backend field rename becomes a **frontend
compile error**, not a runtime `undefined`. Regenerate on every backend change. An `apps/`
prefix was considered and dropped as ceremony.

**`desktop/` must stay dumb.** It spawns the sidecar, owns the window, and proxies native
dialogs. No AI, no business logic, no data access — that is D17, restated for the shell. Logic
leaking into `desktop/` is the failure mode to watch for.

---

# Part 3 — LLM layer

### D11 — BYO API key + local models, both first class

The user supplies their own LLM access. Two equally supported paths:

- **Remote, BYO key** — ~6 first-class providers: **Google, Groq, OpenAI, Anthropic, OpenRouter,
  DeepSeek**, plus **Custom / OpenAI-compatible (base URL)**. Onboarding leads with free tiers
  (Groq, Google AI Studio) so a user without budget can still start.
- **Local, zero API spend** — **Ollama and vLLM as named, first-class entries** in model
  settings, **not** buried under "Custom". Each takes a **base URL and no API key, and the UI
  must not demand one.** **Model discovery**: query the endpoint for available models rather
  than making the user type a model string. Researchers with a decent GPU should be able to run
  this for nothing.

**Tool-calling varies wildly across local models**, so D18 node 6's prompted-structured-output
fallback is load-bearing here, not a nicety.

**Invariant #1 is under active threat in this decision:** once a local model server is running,
routing embeddings through it looks natural. **Do not.** Embeddings stay on the pinned local
model forever (D14).

**GPU contention is real and deliberately unhandled in v1.** A resident vLLM server holding VRAM
will starve an experiment container and vice versa. No detection, no arbitration, no automatic
eviction — the user stops one or the other by hand. A VRAM scheduler for a single-user app is
exactly the over-engineering this project avoids; revisit only if it becomes a daily annoyance.

### D12 — LiteLLM as the provider abstraction

One `llm.complete()` call across 100+ providers; retries, streaming, cost tracking, key routing.
**No native provider SDKs in application code.** LiteLLM already speaks `ollama/*` natively and
reaches vLLM through its OpenAI-compatible endpoint, so D11's local support is configuration,
not architecture.

### D13 — Key storage and model configuration

- **Master key in the OS keyring** (`libsecret` via the `keyring` package) — never in the vault,
  never in the DB, never in the repo. Keys are stored encrypted (AES-256-GCM), decrypted
  **in-memory at call time** only, never logged; the UI shows `…last4` only.
- **Config:** per-provider keys stored; the user selects a **primary model + an optional
  auxiliary model** (D18 node 6). **Validate on save** with a test call, and surface the
  available models.
- **Entry points:** a "Models" settings page and the onboarding wizard (D35).

*(See "Open at implementation time" — the encryption layer may be redundant now that key and
ciphertext sit on the same single-user disk.)*

### D14 — Embeddings: one fixed model, forever

**`Alibaba-NLP/gte-modernbert-base`** — 768-dim, English, 8192 ctx, dense retrieval, CPU-served
in-process via `sentence-transformers`.

**Embeddings are deliberately not configurable — this is invariant #1.** Chat models can be
swapped freely; the embedding model cannot, because changing it silently invalidates every vector
in the index. Changing it means re-embedding the entire corpus.

Chosen over BGE-M3 because usage is **English-only and dense-only** (BM25 is handled separately
by `tsvector`, so BGE-M3's multilingual and hybrid-sparse edges are unused), and embedding is
**async** (D9) so CPU speed never touches UX. 768-dim also means lighter vectors.

### D15 — Model picks

- **Embedding (permanent, invariant #1):** `Alibaba-NLP/gte-modernbert-base` — see D14.
- **Reranker (swappable, in the request path):** `cross-encoder/ms-marco-MiniLM-L-6-v2` —
  light and fast on CPU; upgrade to `bge-reranker-v2-m3` with a GPU. No reindex to swap.
- **Dev / default LLM (swappable):** Gemini 2.5 Flash (Google AI Studio free tier — strong
  tool-calling, $0); Groq Llama 3.3 70B as the fallback dev target; auxiliary tier → Gemini
  Flash-Lite. This is only the build target — users bring anything (D11/D12).
- **Parsing:** docling, in-process Python. A GROBID JVM service was collapsed away; it returns
  as a service only if docling's reference extraction proves insufficient.

### D16 — Natural language → actions: pure agent, no hardwired fast path

A small set of **typed tools** (D19). **Every turn goes through the single-agent loop** (D18
node 1).

A hardwired ~15-intent classifier/regex fast path was designed and then **dropped**: it is
hand-maintained hardwiring that rots, and it contradicts the harness goal of making obvious
things work *without* hardwiring. A latency optimisation via a small **routing model** (never a
regex table) is future scope. Graceful degradation on weak models is preserved by the
**prompted-structured-output fallback** (D18 node 6), not by a regex table.

---

# Part 4 — The harness

### D17 — Fat backend, thin frontend

The **sidecar is the single core**: it thinks (the harness), stores (papers, notes, experiments,
index), and runs the agent loop. The **frontend is a window** — it captures user input and UI
state and renders events; **no business logic on the client.** The Electron main process is
thinner still (D2/D10).

Minimise data transfer: the model gets tiny summaries; the client gets **scoped, referenceable
payloads it pulls lazily by id** (D18 node 3).

### D18 — The harness (7 nodes)

The harness is an **agent runtime** (Claude-Code / "Hermes"-style loop) living **inside the
FastAPI sidecar**, adapted for a **windowed UI** rather than a terminal. Two consequences of
"UI, not terminal" drive the design: **(a) tool results are dual-channel** — a compact
`model_view` for the LLM and a rich `ui_view` for the frontend; **(b) UI state is part of
context, bidirectionally** — what is open or highlighted flows *into* the loop, and some tools
flow *out* as UI commands. That coupling is what makes obvious things work without hardwiring.

| # | Node | Decision |
|---|---|---|
| 1 | **Control loop** | Single-agent tool-calling loop. Subagents exist only **as tools** (e.g. `deep_research`), never as top-level orchestration. **Hard iteration cap** (~8–10) with a graceful stop. |
| 2 | **Context assembly** | **Hybrid.** *Ambient (always-on, deterministic):* system prompt + provenance rules, tool schemas, **live UI/workspace state**, compact working set (active items as ids/titles). *Deep memory (demand-driven):* `query_memory` returning **cited rows**. History **compacted** past a budget (full history stays in the DB); eviction order: working set → history → per-turn retrieval; system/tools/UI-state never evicted. |
| 3 | **Tool layer** | Reference-based `ToolResult` = `{model_view` (tiny summary)`, ui_view` (renderable, **by id**, never in LLM context)`, refs` (stable ids)`, ui_actions` (UI commands)`}`. Large results → **server-side result store**, keyed by id; the model manipulates handles, not payloads. Taxonomy: **Query / Action / MCP-bridged**, one contract. **Native-first**; MCP is the extensibility lane, not the core mechanism. |
| 4 | **Memory** | One **project-scoped pgvector + tsvector index** over *all* artifacts (papers chunked, notes, experiments, conversations), tagged `{type, source_id}`; hybrid retrieval → rerank → cited rows. **Write path = hybrid:** explicit artifacts are **user-authored ground truth**; conversations persist **verbatim + a summary-as-index** (recall links back to verbatim turns); **no AI-invented standalone facts.** **User-visible and editable.** Compaction is a *window* op, not forgetting. |
| 5 | **I/O** | **WebSocket** (bidirectional, single channel) over loopback (D2). Typed event stream — *down:* `status / text_delta / tool_call / tool_result(ref) / ui_action / turn_complete / error`; *up:* `user_message / ui_state / interrupt`. **UI-state snapshot attached to each `user_message` + incremental `ui_state` pushes** mid-turn. **First-class interrupt** (cancels the turn; partial results retained). |
| 6 | **Model & turns** | **Pure agent** (D16). **Primary + optional auxiliary model tier:** the user sets a primary chat model (D11/D13); auxiliary tasks (extraction, summarisation, interest classification) default to a cheaper model, else fall back to primary. **Prompted-structured-output fallback** for models without native tool-calling. Embeddings and reranking are non-LLM. |
| 7 | **Runtime shape** | **In-process async** cancellable `asyncio` task per turn, bound to the WebSocket session (this is what makes interrupt real). I/O-bound steps `await`ed inline; **CPU-bound steps (embed, parse, rerank) offloaded to the D9 queue** — never block the event loop. Turn state in-process but **persisted incrementally**. The harness is a **self-contained, extractable package** (`backend/harness/`). |

### D19 — Tool catalog

**Q** = Query, **A** = Action. Slice-1 set: Discovery / Reading / Memory / Mutations / Nav.

- **Discovery:** `search_papers(query, filters?)` **Q** → `result_id` + summaries;
  `refine_results(result_id, filters)` **Q**; `add_paper(link|id|upload_ref)` **A**.
- **Reading:** `get_paper(paper_id, include=[card|sections|references|datasets|code])` **Q**
  (one parameterised tool, not five); `compare(paper_ids[])` **Q**;
  `open_reference(paper_id, ref_id)` **A**.
- **Memory:** `query_memory(query, types?)` **Q** → cited rows.
- **Mutations:** `save_note` / `update_note` **A**, `mark_relevant(paper_id, level)` **A**,
  `create_highlight(paper_id, anchor)` **A**, `log_experiment` / `update_experiment` **A**.
- **Navigation** (emit `ui_actions`): `open_paper`, `scroll_to`, `highlight_span`,
  `open_view(matrix|graph|feed|experiments)`.
- **Execution (Slice 2, D30/D31):** `propose_cell(experiment_id, code)` **A** — writes a
  notebook cell, **never executes**; `run_all(experiment_id)` **A** — **requires explicit user
  confirmation**, and is the only path to a `source: measured` metric; `read_run(run_id)` **Q** —
  outputs and logs. **`run_all` is the only tool in the catalog that cannot complete without a
  human**, and that is deliberate. The `ui_action` lane carries the approval prompt.
- **Later slices:** `build_matrix` / `update_cell` (S3); `insert_citation` / `check_citations` /
  `find_missing_citations` (S4); `get_feed` / `save_feed_item` / `dismiss_feed_item` /
  `get_interest_profile` / `update_interest_profile` (S5); `get_graph` / `find_related` (graph
  viz, accretes across slices).

**Design rules.** Reader Q&A is **not a tool** — it is the core agent loop answering from ambient
UI-state plus retrieval tools (no redundant `ask_paper` hop). Prefer **moderate-fat**
parameterised tools over many thin ones. **MCP:** build the adapter as the extension seam, but
**bundle zero MCP servers in v1** — native covers v1.

---

# Part 5 — Retrieval, content & knowledge

### D20 — Search: live federated, rerank, cache

One LLM query-understanding pass → parallel fan-out to academic sources → dedupe by canonical id
→ cross-encoder rerank top ~100 → cache results in Postgres keyed by `result_id`.

**No owned index.** A 250M-work OpenAlex snapshot is a whole project before feature one works.

### D21 — Search federation: three tiers

- **Primary fan-out (every search):** **arXiv** (OA full text + discovery), **OpenAlex**
  (metadata / citations / concepts; also feeds feed categories and graph edges), **Semantic
  Scholar** (citations / influential-citations / TLDR / OA links).
- **Enrichment (on paper *open*, not every search):** **Papers with Code** (code / datasets /
  benchmarks → card fields + PwC canonical ids), **GitHub** (repo details, "open
  implementation").
- **On-demand resolver:** **Crossref** — only to resolve a DOI that OpenAlex and S2 missed.
- **Query handling:** **one** LLM query-understanding pass → `{keywords, filters: year / venue /
  has_code / author}` → deterministic per-source parameter mapping. Not N per-source LLM
  rewrites.
- **Dedup** on the D25 canonical id; **rerank** with the D15 cross-encoder; **cache** as a
  `result_id` in the D18 node 3 result store.

### D22 — Structured extraction: two-stage, lazy

1. The results list shows **abstract summary + metadata only** (title, venue, year, citations,
   code link, source link).
2. On opening a paper: the full structured split (**Problem / Method / Datasets / Results /
   Limitations**), derived **strictly from the paper's own content and section headings** — no
   outside knowledge, no inference.
3. If marked relevant → the paper joins the project's library (membership + the user's
   relevance note, D3/D25).

Extraction is **extractive-only** and governed by D24. Cache derived artifacts globally by
canonical paper id — compute once, ever.

### D23 — Full text: open access + user upload, graceful degradation

Fetch OA PDFs (arXiv, Unpaywall, S2 OA link); otherwise the user drags in their own copy. Parse
with **docling** in-process (sections, references, figures, equations).

**Never scrape paywalls** (invariant #3). If no full text is available: show the abstract only,
plus a link to the source it came from. **No fabricated structured card.**

### D24 — Provenance: evidence over generated text, enforced structurally

**Enforced by mechanism, not by prompting.**

- **Extraction cards (D22) and literature-matrix cells (D27) are extractive-only.** Every field
  is a **verbatim span** `{value, quote, char_offsets, section_heading}` where the quote is an
  exact substring of the parsed document. A **deterministic, non-LLM substring validator**
  confirms the quote resolves to real text at the claimed offsets; if it fails, the field is
  dropped and rendered **"not stated"** — never as unverified prose.
- **Memory recall cites source row ids.** A recalled item cites the note / paper / conversation
  row it came from; verification is trivial (the row exists).
- **Reader answers (ask-about-highlight) are grounded-generative.** Free reasoning is allowed,
  but **any factual claim about the paper carries an inline citation** to a span (the highlight
  or retrieved passages), and **quoted evidence is visually distinct from the model's
  reasoning**. Cross-paper claims cite spans in *both* papers from the memory layer; if the
  compared paper is not in the read set, the model says so rather than reciting training
  knowledge. The **same validator** runs on every cited span; a span that does not resolve is
  stripped and its claim flagged unverified.
- **Measured experiment results (D29) are the one non-quote form of evidence** — a number backed
  by a reproducible execution rather than a span.

**Through-line: no field or claim is shown as coming from a paper unless its supporting quote
provably exists in the source.**

*(A two-layer extractive-value → grounded-paraphrase display is future scope. v1 is
extractive-only.)*

### D25 — Data model: the global / project boundary

The load-bearing schema decision: **what is global (computed once, shared) vs project-owned.**

- **Global — keyed by canonical paper id, computed once, shared across all projects:** `papers`
  (canonical id + JSONB metadata + all source ids + abstract), `paper_content` (parsed sections /
  full text), `paper_cards` (extractive cards + quote anchors), `paper_chunks` (embeddings +
  `tsv`), `paper_edges` (metadata + LLM-derived *paper-intrinsic* edges).
- **Project-scoped — the user's workspace:** `projects` (name, interest profile JSONB, seed),
  `project_papers` (membership + relevance level + **why it's relevant**, user-authored), `notes`,
  `experiments`, `conversations` + `messages` (verbatim), `project_chunks` (embeddings of notes /
  experiments / conversation summaries), `highlights` (quote anchors), `feed_items` / `seen_set`,
  `idea_edges`.
- **Local settings:** `api_keys` as a single-row local settings store (D13). There are no
  account-level tables — no `users`, no `owner_id`, no `storage_connections`.

**Papers are per-project at the level of membership, not content.** Duplicating a PDF into three
projects would mean three parses, three sets of cards, and three sets of embeddings for one paper
— destroying the canonical id, the "compute once, ever" constraint, and cross-project dedup. So
content is global and *membership, relevance, notes, highlights and matrix placement* are
per-project. **Notes are project-owned in full and never leak across projects.** On disk this
reads as per-project anyway (D3: symlinks + `papers.md`).

- **Reconciliation ("compute once" ↔ "project-scoped memory"):** the project memory index is a
  **query-time union**, not a table — memory(P) = `paper_chunks`(papers in P) ∪
  `project_chunks`(P). Paper embeddings are computed **once globally and reused**; retrieval
  stays **project-isolated** via the membership filter.
- **Canonical paper identity:** normalise by priority **DOI → arXiv id → OpenAlex/S2 id**; all
  source ids retained. This is the dedup key and the graph-edge key.
- **Memory tables: two** — `paper_chunks` (global, no `project_id`) and `project_chunks` (has
  `project_id`), both `{embedding vector(768), tsv, source_type, source_id, char_span}`.
- **Chunking: section-aware** — split on docling section boundaries, sub-split long sections to
  a token budget with small overlap. This aligns chunks with the quote-anchor/provenance model.

### D26 — Knowledge graph: metadata-first, LLM only on opened papers

- **Free and exact edges from APIs:** cites / cited-by (OpenAlex, S2), authored-by, uses-dataset
  and has-code (Papers with Code), topic tags.
- **LLM-derived edges** (method→method, idea→paper) are extracted **only for papers the user
  actually opened**, reusing the D22 extraction pass. A graph whose value is trustworthiness
  cannot afford hallucinated edges.
- **Scope = a project-scoped union** — edges among the project's papers + `idea_edges` + the
  relevant global `paper_edges`. Not a global blob.
- **Node identity, split by trust:** canonical ids for API entities (OpenAlex author id, PwC
  dataset id, repo URL, D25 paper id); **LLM-derived method/concept nodes get light
  normalisation** (lowercase / alias / embedding-merge) and are **dup-tolerant** — under-merging
  beats false-merging for a trust graph.
- **Built incrementally in the extraction pass.** No separate build step, no dedicated slice.

### D27 — Literature matrix

- **Standard cells are a projection of existing extractive cards** (Problem / Method / Datasets /
  Results / Limitations) — **no re-extraction**, provenance-safe by construction — plus a
  **Personal notes** (user) column. Strengths is user-authored or extractive.
- **Custom columns are a per-paper scoped extractive query**, cached per `(paper, column)`, with
  the **"not stated"** fallback (D24 holds).
- **Editable cells become user-authored with a `source: extracted|user` flag** — editing
  *labels* an override, it never corrupts provenance.
- **Persisted** as a project artifact: `{selected_paper_ids, column_defs, cell_overrides,
  custom_column_cache}`.
- Experiment records (D29) sit in the same matrix as comparable rows, since their `metrics` share
  the `{name, value, unit?}` shape.

### D28 — Research feed

**Pipeline: interest profile → category fetch → keyword rank → dedup → catch-up-on-launch poll.**

- **Interest profile** — inspectable and **user-editable** `{categories, keywords}`. Keywords are
  **synonym-expanded** at extraction time (counters brittleness, e.g. RAG ↔ retrieval-augmented
  generation). Extracted from the project corpus by an **infrequent LLM classification pass**
  (weekly, or on meaningful corpus growth; cached). Categories anchor to each source's native
  taxonomy — the arXiv tree is the anchor.
- **Fetch is category-driven (broad recall), not keyword-driven.** Broad category recall never
  drops a relevantly-but-differently-phrased paper. Per source, windowed to "since last poll",
  recency-sorted.
- **Rank** — synonym-expanded keyword match + **embedding centroid cosine** + **cross-encoder
  rerank** of the top N. **No LLM in the scoring path.** `why-relevant` = matched keywords and
  categories + similarity (deterministic; an optional one-line LLM gloss is low-stakes).
- **Dedup** — normalised DOI/arXiv id against the **seen set** = read ∪ library ∪
  previously-surfaced ∪ dismissed.
- **Cadence** — a **D9 job per project, run catch-up-on-launch** rather than on a cron. The feed
  is a scheduled harness job, never a live request path.

**Profile lifecycle:**
- **Bootstrap** — a **user-declared seed at project creation** (a sentence of focus + optional
  seed papers). The seed *is* the initial editable profile; seed papers seed the corpus centroid.
  This doubles as onboarding; there is no cold-start magic.
- **Refine** — **save** → the item enters the library → shifts the centroid and feeds the next
  re-extraction; **dismiss** → the seen set (never resurfaces) plus a light down-weight of
  very-similar items in the same poll; **explicit edits** always available; **weekly
  re-extraction** reconciles the profile with the evolved corpus. Full negative-example learning
  is future scope.

---

# Part 6 — Experiments & execution

### D29 — The experiment record

Each experiment is a structured record — a lab notebook, not a live run-tracker (W&B/MLflow
integration is future scope, and this *is* its ingestion path arriving early):

- `hypothesis` (text)
- `setup` (model / dataset / config — text or light structure)
- `metrics` — a structured list `[{name, value, unit?, source}]`
- `notes` (free-form markdown for everything that does not structure cleanly)
- `status` enum — planned / remaining / in-progress / done
- **graph links** — inspired-by paper, uses-dataset, references-note
- `runs[]` — `{run_id, started_at, exit_code, image, reqs_hash, notebook_hash, stdout_ref,
  artifacts[]}`

**Three metric sources, and only three:**

- **`source: user`** — typed in by hand. The researcher vouching for a number. Fully supported.
- **`source: measured`** — **captured from a clean "restart kernel and run all" that exited
  successfully** (D30), linked to `run_id`, image digest, `requirements.txt` hash, notebook hash
  and timestamp. **This is the strongest provenance in the entire system.** Interactive,
  out-of-order cell runs **never** produce a `measured` metric, because hidden kernel state makes
  the number unverifiable — that is the price of an interactive kernel, and it is paid here
  rather than by weakening the provenance rule.
- **`source: llm`** — **forbidden.** The AI may *propose* code and *read* results; it may
  **never author a metric value.** D24's through-line is unchanged.

Structured `metrics` make experiments **comparable rows** that sit in a matrix alongside papers'
extracted results (D27).

**In v1 the structured experiment record is indexed and retrievable** (hypothesis, setup,
metrics, notes, status, graph links); **the `.ipynb` file itself is not embedded.** So
`query_memory` can find "the experiment where I tried X" but not a specific line of code inside a
notebook. The notebook remains a plain, greppable file in the vault.

### D30 — Execution: Docker sandbox, always

**Every piece of code this system runs, runs in a container. No exceptions, no opt-out**
(invariant #4).

The threat is not the user's own scripts — it is that **the agent writes and runs code, and the
agent reads PDFs from the open internet.** A prompt-injected paper that talks the agent into
running something is a realistic path to arbitrary code execution on the user's machine.

- **Docker containers**, chosen over bubblewrap/firejail: strongest isolation for the effort, and
  **reproducible environments are something researchers want anyway** — the sandbox and the
  reproducibility feature are the same mechanism. **Docker is a hard dependency**, checked at
  onboarding (D35).
- **Execution model: a Jupyter kernel running *inside* the container.** The container is
  **per-experiment and long-lived**, not per-run: it starts when the experiment is opened, holds
  kernel state across cells, and is torn down on close or idle timeout. The notebook is a
  **`.ipynb` file in the vault** (D3) — truth on disk, like everything else.
- **Base image:** pinned, with the usual stack (numpy / pandas / torch / scikit-learn /
  matplotlib) so a kernel starts in ~1 s. **Per-project dependencies via `uv` +
  `requirements.txt`** in the experiment folder, layered on top.
- **Mounts:** the project's `experiments/<exp>/` read-write, `library/` read-only if the run
  needs paper data. **Nothing else.** Never the whole vault, never `$HOME`.
- **Network: off by default** for the kernel. Dependency installation happens at **image-build
  time**, where network is expected and fine, not at execution time — so a *running* kernel stays
  offline. Dataset downloads are an explicit per-experiment opt-in, recorded in the run record,
  because a networked run is a less reproducible run.
- **Limits:** CPU, memory, idle timeout, per-cell wall-clock timeout. **GPU via `--gpus`** +
  nvidia-container-toolkit, opt-in per experiment. No GPU arbitration (D11).
- **Cell execution and kernel lifecycle ride the D9 queue** — cancellable, with logs and results
  streaming to the UI over the existing WebSocket (D18 node 5), where interrupt is already
  first-class.

**Reconciling an interactive kernel with reproducible provenance.** A stateful kernel is the
right tool for research and the wrong tool for evidence. The two are separated rather than
compromised: **exploration is free** (run cells in any order, any number of times — nothing is
recorded as a measurement), and **a number only becomes evidence via a clean restart-and-run-all**
(D29). The discipline of one clean run before a number counts is the entire cost.

### D31 — Consent: the agent never runs code on its own

**Propose → user approves → execute.** The agent may write code into a cell and may read results,
but **execution is always a human action** (invariant #5). There is no auto-run, no "trusted
experiment" mode, and no blanket per-project approval that would let a later agent-authored cell
run unattended.

**The sandbox and the consent gate are independent controls, and both are required.** Docker
limits what a run can damage; it says nothing about whether the run should have happened. The
realistic attack — a prompt-injected paper persuading the agent to write and execute something —
is stopped by the gate, not by the container.

The approval prompt shows **the code and the container spec** (image, mounts, network, GPU). The
tools are `propose_cell` / `run_all` / `read_run` (D19). Agent-written cells must be **visually
marked as unrun and pending approval** (D32) — the user must never be unsure whether something
executed.

---

# Part 7 — Frontend

### D32 — Workspace shape

- **Layout = top bar + persistent 3-pane shell.** Top bar (project switcher / breadcrumb /
  active-paper title / settings); **left** nav (papers / notes / experiments tree); **center**
  active view (reader / notebook / matrix / graph / feed / writing); **right** **persistent
  Companion chat** (always visible and addressable).
- **The persistent chat is the Companion, and the Companion is the product.** One WebSocket
  session **per project**, surviving center-pane navigation. Voice (D36/D37) drops in as another
  transport into the same session — **no logic rewrite**. Clicking in the UI, typing, and
  speaking all resolve to the **same tool call + route transition** (one path, D17/D18).
- **State:** **React Query** for all REST server data (cache / invalidate / loading) + a
  **WebSocket event bus** (harness stream → chat transcript, invalidates the RQ cache on
  `tool_result`, dispatches `ui_action` to the router) + **Zustand** for local UI state (open
  pane, selection) — which *is* the `ui_state` payload sent up (D18 node 5).
- **Routing (React Router):** the URL owns **project + center-pane content**
  (`/p/:projectId/paper/:paperId`, `/experiments/:expId`, `/matrix/:id`, `/graph`, `/feed`,
  `/write/:docId`). Chat and nav are persistent shell, not routes. `ui_action` events drive the
  same router.
- **Experiments center pane = a notebook UI** over the container kernel (D30): cell list, run
  controls, streamed output, kernel status, and a visible **"restart & run all"** action — which
  is also the only action that produces a `measured` metric, so it must read as a deliberate step,
  not a refresh button. **Cell editor = CodeMirror 6**, the same editor as LaTeX; Monaco was
  rejected as a second heavy dependency for a familiarity gain that does not matter at this scale.
- **Style:** **light-only, academic, serif for reading and sans for chrome**, comfortable
  measure, soft contrast, minimal chrome. The concrete palette and type ramp live in
  `UI_DESIGN.md` (cool blueprint: light-blue gridded frame, near-white floating panels, cyan
  accent, Space Grotesk chrome + Newsreader reading serif). A single theme — less to build.

### D33 — Reader UI

- **Render the real PDF via PDF.js** (figures, equations and layout intact). The docling
  structure is a **navigation + provenance overlay** (jump-to-section, references, datasets), not
  a reflowed text reader.
- **Quote-based (content-addressed) highlight anchoring** — `{quote, prefix, suffix}` (W3C
  `TextQuoteSelector` style) as the durable anchor, with a cached page + bbox as a rendering
  hint. **Shared with provenance:** an extractive card's `char_offsets` and a reader highlight
  are the same anchor object. It survives re-parsing (re-locate by searching the quote) and
  **bridges the two text streams** (docling text ↔ PDF.js text layer) because the quote is the
  lingua franca — string-search it in either. Requires a **normalising fuzzy locator**
  (whitespace / hyphenation / ligatures) — the same locator provenance needs, built once.
- **Interaction:** selection → inline popover ("Ask about this" / Highlight / Explain);
  collapsible **structure sidebar** (sections / references / datasets / code); **toggleable
  extractive card** whose fields click through to `scroll_to` + `highlight_span` on the source
  span; **cross-pane anchor sync** — chat citation ↔ card field ↔ PDF span all speak the same
  anchor.

### D34 — Writing workspace (LaTeX)

- **Editor:** **CodeMirror 6** with LaTeX highlighting + **live inline KaTeX math** (the Obsidian
  touch) + **debounced SwiftLaTeX WASM PDF preview** (~1–2 s, Overleaf-style — full WYSIWYG for a
  whole LaTeX document is not possible) + a compile-error panel.
- **Compilation: SwiftLaTeX WASM in the renderer is the default** — instant preview, no container
  spin-up per keystroke. **Tectonic in a Docker container is a shipped escape hatch** for full
  package coverage on final compiles; Docker is already a hard dependency (D30), so it costs one
  image.
- **GUI help:** toolbar snippets, command and `\cite` / `\ref` autocomplete (cite pulls project
  references), environment snippets, AI syntax assistance via chat. Insert: image upload → VFS →
  `\includegraphics`, **Mermaid** flowcharts, workspace **dataviz PNG/SVG** exports.
- **The AI never writes prose or paper sections.** It verifies, organises, checks citations, and
  finds missing ones; the researcher is the author.

### D35 — Onboarding

A **gated setup wizard** — all steps required, ending in a working project. Progressive/lazy
prompting was considered and **rejected** in favour of complete upfront setup.

1. **Environment check** — verify Docker is installed and the daemon is reachable (D30); offer
   the exact `dnf` / `systemctl` commands if not.
2. **Vault folder** — pick it, defaulting to `~/ResearchOS` (D3). **Not skippable** — nothing
   works without a vault.
3. **Models** — add and validate at least one LLM endpoint: a BYO key or a local Ollama/vLLM base
   URL (D11/D13). Free-tier links are shown so the step is never a dead end.
4. **First project** — name + an **optional, skippable focus seed** (a sentence + optional seed
   papers, which bootstraps the D28 interest profile).

No pre-loaded sample or demo project in v1.

---

# Part 8 — Voice

### D36 — Voice is a thin transport over the tool layer

Voice is **not** a separate mode, a separate agent, or a separate code path. It produces text and
consumes text; **the agent cannot tell how a turn arrived.** That property is the whole design:
the transport can change without the application noticing, which it already has twice.

- **Sequencing:** text-first. Voice is a cross-cutting layer added **right after Slice 1** (D5) —
  it only needs the tool layer.
- **Interaction: push-to-talk, not always-on VAD.** Simpler, no accidental capture, no idle CPU
  burn, no microphone running unprompted in a research tool. VAD is future scope.
- **If local STT proves too heavy, voice slips — nothing else changes.** Voice was never the
  load-bearing feature; the Companion is.

### D37 — Voice runs locally, behind one module boundary

**STT and TTS both run locally in the Python sidecar:**

- **STT: `faster-whisper`** (CTranslate2), model **`base.en`, int8** — comfortably real-time on
  CPU, far faster on a free GPU. ~150 MB of weights, downloaded once and cached in
  `.research-os/`. `whisper.cpp` is the fallback if the CTranslate2/torch dependency weight
  proves annoying — a small native binary with no Python ML stack behind it.
- **TTS: Piper** — small, fast, fully offline, acceptable quality. Linux `speech-dispatcher` is
  the zero-install fallback.
- **Lazy-loaded** like every other model (D2 cold start): the STT model must not load until the
  first time the user presses the talk key.

**Build order: infrastructure now, models later.** Build the plumbing — capture, transport,
playback, the module boundary — against a **stub engine** that returns canned text for STT and
silence (or an OS beep) for TTS, then drop `faster-whisper` in behind it. This keeps a ~150 MB
download and any GPU/CPU tuning off the critical path. **This path is not yet prototyped —
validate with a small spike before voice work starts.**

**The module boundary is the point of this decision — voice must be swappable in exactly one
place.**

- **`backend/voice/` is a self-contained package with a narrow, engine-agnostic interface:**
  `transcribe(audio_bytes, *, lang) -> Transcript` and `synthesize(text, *, voice) -> audio_bytes`,
  plus a small engine registry so `stub`, `faster_whisper`, `whisper_cpp`, or a future cloud
  engine are interchangeable implementations selected by config.
- **No other module may import an STT/TTS library, name an engine, or know a model exists.** The
  harness, the WebSocket transport, and the UI talk to `backend/voice/` only. If swapping the
  engine requires touching anything outside that package, the boundary is wrong and the fix goes
  into the package, not the caller.
- **`frontend/src/voice/` is the mirror module** — it owns microphone capture, push-to-talk
  state, and audio playback, and exposes one hook to the rest of the app. No component anywhere
  else touches `getUserMedia` or an audio element.
- **Model files, engine config, and warm-up all live inside the module** and stay lazy. Nothing
  outside it needs to know whether a model is loaded.

---

## Standing constraints (do / don't)

**Do**
- Cite a span for every extracted field; if the paper does not state it, print **"not stated."**
- Cache expensive derived artifacts (structured cards, rerank results) globally by canonical
  paper id — compute once, ever.
- Keep every capability reachable as a **typed tool**, so voice, text, and UI all share one path.
- Regenerate the TS API client from OpenAPI on every backend change.
- Write the file and update the index in the **same operation** (D4).
- Key notes by their stable frontmatter id, never by file path (D4).

**Don't**
- Don't make the embedding model configurable, and **don't route embeddings through Ollama or
  vLLM** just because a local server is running (D14, invariant #1).
- Don't scrape paywalled PDFs (D23, invariant #3).
- Don't let the AI write paper sections or author a metric value — it verifies and organises; the
  researcher authors (D24, D29, D34).
- Don't execute any code outside a Docker container (D30, invariant #4).
- Don't let the agent run code without an explicit user approval — sandboxing is not consent
  (D31, invariant #5).
- Don't let a `measured` metric come from anything but a clean restart-and-run-all (D29).
- Don't add a second datastore before a Postgres query actually measures slow (D7).
- Don't build a file watcher or conflict resolution (D4).
- Don't put logic in `desktop/` — the Electron shell is a launcher, nothing more (D2, D10).
- Don't build auth, multi-machine sync, GPU arbitration, or multi-tenancy — all out of scope by
  decision (D1, D3, D11).
- Don't put one-time setup in a request path.
- Don't store LLM keys unencrypted, or log them (D13).

---

## Open at implementation time

Not architecture blockers.

- **Sidecar ↔ Jupyter kernel transport** (D30) — likely `jupyter_client` over ZMQ to a kernel
  inside the container, ports published to loopback only. **A technical spike, not a decision —
  but a prerequisite of Slice 2**, since everything else in D30 assumes it works. It is the
  least-proven part of the design.
- **Notebook content in the memory index** (D29) — deciding what to chunk out of a `.ipynb` (code
  cells, markdown, text outputs; and what to exclude, like base64 images and stack traces that
  would pollute retrieval). v1 indexes the structured record only.
- **Local voice is unprototyped** (D37) — spike `faster-whisper` before voice work starts.
- **The key-encryption layer may be redundant** (D13) — key and ciphertext sit on the same disk,
  single user, no network service. Consider dropping AES-256-GCM for plain OS-keyring storage and
  deleting the crypto layer. Not urgent; it works as specified.
- **`UI_DESIGN.md` has no notebook screen** — it predates D30. The Experiments pane will be
  designed freehand against the existing visual language (inspiration-rank, so not blocking).
- **Feed tuning** — fetch-vs-rank balance as the corpus grows; interest-profile refresh cadence.
- **Full negative-example learning** for feed dismissals (v1 = light down-weight only).
- **Two-layer extractive → paraphrase card display** (v1 = extractive-only).
- **GROBID-as-a-service** upgrade if docling's reference extraction proves insufficient.

*(Next, on the user's explicit instruction only: PRD / TRD, with user-tagged skills.)*

---

## Appendix A — Retired paths: do not re-derive

Each of these was designed, then killed for the stated reason. They are recorded so no future
session proposes them again as if new.

| Path | Killed | Why |
|---|---|---|
| **Hosted, multi-tenant web app** | 2026-08-01 | Cannot reach the user's filesystem or GPU; experiment execution is core (D1). |
| **Single-tenant web app with auth kept "so scaling is a widening"** | 2026-08-01 | Multi-user is not a goal. A dead `owner_id` column is speculative ceremony; re-adding it later is one migration. |
| **Supabase** (GoTrue auth + managed Postgres + S3 storage + RLS) | 2026-08-01 | All three roles went local: auth dropped, storage → the vault (D3), Postgres → local Docker (D8). |
| **Auth: Google OAuth, email/password, anonymous demo door, JWT, `owner_id`, RLS** | 2026-08-01 | Single local user; the OS login is the auth boundary. |
| **$0 hosted deployment** (Cloudflare Pages + Hugging Face Space + Cloudflare Tunnel) | 2026-08-01 | No deployment exists. Two pieces survived into D2/D15: heavy ML collapsed into in-process Python libraries, and one Docker-composed unit. **Cost accepted: no clickable demo URL; demo by screen recording.** |
| **"Postgres is the durable truth, PDFs are a cache"** | 2026-08-01 | Inverted by D3 — files are truth, Postgres is a rebuildable index. Correct for a hosted app, wrong for a local one. |
| **BYO Google Drive / OneDrive blob storage via OAuth; three blob classes; storage quota; markdown export** | 2026-08-01 | Existed to dodge hosted storage cost, which no longer exists. The local folder *is* user-owned storage, which was the actual goal; notes were always markdown on disk. |
| **Browser Web Speech API as the v1 voice mechanism** | 2026-08-01 | `webkitSpeechRecognition` streams audio to a Google service authorised by a **proprietary key compiled into Google-branded Chrome**. Electron ships plain Chromium without it, so recognition errors out or returns nothing, forever. An entitlement, not a bug — unpatchable. Replaced by D37. |
| **Hosted WebRTC streaming STT → agent → TTS** | 2026-08-01 | Needs a media server and hosting; both gone. Superseded by local engines (D37). |
| **Local speech-to-speech model as the voice architecture** | 2026-07-21 | Hard to ground in workspace tools, and it collapses the "voice is a thin transport" property that makes the harness transport-agnostic (D36). |
| **Claude Pro/Max & ChatGPT/Codex subscriptions as LLM access** | 2026-07-21 | No API surface; ToS forbids it. Still true now that a local process exists — a local CLI shim is equally forbidden (invariant #2). |
| **Hardwired ~15-intent classifier / regex fast path** | 2026-07-23 | Hand-maintained hardwiring that rots, and it contradicts the harness goal. Replaced by the pure agent loop (D16). |
| **Hosted-core / desktop-harness partition** ("core remembers and indexes; harness reaches out and reasons") | 2026-07-23 | Explored during an earlier desktop exploration and dropped. Superseded by D17 + D18. |
| **Tauri as the desktop shell** | 2026-08-01 | Delegates to the system webview; this app leans on PDF.js, KaTeX and SwiftLaTeX WASM. A pinned Chromium is worth the bundle size for a Linux self-install (D2). |
| **SQLite + `sqlite-vec`, or embedded Postgres binaries** | 2026-08-01 | Would have forced a rewrite of hybrid pgvector/tsvector retrieval and the Postgres job queue, to avoid a Docker dependency already paid for (D8). |
| **Monaco as the code editor** | 2026-08-01 | A second heavy editor dependency for a familiarity gain that does not matter at this scale. CodeMirror 6 does LaTeX and notebook cells (D32/D34). |
| **Vault file-watching, conflict detection, startup reconciliation** | 2026-08-01 | Insurance against external editors, which are not in scope. The app is the sole writer (D4). Recoverable from git at `b53bff8`. |
| **Multi-machine sync** | 2026-08-01 | One device. The vault is a plain folder; point Syncthing or git at it. |
| **GPU arbitration between vLLM and experiment containers** | 2026-08-01 | A VRAM scheduler for a single-user app is over-engineering. The user stops one by hand (D11). |
| **Packaged distribution (AppImage, installers, code signing, auto-update)** | 2026-08-01 | Student scope, Linux only, one user. `git clone` + `make dev` (D2). |
| **Rate limiting** | 2026-07-23 | Dissolved by single-user. |
| **`FRONTEND_BRIEF.md`** | 2026-07-31 | Redundant with this file. Recoverable from git history if a screen-by-screen restatement is ever wanted. |
| **Warm off-white / sepia palette** | 2026-07-31 | Superseded by `UI_DESIGN.md`'s cool blueprint palette. Everything else about the visual direction (light-only, serif for reading, recessive chrome) stands. |

---

## Appendix B — Old → new decision IDs

Decisions were renumbered 1..37 in the 2026-08-01 rewrite. Prior sessions, commit messages, and
`UI_DESIGN.md` used the old IDs; this maps them.

| Old | New | Topic |
|---|---|---|
| D1 | **D1** | Target / product shape |
| D2, D23 (voice half) | **D36** | Voice as a thin transport, sequencing |
| D3 | **D5** | Build order / slices |
| D4 | **D6** | FastAPI + React |
| D5 | **D7** | Postgres only |
| D6 | **D20** | Federated search |
| D7 | **D22** | Structured extraction |
| D8 | **D23** | Full text, OA + upload |
| D9, D34 | **D11** | BYO key + local LLMs |
| D10 | **D12** | LiteLLM |
| D11, D24 (embedding) | **D14** | Fixed embedding model |
| D12 | **D16** | Pure agent, no fast path |
| D13 | *retired* | Supabase — Appendix A |
| D14 | **D9** | Background job queue |
| D15, D28 (graph) | **D26** | Knowledge graph |
| D16, D30 (writing) | **D34** | LaTeX / writing workspace |
| D17 | **D10** | Repo layout |
| D18 | *retired* | Hosted-core/desktop-harness split — Appendix A |
| D19 | **D17** | Fat backend, thin frontend |
| D20 | **D18** | The harness, 7 nodes |
| D21 | **D25** | Data model, global/project boundary |
| D22 | **D19** | Tool catalog |
| D23 (text-first half) | **D5** | Text-first build order |
| D24 | **D15** | Model picks |
| D25 | **D21** | Search federation tiers |
| D26 | **D13** | Key storage & model config |
| D27 | *retired* | Auth + demo mode — Appendix A |
| D28 (matrix) | **D27** | Literature matrix |
| D29 | **D35** | Onboarding wizard |
| D30 | **D32** | Frontend / workspace shape |
| D31 | **D2** | Electron shell + Python sidecar |
| D32 | **D3** | The vault, files are truth |
| D33 (sandbox) | **D30** | Docker execution sandbox |
| D33 (consent, Q40) | **D31** | Agent never runs code unapproved |
| D35 | **D8** | Postgres local in Docker |
| D36 | *dissolved* | "What the pivot doesn't change" — meaningless once rewritten; its invariants live in the Invariants section |
| D37 | **D4** | App is the vault's sole writer |
| D38 | **D37** | Local STT/TTS + module boundary |
| Q3 | *retired* | Deployment — Appendix A |
| Q5 | **D3** | PDF storage → the vault |
| Q7 | **D33** | Reader UI |
| Q18 | **D24** | Provenance enforcement |
| Q19 | **D29** | Experiment record |
| Q20 / Q4 | **D28** | Research feed |
| Q33 | **D33** | Quote anchoring |
| Q40 | **D31** | No agent auto-execution |
| Q41 | **D3** | No sync |
| Q42, Q43 | **D30** | Jupyter kernel, `uv` + base image |
| Q44 | **D4** | Sole writer |
| Q45 | **D11** | No GPU arbitration |
| Q46 | **D32** | CodeMirror 6 |
| Q47 | **D2** | `git clone` + `make dev` |
| Q48 | **D37** | Local voice |
