# Research Companion OS — Architecture Decisions

Recorded 2026-07-21, extended 2026-07-23, **pivoted 2026-08-01**. Companion to
`Research Companion Workspace OS.md` (the product vision). This file is the **how**; that file
is the **what**.

> **2026-07-23 session** resolved Q18 (provenance) and Q19 (experiment logging), amended D1
> and D12, added D19 (fat backend / thin frontend) and D20 (the harness design, 7 nodes). A
> mid-session pivot to a local desktop app — which reopened D2 and invariant #2 — was
> **considered and reverted**: the product is **fully web-based, single-user-first**. See the
> "2026-07-23 additions" section and the amendment notes on D1/D2/D12.
>
> ### ⚠️ **2026-08-01 — THE DESKTOP PIVOT IS NOW REAL (D31–D36).**
>
> The web-only stance above is **superseded.** Research Companion OS is now an
> **Obsidian-inspired local desktop application**: an Electron shell supervising a Python
> (FastAPI) sidecar, a plain-files vault on disk, an embedded Postgres index, and **Docker-
> sandboxed experiment execution as a first-class feature**. The trigger was a requirement the
> web framing could not satisfy: **running research experiments locally with real filesystem and
> GPU access.** Read **"2026-08-01 — the desktop pivot"** at the bottom before D1–D30; where the
> two conflict, the 2026-08-01 section wins.
>
> **Scope note:** this is a **solo, student-scope project** targeting **Linux, one user (the
> author)**. Windows/macOS builds, code signing, notarization, auto-update infrastructure, and
> multi-tenant scale are **explicitly out of scope** and are not to be raised as objections.

---

## Product shape

**D1. Target — hosted, multi-user, free (v1), real use + portfolio.**
Not a solo local tool, not a pure demo. Auth and multi-tenancy from day one; no billing in v1.

> **Amended 2026-07-23 — single-user-first, fully web, no browser-vs-desktop split.** Build
> **single-tenant** for v1 (one working user), and treat multi-user as a *widening* not a
> rewrite — **keep auth in** so scaling later doesn't require re-architecting. The product is a
> **fully web-based** workspace (React/Vite frontend + FastAPI backend); there is **no desktop
> client** and the browser is the only surface. A desktop-app pivot was explored this session
> and dropped. Scaling and multi-tenancy are explicit **future scope**.

> **SUPERSEDED 2026-08-01 — local desktop app, single user, no hosting.** See **D31**. The
> product is an **Electron desktop app** running a local Python sidecar against a local vault.
> There is **no hosted deployment, no multi-tenancy, and no auth in v1**. The reason the
> hosted framing failed: **local experiment execution** (filesystem + GPU) is a core research
> need that a browser sandbox cannot serve. Multi-user is no longer "future scope by design" —
> it is simply **not a goal**.

**D2. Voice — WebRTC streaming STT → text agent → TTS.**
Not a local speech-to-speech model (incompatible with hosted multi-user) and not a realtime
s2s API (cost, hard to ground in workspace tools). Voice is a thin client over the same tool
layer the text UI uses, so it can be swapped for realtime s2s later without touching the agent.

> **Re-confirmed 2026-07-23.** Briefly reopened during the desktop-app pivot (a local process
> could run a local s2s model); since the product stayed **fully web-based**, D2 stands
> unchanged — hosted WebRTC STT → agent → TTS. Same for **invariant #2** (BYO API key, no
> subscription auth): the local-CLI workaround needs a local process, which no longer exists.

> **Amended 2026-08-01 — voice moves fully local; D23's Web Speech plan breaks.** The
> **browser Web Speech API is not usable in Electron**: Chromium's speech recognition relies on
> a Google backend reachable only via a proprietary API key that ships with Chrome and is not
> present in Electron builds. D23's "$0, no media server" v1 voice mechanism therefore **does
> not survive the pivot**. Replacement: **local STT/TTS inside the Python sidecar**
> (`faster-whisper` for STT; Piper or the OS TTS for output) — genuinely $0, fully offline, and
> better privacy. Voice remains a **thin transport over the same tool layer**, so the harness is
> untouched (D2's core claim holds). **Invariant #2 still stands** — a local process now exists,
> but using a Claude/ChatGPT *subscription* through a CLI remains ToS-forbidden; BYO API key or
> a local model (D34) only.

**D3. v1 scope — everything, in four sub-slices.**

| Slice | Contents |
|---|---|
| 1 | Project workspace + AI research search + reader with ask-about-highlight + notes + retrieval over everything read |
| 2 | Reader depth + notes + literature matrix |
| 3 | Writing workspace (LaTeX) |
| 4 | Research feed |

Knowledge graph is built incrementally across slices (see D15) rather than as its own slice.

---

## Stack

**D4. FastAPI (Python) backend + React (Vite, TSX) frontend.**
API-first because a downloadable desktop app is planned. Next.js was rejected — it fights an
API-first design. Future desktop = **Tauri/Electron shell around the same React build**, not a
second UI codebase.

> **Confirmed + activated 2026-08-01 — the shell is now built, and it is Electron.** The seam
> this decision reserved is being used. **Electron over Tauri** because Tauri delegates to the
> system webview, and this app leans hard on **PDF.js, KaTeX, and SwiftLaTeX WASM** — a pinned
> Chromium is worth the bundle size, which is irrelevant for a Linux-only self-install anyway.
> API-first pays off exactly as predicted: the React build and the FastAPI backend are unchanged,
> the shell wraps them. See **D31**.

**D5. Postgres only.**
`pgvector` for embeddings, `tsvector` for BM25, join tables + recursive CTEs for the knowledge
graph, JSONB for paper metadata. No Qdrant, no Neo4j. Split a store out only when a query
actually gets slow — it won't at solo-researcher data volumes.

**D13. Supabase — auth + Postgres + storage.**
GoTrue auth (email/Google/GitHub), managed Postgres with pgvector, S3-compatible storage for
PDFs, row-level security. FastAPI verifies the JWT and keeps full SQL control. Saves ~2 weeks
of undifferentiated auth/storage plumbing.

> **RETIRED 2026-08-01 — Supabase is gone entirely.** All three things it provided are now
> local: **auth → dropped** (single local user, D31); **storage → the vault folder on disk**
> (D32); **Postgres → local, in a Docker container** (D35). Docker is already a hard dependency
> for the experiment sandbox (D33), so running Postgres in it costs nothing extra and beats
> bundling embedded binaries. **D5 (Postgres-only, pgvector + tsvector) is untouched** — only
> the host changed. **D27 (auth) is retired with this.**

**D14. Background jobs — Postgres-backed queue (SAQ w/ Postgres backend, or pgqueuer).**
No Redis. Transactional enqueue, one less service to run. Jobs: PDF fetch/parse, embedding,
structured extraction, feed polling.

> **Amended 2026-08-01 — the queue survives, the *cadence* changes.** A desktop app is only
> running when the user opens it, so **"daily cron" is not a thing that exists.** Scheduled work
> (feed polling, weekly interest-profile re-extraction) becomes **catch-up-on-launch**: on
> startup the sidecar checks `last_run_at` per scheduled job and runs anything overdue, once.
> The queue itself is unchanged (Postgres-backed, transactional). Add **experiment container
> runs** to the job list (D33) — long-running and cancellable.

**D17. Repo — flat monorepo with npm workspaces.**

```
backend/               FastAPI
frontend/              Vite + React (Tauri wraps this later)
packages/api-client/   TS client generated from FastAPI's OpenAPI schema
```

The generated client is the load-bearing part: a backend field rename becomes a **frontend
compile error**, not a runtime `undefined`. `apps/` prefix was considered and dropped as
pure ceremony.

> **Amended 2026-08-01 — one directory added.**
>
> ```
> backend/               FastAPI sidecar (harness, AI, Docker orchestration) — Python only
> frontend/              Vite + React renderer
> desktop/               Electron main + preload — launcher/supervisor, ~300 lines, no logic
> packages/api-client/   TS client generated from OpenAPI (unchanged)
> ```
>
> **`desktop/` must stay dumb.** It spawns the sidecar, owns the window, and proxies native
> dialogs. **No AI, no business logic, no data access** — that is D19, restated for the shell.

---

## LLM layer

**D9. BYO API key, encrypted at rest — with open-weight support.**
User supplies their own key (Anthropic, OpenAI, Google, Groq, DeepSeek, Kimi, Qwen, Ollama,
OpenRouter…). Your inference cost ≈ zero; scales to any number of users. Onboarding links to
Google AI Studio / Groq free tiers so a user without budget can still start.

> **Ruled out:** Claude Pro/Max and ChatGPT/Codex subscriptions **cannot** be used by a
> third-party hosted app. No API surface exists for it and the ToS does not permit it. The
> only workaround (shelling out to a locally installed `claude` CLI) requires a local-only
> desktop app and is incompatible with hosted multi-user.

**D10. LiteLLM as the provider abstraction.**
One `llm.complete()` call across 100+ providers. Handles retries, streaming, cost tracking,
per-user key routing. No native provider SDKs in application code.

**D11. Embeddings — self-hosted, ONE fixed model, forever.**
BGE-M3 or gte-modernbert, CPU-served. **Embeddings are deliberately not configurable.** Chat
models can be swapped freely; embedding models cannot — changing one silently invalidates every
vector in the index. This is the single most important non-configurable decision in the system.

> **Locked 2026-07-23 — `Alibaba-NLP/gte-modernbert-base`** (768-dim, English, 8192 ctx, dense
> retrieval). Chosen over BGE-M3: usage is **English-only + dense-only** (BM25 handled
> separately by `tsvector`, so BGE-M3's multilingual + hybrid-sparse edges are unused), and
> embedding is **async** (D14) so CPU speed is irrelevant to UX. 768-dim = lighter vectors. See
> D24. **This is permanent — changing it means re-embedding the entire corpus.**

**D12. Voice/NL → actions: intent router + tool-calling agent.**
A small set of typed tools (`search_papers`, `open_paper`, `compare`, `filter_by_dataset`,
`query_memory`, …). Fast path: cheap classifier/regex handles the ~15 most common commands with
zero LLM call. Fallback: tool-calling agent. This degrades gracefully — common voice commands
still work on weak open-weight models that tool-call badly.

> **Amended 2026-07-23 — pure agent, no hardwired fast path.** The hardwired 15-intent
> classifier is dropped: it is hand-maintained hardwiring that rots, and it contradicts the
> harness goal of making obvious things work *without* hardwiring. **Every turn goes through
> the single-agent loop** (D20, node 1); the typed tools stay. A latency optimization via a
> small *routing model* (never a regex table) is **future scope**. Graceful degradation on
> weak models is preserved via **prompted-structured-output fallback** when native tool-calling
> is absent (D20, node 6).

---

## Retrieval & content

**D6. Search — live federated + rerank + cache.**
LLM rewrites the query per source → parallel fan-out to arXiv / OpenAlex / Semantic Scholar /
Crossref / Papers with Code / GitHub → dedupe by DOI/arXiv-id → cross-encoder rerank top ~100 →
cache results in Postgres. No owned index (a 250M-work OpenAlex snapshot is a whole project
before feature one works). Revisit for the Feed in slice 4.

**D7. Structured extraction — two-stage, lazy.**
1. Results list shows **abstract summary + metadata only** (title, venue, year, citations, code
   link, source link).
2. On opening a paper: full structured split (Problem / Method / Datasets / Results /
   Limitations), derived **strictly from the paper's own content and section headings** — no
   outside knowledge, no inference.
3. If marked relevant → saved to the project's **relevant papers** library.

> **Correction to the original phrasing:** "local storage" won't survive multi-user + desktop
> sync. The relevant-papers library is **server-side, per project**, and syncs to the client.

**D8. Full text — open-access + user upload, graceful degradation.**
Fetch OA PDFs (arXiv, Unpaywall, S2 OA link); otherwise the user drags in their own copy.
Parse with GROBID (sections + references) and marker/docling (figures, equations). **Never
scrape paywalls.** If no full text is available: show abstract only, plus a link to the source
the abstract came from. No fabricated structured card.

**D15. Knowledge graph — metadata-first, LLM only on opened papers.**
Free and exact edges from APIs: cites/cited-by (OpenAlex/S2), authored-by, uses-dataset and
has-code (Papers with Code), topic tags. LLM-derived edges (method→method, idea→paper) are
extracted **only for papers the user actually opened**, reusing the D7 extraction pass. A
knowledge graph whose value is trustworthiness cannot afford hallucinated edges.

**D16. LaTeX — in-browser WASM compilation (SwiftLaTeX / texlive.js).**
Client-side. Zero server cost, zero sandbox/RCE surface, instant preview, works offline.
Trade-off: ~20–40MB WASM on first use, exotic packages may be missing. Server-side Tectonic in
a locked-down container is the fallback if package coverage proves insufficient.

> **Amended 2026-08-01 — the fallback just got cheap; take it when needed without ceremony.**
> Docker is now a hard dependency (D33) and the machine is the user's own, so **Tectonic in a
> container** costs one image instead of a hosted service, and the "zero server cost / zero RCE
> surface" argument for WASM largely dissolves. **Keep SwiftLaTeX WASM as the default** (instant
> preview, no container spin-up per keystroke) and **promote Tectonic-in-Docker from
> hypothetical fallback to a shipped escape hatch** for full package coverage on final compiles.

---

## Standing constraints (do / don't)

**Do**
- Cite a span for every extracted field; if the paper doesn't state it, print "not stated."
- Cache expensive derived artifacts (structured cards, rerank results) globally by paper ID —
  compute once, ever.
- Keep every capability reachable as a **typed tool**, so voice, text, and UI all share one path.
- Generate the TS API client from OpenAPI on every backend change.

**Don't**
- Don't make the embedding model configurable (D11).
- Don't scrape paywalled PDFs (D8).
- Don't let the AI write paper sections — it verifies and organizes; the researcher authors.
- Don't add a second datastore before a Postgres query actually measures slow (D5).
- Don't put `agents.create`-style one-time setup in a request path.
- Don't store user API keys unencrypted, or log them.

---

---

## 2026-07-23 additions

### Provenance enforcement — resolves Q18

**"Evidence over generated text" is enforced structurally, not by prompting.**

- **Extraction cards (D7) & literature-matrix cells → extractive-only.** Every field is a
  **verbatim span** `{value, quote, char_offsets, section_heading}` where the quote is an exact
  substring of the GROBID-parsed document. A **deterministic (non-LLM) substring validator**
  confirms the quote resolves to real text at the claimed offsets; if it fails, the field is
  dropped and rendered **"not stated"** — never as unverified prose. No paraphrase layer in v1.
  *(A two-layer extractive-value → grounded-paraphrase display is future scope.)*
- **Memory recall → source row IDs.** A recalled item cites the note/paper/conversation row it
  came from; verification is trivial (the row exists).
- **Reader answers (ask-about-highlight) → grounded-generative.** Inherently generative, so:
  free reasoning is allowed but **any factual claim about the paper carries an inline citation**
  to a span (the highlight or retrieved passages). **Quoted evidence is visually distinct from
  the model's reasoning.** Cross-paper claims cite spans in *both* papers from the memory layer;
  if the compared paper isn't in the read set, the model says so rather than reciting training
  knowledge. The **same substring validator** runs on every cited span; a span that doesn't
  resolve is stripped and its claim flagged unverified.

Through-line: **no field/claim is shown as coming from a paper unless its supporting quote
provably exists in the source.**

### Experiment logging — resolves Q19

**v1 = a structured research log (lab notebook); live run-tracking (W&B/MLflow) is future scope.**

Each experiment record:

- `hypothesis` (text)
- `setup` (model / dataset / config — text or light structure)
- `metrics` — a **structured list** `[{name, value, unit?}]` (user-authored)
- `notes` (free-form markdown for everything that doesn't structure cleanly)
- `status` enum — planned / remaining / in-progress / done
- **graph links** — inspired-by paper, uses-dataset, references-note

Outcomes are **user-authored** (AI never fabricates results — stays inside the provenance
discipline). Structured `metrics` make experiments **comparable rows** that can sit in a matrix
alongside papers' extracted results, and are forward-compatible with the future live-tracking
schema (ingestion writes into the same `{name, value}` shape — no migration).

> **Amended 2026-08-01 — experiments now actually run, and this makes provenance *stronger*.**
> Q19 assumed the app could never observe a run, so metrics had to be **user-authored** to stop
> the AI fabricating results. With D33 the app **executes the experiment in a container it
> controls**, so a third, better class appears:
> - `source: user` — typed in by hand (Q19's original case). Still supported.
> - `source: measured` — **captured from a real run** the app supervised: stdout/artifact parse,
>   linked to `run_id`, exit code, image digest, config hash, timestamp. **This is the strongest
>   provenance in the entire system** — a claim backed by a reproducible execution, not a quote.
> - `source: llm` — **still forbidden.** The AI may *propose* code and *read* results; it may
>   **never author a metric value.** Q18's through-line is unchanged.
>
> Every experiment record gains `runs[]` (`{run_id, started_at, exit_code, image, config_hash,
> stdout_ref, artifacts[]}`). The forward-compatibility claim to W&B/MLflow holds — this *is*
> that ingestion path, arriving early.

### D18 — (retired) hosted-core / desktop-harness partition

Explored 2026-07-23 during the desktop pivot, then **dropped** when the product stayed fully
web-based. Recorded only so a future session doesn't re-derive it: the idea was "core remembers
and indexes; harness reaches out and reasons." Superseded by **D19 + D20**.

### D19 — Fat backend, thin frontend

The **backend is the single core**: it thinks (the harness), stores (papers, notes,
experiments, index), and runs the agent loop. The **frontend is a window** — it captures user
input + UI state and renders events; **no business logic on the client.** Minimize data
transfer: the model gets tiny summaries, the client gets **scoped, referenceable payloads it
pulls lazily by id** (see D20 node 3). Keeps work in one place; makes "scale later" a widening.

### D20 — The harness (7 nodes)

The harness is an **agent runtime** (Claude-Code / "Hermes"-style loop) living **inside the
FastAPI backend**, adapted for a **web** interface rather than a terminal. Two deep consequences
of "web, not terminal" drive the design: **(a) tool results are dual-channel** — a compact
`model_view` for the LLM and a rich `ui_view` for the frontend; **(b) UI state is part of
context, bidirectionally** — what's open/highlighted flows *into* the loop, and some tools flow
*out* as UI commands. That coupling is what makes obvious things work without hardwiring.

| # | Node | Decision |
|---|---|---|
| 1 | **Control loop** | Single-agent tool-calling loop. Subagents exist only **as tools** (e.g. `deep_research`), never as top-level orchestration. **Hard iteration cap** (~8–10) with a graceful stop. |
| 2 | **Context assembly** | **Hybrid.** *Ambient (always-on, deterministic):* system prompt + provenance rules, tool schemas, **live UI/workspace state**, compact working set (active items as ids/titles). *Deep memory (demand-driven):* `query_memory` tool returning **cited rows**. History **compacted** past a budget (full history stays in DB); eviction order: working set → history → per-turn retrieval; system/tools/UI-state never evicted. |
| 3 | **Tool layer** | Reference-based `ToolResult` = `{model_view` (tiny summary)`, ui_view` (renderable, **by id**, never in LLM context)`, refs` (stable ids)`, ui_actions` (UI commands)`}`. Large results → **server-side result store**, keyed by id; model manipulates handles, not payloads. Taxonomy: **Query / Action / MCP-bridged**, one contract. **Native-first**; MCP is the extensibility lane, not the core mechanism. |
| 4 | **Memory** | One **project-scoped BGE-M3 pgvector + tsvector index** over *all* artifacts (papers chunked, notes, experiments, conversations), tagged `{type, source_id}`; hybrid retrieval → rerank → cited rows. **Write path = C (hybrid):** explicit artifacts are **user-authored ground truth**; conversations persist **verbatim + a summary-as-index** (recall links back to verbatim turns); **no AI-invented standalone facts** in v1. **User-visible and editable.** Compaction is a *window* op, not forgetting (salient turns already in memory). |
| 5 | **Web I/O** | **WebSocket** (bidirectional, single channel). Typed event stream — *down:* `status / text_delta / tool_call / tool_result(ref) / ui_action / turn_complete / error`; *up:* `user_message / ui_state / interrupt`. **UI-state snapshot attached to each `user_message` + incremental `ui_state` pushes** mid-turn. **First-class interrupt** (cancels the turn; partial results retained). Voice stays on a separate **WebRTC** path (D2). |
| 6 | **Model & turns** | **Pure agent** (D12 amended — no hardwired fast path). **Primary + optional auxiliary model tier:** user sets a primary chat model (BYO key via LiteLLM D10); auxiliary tasks (extraction, summarization, interest classification) default to an optional cheaper model, else fall back to primary. **Prompted-structured-output fallback** for models without native tool-calling (graceful degradation). Embeddings/rerank are non-LLM. |
| 7 | **Runtime shape** | **In-process async** cancellable `asyncio` task per turn, bound to the WebSocket session (this is what makes interrupt real). I/O-bound steps `await`ed inline; **CPU-bound steps (embed, parse, rerank) offloaded to the D14 Postgres job queue** — never block the event loop. Turn state in-process but **persisted incrementally**. Harness is a **self-contained, extractable package** (`backend/harness/`) so a future move to a dedicated worker is extraction, not rewrite. |

### Research Feed — resolves Q20 / Q4

**Pipeline: interest profile → category fetch → keyword rank → dedup → daily poll.**

- **Interest profile** — inspectable, **user-editable** `{categories, keywords}`. Keywords are
  **synonym-expanded** at extraction time (counters keyword brittleness, e.g. RAG ↔
  retrieval-augmented generation). Extracted from the project corpus by an **infrequent LLM
  classification pass** (weekly / on meaningful corpus growth; cached). Categories anchor to
  each source's native taxonomy (arXiv tree is the anchor).
- **Fetch — category-driven (broad recall), NOT keyword-driven.** Chosen over keyword-fetch
  even though single-user removes the quota argument: broad category recall never drops a
  relevantly-but-differently-phrased paper, and the shared-poll scaling property returns for
  free if multi-user ever happens. Per source, windowed to "since last poll," recency-sorted.
- **Rank** — synonym-expanded keyword match + **BGE-M3 centroid cosine** + **cross-encoder
  rerank** top-N. **No LLM in the scoring path.** `why-relevant` = matched keywords/categories +
  similarity (deterministic; optional one-line LLM gloss, low-stakes).
- **Dedup** — normalized DOI/arXiv-id against the **seen set** = read ∪ library ∪
  previously-surfaced ∪ dismissed.
- **Cadence** — **daily job on the D14 Postgres queue**, per project. *(This is also the
  feed→harness wiring: the feed is a scheduled harness job, not a live request path.)*

**Profile lifecycle:**
- **Bootstrap** — a **user-declared seed at project creation** (a sentence of focus + optional
  seed papers). The seed *is* the initial editable profile; seed papers seed the corpus
  centroid. Doubles as onboarding; no cold-start magic.
- **Refine** — **save** → item enters the library → shifts centroid + feeds next re-extraction;
  **dismiss** → seen set (never resurfaces) + light down-weight of very-similar items in the
  same poll; **explicit edits** always available; **weekly re-extraction** reconciles profile
  with the evolved corpus. Full negative-example learning is **future scope**.

### Reader UI — resolves Q7

- **Render the real PDF via PDF.js** (figures/equations/layout intact); the docling/GROBID
  structure is a **navigation + provenance overlay** (jump-to-section, references, datasets),
  not a reflowed text reader.
- **Quote-based (content-addressed) highlight anchoring** — `{quote, prefix, suffix}` (W3C
  `TextQuoteSelector` style) as the durable anchor + cached page+bbox as a rendering hint.
  **Shared with provenance:** the extractive card's `char_offsets` and a reader highlight are
  the same anchor object. Survives re-parsing (re-locate by searching the quote); **bridges the
  two text streams** (GROBID/docling text ↔ PDF.js text layer) because the quote is the lingua
  franca — string-search it in either. Requires a **normalizing fuzzy locator** (whitespace /
  hyphenation / ligatures) — the same locator provenance needs, built once.

### Deployment & the $0 free-tier plan — resolves Q3

> **RETIRED 2026-08-01 — there is no deployment.** The app runs on the user's own machine
> (D31); Cloudflare Pages, the Hugging Face Space, the Cloudflare Tunnel dev loop, and the
> Supabase free tier are all **deleted**. Still **$0**, now trivially so. Two pieces of this
> section **survive and are load-bearing** — carried into D31:
> - **Heavy ML collapsed into in-process Python libraries** (docling instead of a GROBID JVM;
>   `sentence-transformers` instead of an ML box). This is what makes a single sidecar process
>   viable at all.
> - **Everything ships as one Docker-composed unit**, which is now the install story.
>
> **Cost of the retirement, stated plainly:** there is no longer a clickable demo URL for the
> portfolio. Accepted deliberately — demo via screen recording. Kept below for reference only.

Portfolio-first, **$0**, extendable to real use later.

- **Frontend** → Cloudflare Pages / Vercel (free, always on).
- **DB + auth + storage** → **Supabase free tier** (pgvector included). Paper *content* lives
  here (see storage below); tiny footprint.
- **LLM** → BYO key on Google AI Studio / Groq free tiers (D9). $0.
- **Collapse the heavy ML services into in-process libraries (amends D8, D14):**
  - **GROBID → docling (in-process Python)** for v1. Deletes an always-on JVM service; loses
    some citation-graph precision. GROBID returns as a service if real use demands it.
  - **BGE-M3 + cross-encoder → `sentence-transformers` in-process** (CPU). No separate ML box.
  - Net: **no separate always-on ML services in v1** — parsing + embedding are libraries in the
    Python backend. Invariant #1 intact (still one fixed model; "self-hosted" = pinned, not metal).
- **Backend** → **Hugging Face Space (Docker, free CPU)** as the hosted demo URL + **local
  machine + Cloudflare Tunnel** as the dev loop. Same Docker image both places (D19 payoff).
- **Scale-later seam:** peel a service off onto its own host / VPS when it actually needs it —
  a container move, not a rearchitect.

### PDF storage & user-owned blobs — resolves Q5

> **INVERTED 2026-08-01 — files are the truth, Postgres is a rebuildable index.** See **D32**.
> The old rule ("durable truth is Postgres, blobs are a cache") was correct for a hosted app and
> is **wrong for an Obsidian-inspired local one** — the entire appeal of that model is that your
> data is plain files in a folder you own, readable without the app. New rule:
> - **Files on disk are truth** for everything human-authored or source: notes (`.md`), PDFs,
>   experiment code and outputs, manuscripts.
> - **Postgres is truth only for machine-derived data**: embeddings, `tsv`, parsed sections,
>   extractive cards, graph edges, caches. **Delete the whole DB and it rebuilds from the vault.**
> - Cost of the inversion, stated honestly: a rebuild means **re-parsing and re-embedding** the
>   corpus. That costs time (minutes to hours), **not data**. Acceptable, and worth it.
> - **Blob classes collapse to one:** the vault holds the PDF. No eviction tiering, no
>   content-hash-only class. Re-fetch by canonical id stays as a *repair* path for a missing file.
> - **BYO Google Drive / OneDrive OAuth is dropped** — it existed to avoid hosted storage cost,
>   which no longer exists. The local folder *is* user-owned storage, which was the actual goal.
>   Multi-machine sync is an **open question (Q41)**, not a v1 feature.
> - **Markdown export is no longer a feature** — the notes were always markdown on disk.
>
> Kept below for reference; the three-blob-class scheme and the quota section no longer apply.

**The durable truth is Postgres; PDF blobs are a cache or user-owned.**

- On first processing, persist to Postgres **once, forever**: full parsed text (sections),
  extractive cards + quote anchors, embeddings, notes, graph edges. **Never re-extracted.**
  A paper's link/id is **provenance + a re-fetch handle**, *not* the sole holder of context.
- **The PDF blob is needed only to visually render the real PDF + draw highlights** (Q7). If a
  blob is evicted or a link rots, the user keeps **full textual access** (text + cards + notes +
  citations) and only loses the *visual* PDF until re-fetched — D8 graceful degradation.
- **Three blob classes:**
  1. **OA papers** → re-fetch by id, evictable cache. No durable store.
  2. **User-provided links** → **normalize to canonical id when possible** (arXiv/DOI link →
     extract id → treat exactly like OA); non-normalizable URLs → best-effort fetch, graceful
     degrade on rot/auth-gate.
  3. **True uploads (no link)** → the only class that must persist. **Content-hash (SHA-256)
     dedup.**
- **`StorageBackend` abstraction, with BYO storage as a v1 feature.** v1 ships **both**:
  **Supabase storage** (default/fallback) **and BYO Google Drive / OneDrive via OAuth** — user
  connects a drive, blobs live in *their* drive (app folder), we hold only a token + file id.
  User owns the files; our blob cost ≈ 0. (Local-folder / File-System-Access ruled out — not
  viable for a hosted app.)
- **Quota** — soft at n=1 (warn near the free 1 GB). **Markdown export** of notes/cards to the
  user's drive (data portability) = **future scope**.

---

## Build specification (Items 1–4) — 2026-07-23

Pre-build spec pass. Architecture (D1–D20) said *how the system is shaped*; this says *what to
build first*. Resolved before writing any code, at the user's request.

### D21 — Data model (the global/project boundary)

The load-bearing schema decision: **what is global (shared, computed once) vs project-owned.**

- **Global — keyed by canonical paper id, computed once, shared across all projects:**
  `papers` (canonical id + JSONB metadata + all source ids + abstract), `paper_content` (parsed
  sections/full text), `paper_cards` (extractive cards + quote anchors), `paper_chunks`
  (embeddings + `tsv`), `paper_edges` (metadata + LLM-derived *paper-intrinsic* edges).
- **Project-scoped — the user's workspace:** `projects` (name, **interest profile JSONB**,
  seed), `project_papers` (membership + relevance mark), `notes`, `experiments` (Q19),
  `conversations`+`messages` (verbatim), `project_chunks` (embeddings of notes/experiments/
  conversation-summaries), `highlights` (quote anchors), `feed_items`/`seen_set`, `idea_edges`.
- **Account-level:** `users` (Supabase auth), `api_keys` (encrypted BYO), `storage_connections`
  (Drive/OneDrive tokens).
- **Reconciliation ("compute once" ↔ "project-scoped memory"):** the project memory index is a
  **query-time union**, not a table — memory(P) = `paper_chunks`(papers in P) ∪
  `project_chunks`(P). Paper embeddings computed **once globally, reused**; retrieval stays
  **project-isolated** via membership filter.
- **Canonical paper identity:** normalize by priority **DOI → arXiv id → OpenAlex/S2 id**; all
  source ids retained; `papers` keyed on canonical id (this is the dedup + graph-edge key).
- **Memory tables:** **two** — `paper_chunks` (global, no `project_id`) + `project_chunks` (has
  `project_id`), both `{embedding vector(768), tsv, source_type, source_id, char_span}`.

> **Amended 2026-08-01 — reaffirmed against a "papers per project" request; the boundary holds.**
> The user asked for **papers and notes to be per-project**, with a clear view of which papers
> mattered to which project. **Notes: granted in full** — notes are project-owned, live in that
> project's folder (D32), and never leak across projects. **Papers: the request is granted at the
> level that matters (membership) but refused at the level of content.** Duplicating a PDF into
> three projects would mean three parses, three sets of extractive cards, and three sets of
> embeddings for one paper — destroying the D21 canonical id (DOI→arXiv→S2), the "compute once,
> ever" constraint, and cross-project dedup. So:
> - **Global, stored once:** the PDF blob, parsed text, extractive cards, `paper_chunks`
>   embeddings, paper-intrinsic edges.
> - **Per project:** membership, **why it's relevant** (user-authored), relevance level, notes,
>   highlights, matrix placement. `project_papers` carries this and answers "what was relevant to
>   this project" directly.
> - **On disk** this reads as per-project anyway — each project folder holds a `papers/` view
>   (symlinks into the global library + a human-readable `papers.md` index of what matters and
>   why). See **D32**. Project-isolated retrieval is unchanged: the query-time union already
>   filters by membership.
> - **Account-level tables** (`users`, `storage_connections`) are **dropped** with D13/D27;
>   `api_keys` becomes a local single-row settings store (D36).
- **Chunking:** **section-aware** (split on docling section boundaries, sub-split long sections
  to a token budget with small overlap) — aligns chunks with the quote-anchor/provenance model.

### D22 — Tool catalog (v1)

**Q**=Query, **A**=Action. Slice-1 set: **Discovery / Reading / Memory / Mutations / Nav.**

- **Discovery:** `search_papers(query, filters?)` Q → `result_id` + summaries; `refine_results
  (result_id, filters)` Q (subsumes `filter_by_dataset`); `add_paper(link|id|upload_ref)` A.
- **Reading:** `get_paper(paper_id, include=[card|sections|references|datasets|code])` Q (one
  parameterized tool, not five); `compare(paper_ids[])` Q; `open_reference(paper_id, ref_id)` A.
- **Memory:** `query_memory(query, types?)` Q → cited rows.
- **Mutations:** `save_note`/`update_note` A, `mark_relevant(paper_id, level)` A,
  `create_highlight(paper_id, anchor)` A, `log_experiment`/`update_experiment` A.
- **Navigation (emit `ui_actions`):** `open_paper`, `scroll_to`, `highlight_span`,
  `open_view(matrix|graph|feed|experiments)`.
- **Later slices:** `build_matrix`/`update_cell` (S2); `get_feed`/`save_feed_item`/
  `dismiss_feed_item`/`get_interest_profile`/`update_interest_profile` (S4); `insert_citation`/
  `check_citations`/`find_missing_citations` (S3); `get_graph`/`find_related` (graph viz).

Design rules: **(Fork A)** reader Q&A is **not a tool** — it's the core agent loop answering
from ambient UI-state + retrieval tools (no redundant `ask_paper` hop). **(Fork B)**
**moderate-fat** tools (parameterized `get_paper`/`refine_results`) over many-thin. **MCP:**
build the adapter (extension seam), **bundle zero MCP servers in v1** — native covers v1.

### D23 — v1 scope & voice (amends D2 for v1)

- **Text-first.** Slice 1 (D3) is built and hardened over **text**; the harness (D20) is proven
  where every event is debuggable.
- **Voice is a cross-cutting layer added right after Slice 1** (it only needs the tool layer).
  D2's design makes voice a **thin transport** over the same WebSocket+tools — zero harness
  change whenever it lands.
- **v1 voice mechanism = browser Web Speech API** (client-side STT+TTS, **$0**, no media
  server) — **amends D2 for v1.** Server-side WebRTC STT→TTS and realtime-s2s are the
  quality/scale **upgrade path** (swap transport, harness untouched).
- **Build order:** Slice 1 (text) → voice layer (Web Speech API) → Slices 2–4 (voice rides free).

### D24 — Model picks

- **Embedding (PERMANENT, invariant #1): `Alibaba-NLP/gte-modernbert-base`** — see D11. English,
  dense-only, 768-dim, async-embedded. Changing it = full re-embed.
- **Reranker (swappable, in request path): `cross-encoder/ms-marco-MiniLM-L-6-v2`** — light/fast
  on CPU; upgrade to `bge-reranker-v2-m3` with GPU/real-use. No reindex to swap.
- **Dev / default BYO LLM (swappable): Gemini 2.5 Flash** (Google AI Studio free tier — strong
  tool-calling, $0); Groq Llama 3.3 70B as fallback dev target; auxiliary tier (D20 node 6) →
  Gemini Flash-Lite. Just the build target; users BYO anything (D9/D10).

### D25 — Search federation (Item 5)

**Three tiers, not one flat fan-out.**
- **Primary fan-out (every search):** **arXiv** (OA full text + discovery), **OpenAlex**
  (metadata / citations / concepts; also feeds feed-categories + graph edges), **Semantic
  Scholar** (citations / influential-citations / TLDR / OA links).
- **Enrichment (on paper-*open*, not every search):** **Papers with Code** (code / datasets /
  benchmarks → card fields + PwC canonical ids), **GitHub** (repo details, "open implementation").
- **On-demand resolver:** **Crossref** — only to resolve a DOI OpenAlex/S2 missed.
- **Query handling:** **one LLM query-understanding pass** → `{keywords, filters:
  year/venue/has_code/author}` → deterministic per-source param mapping. Not N per-source LLM
  rewrites.
- **Dedup:** D21 canonical id. **Rerank+cache:** merge → MiniLM rerank (D24) top ~100 → cache
  as `result_id` (D22 result store).

### D26 — BYO-key flow (Item 6)

- **Encryption: app-level AES-256-GCM.** Master key in **backend env (HF Space secret)** — never
  in DB or repo. Decrypt **in-memory at call time** only; never log; UI shows `…last4` only.
  (Chosen over Supabase Vault for portability.)
- **Providers:** ~6 first-class — **Google, Groq, OpenAI, Anthropic, OpenRouter, DeepSeek** —
  plus **Custom / OpenAI-compatible (base URL)** (covers Ollama, local). All via LiteLLM (D10);
  onboarding leads with free tiers (Groq / Google AI Studio).
- **Config:** per-provider keys stored; user selects **primary + optional auxiliary model**
  (D20 node 6); **validate on save** (test call, surface available models).
- **Entry:** a "Models" settings page + the onboarding wizard (D29).

> **Amended 2026-08-01 — local models promoted to first class; key storage moves to the OS.**
> See **D34**. Provider list becomes **~6 remote (unchanged) + Ollama + vLLM as named,
> first-class local options** rather than something buried under "Custom / OpenAI-compatible."
> Local providers take a **base URL and no key**, and the UI must not demand one. Key storage:
> the AES-256-GCM master key lived in a Hugging Face Space secret, which no longer exists —
> it now lives in the **OS keyring** (`libsecret` via the `keyring` package), never in the vault
> and never in the DB. **Invariant #1 is unaffected and must be actively defended here:**
> embeddings run on the fixed local `gte-modernbert-base` and are **never** routed through
> Ollama or vLLM, however tempting the "you already have a local model server" symmetry looks.

### D27 — Auth + demo mode (Item 7)

> **RETIRED 2026-08-01 — no auth in v1.** Supabase GoTrue, Google OAuth, email/password, the
> anonymous demo door, and JWT verification are all **deleted**. The app runs locally for one
> user; the OS login *is* the auth boundary. **`owner_id` columns are dropped** — carrying a
> dead column "so scaling is a widening" is exactly the speculative ceremony this project should
> not pay for, and re-adding it later is one migration. **RLS was already deferred; now moot.**
> Google OAuth also died with the Drive storage connection (Q5 amendment).

- **Full Supabase GoTrue.** Real accounts: **Google OAuth (primary — doubles as the Drive
  connection, Q37) + email/password**; GitHub deferred.
- **Anonymous sign-in = demo door.** Kept as a **secondary** "explore without an account" link
  (limited: still needs a BYO key to use the agent, Supabase-storage only). **Upgradeable
  in-place** — linking credentials to the anonymous user converts it, no data migration.
- **`owner_id` on every project-scoped table** (global tables stay owner-less). Ownership
  **enforced in the FastAPI query layer** (D13 full SQL control); **JWT verified per request**.
  **RLS policies deferred** — additive later, no schema change.
- **No sign-in/key wall for browsing;** required steps live in the gated wizard (D29).

### D28 — Knowledge graph & literature matrix (Item 9)

**Graph:**
- **Scope = project-scoped union** — edges among the project's papers + `idea_edges` + relevant
  global `paper_edges`. Not a global blob.
- **Node identity, split by trust:** canonical ids for API entities (OpenAlex author id, PwC
  dataset id, repo url, D21 paper id); **LLM-derived method/concept nodes = light normalization**
  (lowercase / alias / embedding-merge), **dup-tolerant** — under-merge beats false-merge for a
  trust-graph.
- **Built incrementally in the D7 extraction pass** (D15). No separate build step.

**Literature matrix:**
- **Standard cells = a projection of existing extractive cards** (Problem / Method / Datasets /
  Results / Limitations) — **no re-extraction**, provenance-safe by construction — + a
  **Personal-notes** (user) column; Strengths = user-authored or extractive.
- **Custom columns = a per-paper scoped extractive query**, cached per `(paper, column)`,
  **"not stated"** fallback (Q18 holds).
- **Editable cells → user-authored with a `source: extracted|user` flag** — editing *labels* an
  override, never corrupts provenance.
- **Persisted** as a project artifact: `{selected_paper_ids, column_defs, cell_overrides,
  custom_column_cache}`.

### D29 — Onboarding (Item 10)

> **Amended 2026-08-01 — four steps become three, and one is new.** Step 1 (**Account**) is
> **deleted** with D27. Step 4 (**Storage**/Drive) is **replaced** by *"pick your vault folder"*
> (default `~/ResearchOS`, D32) — and it is **no longer skippable**, since nothing works without
> a vault. New **step 0: environment check** — verify Docker is present and the daemon is
> reachable (D33), and offer the exact `dnf`/`systemctl` commands if not. Resulting wizard:
> **environment → vault folder → models (incl. local, D34) → first project + focus seed.**
> The gated-not-progressive stance is unchanged.

**Gated setup wizard** (all required → a working project). Progressive/lazy prompting was
considered and **rejected** in favor of complete upfront setup.
1. **Account** — Google (recommended; also enables Drive) or email/password.
2. **Models** — add + validate ≥1 BYO key, select primary + optional auxiliary model (D26).
   Free-tier links shown so the step is never a dead end.
3. **First project** — name + **optional/skippable focus seed** (sentence + optional seed
   papers, Q32).
4. **Storage** — connect Drive, or default to Supabase storage — **skippable**.

- **Demo door kept** as a secondary "explore without an account" link (anonymous, D27).
- **No pre-loaded sample/demo project** in v1 (skipped).

### D30 — Frontend / workspace shape (Item 8)

- **Layout = top bar + persistent 3-pane shell.** Top bar (project switcher / breadcrumb /
  active-paper title / account+settings); **left** nav (papers/notes/experiments tree);
  **center** active-view (reader / matrix / graph / experiments / feed / writing); **right**
  **persistent Companion chat** (always visible & addressable).
- **The persistent chat = the Companion = the USP.** One WebSocket session **per project**,
  survives center-pane navigation. Voice (D2→Web Speech, D23) drops in as a new transport into
  the same session — **no logic rewrite**. Clicking in the UI, typing, and (later) speaking all
  resolve to the **same tool call + route transition** (one path, D19/D20).
- **State:** **React Query** (all REST server data — cache/invalidate/loading) + a **WebSocket
  event bus** (harness stream → chat transcript, invalidates RQ cache on `tool_result`,
  dispatches `ui_action` to the router) + **Zustand** for local UI state (open pane, selection)
  — which *is* the `ui_state` payload sent up (D20 node 5).
- **Routing (React Router):** URL owns **project + center-pane content** (`/p/:projectId/
  paper/:paperId`, `/matrix/:id`, `/graph`, `/experiments`, `/feed`, `/write/:docId`). Chat +
  nav are persistent shell, not routes. `ui_action` events drive the same router.
- **Reader:** PDF.js real-PDF render + text/annotation overlay; **selection → inline popover**
  ("Ask about this" / Highlight / Explain); collapsible **structure sidebar** (docling sections
  / refs / datasets / code); **toggleable extractive card** whose fields click-through to
  `scroll_to`+`highlight_span` the source span; **cross-pane quote-anchor sync** (chat citation
  ↔ card field ↔ PDF span all speak the Q33 anchor).
- **Writing (LaTeX, Slice 3):** **CodeMirror 6** source (LaTeX highlight) + **live inline KaTeX
  math** (the Obsidian touch) + **debounced SwiftLaTeX WASM PDF preview** (~1–2s, Overleaf-
  style — full WYSIWYG for a whole LaTeX doc is impossible) + compile-error panel. GUI help:
  toolbar snippets, command + `\cite`/`\ref` autocomplete (cite pulls project refs), env
  snippets, AI syntax-assist via chat (never writes prose). Insert: image upload→VFS→
  `\includegraphics`, **Mermaid** flowcharts, workspace **dataviz PNG/SVG** exports.
- **Style:** **Academic & warm, light-only.** Warm off-white/sepia neutrals, serif for reading
  (Charter/Lora-class), comfortable measure, soft contrast, minimal chrome. Inspiration:
  Readwise Reader / iA Writer. Single theme (less to build).

---

## 2026-08-01 — the desktop pivot (D31–D36)

**Where this section conflicts with anything above, this section wins.**

The trigger: **experimentation is not optional in research.** Reading papers and taking notes is
half the loop; running the thing is the other half. A hosted web app cannot touch the user's
filesystem or GPU, and the "Agentic OS" direction only means something if the agent can act on
the actual machine. That requirement — not aesthetics, not "desktop feels more OS-like" — is
what reopened D1 for the third and final time.

**Student-scope framing (binding):** solo developer, **Linux only**, **one user**. Code signing,
notarization, Windows/macOS builds, auto-update servers, multi-tenancy, and ops burden are
**out of scope by decision**. Effort goes to the core app.

### D31 — App shape: Electron shell + Python sidecar

**"Does a desktop app even have a backend?" — yes, and here it is forced.** All AI stays Python
(docling, `sentence-transformers`, LiteLLM, torch). Electron is Node + Chromium and **cannot
execute Python**. A separate Python process is therefore a **language boundary, not a design
preference**. This is the same shape as VS Code (Electron + extension host + language servers),
Obsidian (Electron + main-process file I/O), and Jupyter Lab (web UI + Python server).

| Layer | Tech | Responsibility |
|---|---|---|
| **Shell** | Electron main + preload (`desktop/`) | Spawn/supervise the sidecar, own the window, native file dialogs, tray, lifecycle. **Zero logic.** |
| **Renderer** | React + Vite (`frontend/`) | The window. Unchanged from D30 / `UI_DESIGN.md`. |
| **Sidecar** | FastAPI + the harness (`backend/`) | Everything real: agent loop (D20), retrieval, parsing, embedding, Docker orchestration. |
| **Index** | Postgres + pgvector in Docker | Machine-derived data only (D35). |
| **Truth** | The vault folder | Files on disk (D32). |

**D19 is not weakened — it is sharpened: fat sidecar, thin shell.** The renderer and the Electron
main process both stay dumb. If logic starts leaking into `desktop/`, that is the failure mode to
watch for.

**Transport:** the sidecar binds **`127.0.0.1` on an ephemeral port**, and Electron passes the
port plus a **per-launch bearer token** to the renderer. The token is mandatory: any local
process — including any web page in any browser — can otherwise reach a localhost port. **D20
node 5's WebSocket protocol is unchanged**; it just terminates on loopback instead of the
internet. Existing REST + generated api-client (D17) unchanged.

**Cold start ("open the app and it runs"):** Electron shows the window immediately with a
readiness strip; the sidecar reports **per-capability readiness** as it warms. **ML models load
lazily** — importing torch and the embedding model is 5–15 s and must never block first paint.
Search, notes, and the vault tree are usable before embeddings are. Escape hatch if that still
irritates in daily use: run the sidecar as a **systemd user service** so it is always warm and
Electron just attaches.

**Updates:** `git pull` + rebuild. No updater, no release pipeline. Student scope.

### D32 — The vault: files are truth

```
~/ResearchOS/
  library/
    papers/<canonical-id>/       paper.pdf, parsed.json      ← GLOBAL, stored once (D21)
  projects/<project-slug>/
    notes/                       *.md            ← project-owned, never shared
    papers/                      symlinks → library/papers/<id>  + papers.md index
    experiments/<exp-slug>/      code/, outputs/, run log
    manuscript/                  *.tex, figures/
    project.md                   focus seed, interest profile (human-readable)
  .research-os/                  Postgres data, model cache, blob cache  ← REBUILDABLE
```

- **Everything outside `.research-os/` is truth.** Everything inside is derived and may be
  deleted at any time; the app rebuilds it from the vault.
- **`papers.md` per project** is the direct answer to "which papers were relevant to this
  project, and why" — a human-readable list with the user's relevance note, readable in Obsidian
  with the app closed. The DB row (`project_papers`) is the queryable mirror, not the source.
- **Symlinks, not copies** — one PDF, one parse, one set of embeddings (D21 amendment).
- **The vault is Obsidian-compatible on purpose.** Notes are plain markdown with wikilinks; the
  user can open the same folder in Obsidian. That is the whole point of the file layout.
- **Consequence: the app is not the only writer.** It must watch the vault and re-index on
  external change (see **Q44**).

### D33 — Experiment execution: Docker sandbox, always

**Every piece of code this system runs, runs in a container. No exceptions, no opt-out.**

The threat is not the user's own scripts — it is that **the agent writes and runs code, and the
agent reads PDFs from the open internet**. A prompt-injected paper that talks the agent into
running something is a realistic path to arbitrary code execution on the user's machine. So the
sandbox is **default-on for all execution**, agent-initiated or user-initiated.

- **Docker containers** (chosen over bubblewrap/firejail): strongest isolation for the effort,
  and **reproducible environments are something researchers want anyway** — the sandbox and the
  reproducibility feature are the same mechanism. **Docker becomes a hard dependency**, checked
  at onboarding (D29).
- **Per-run container**, from a **project-pinned image**. Ship a base image with the usual stack
  (numpy / pandas / torch / scikit-learn / matplotlib) so a run starts in ~1 s rather than
  resolving pip every time; per-project extra dependencies layer on top.
- **Mounts:** the project's `experiments/<exp>/` read-write, `library/` read-only if the run
  needs paper data. **Nothing else.** Never the whole vault, never `$HOME`.
- **Network: off by default.** Explicit per-experiment opt-in (dependency installs, dataset
  downloads) — recorded in the run record, because a networked run is a less reproducible run.
- **Limits:** CPU, memory, wall-clock timeout. **GPU via `--gpus`** + nvidia-container-toolkit,
  opt-in per experiment.
- **Runs go on the D14 queue** — long-lived, cancellable, streaming logs to the UI over the
  existing WebSocket.
- **Captured per run:** `run_id`, image digest, config hash, exit code, stdout/stderr, declared
  output artifacts. This feeds `source: measured` metrics (Q19 amendment) — the strongest
  provenance in the system.
- **The agent proposes; it does not silently execute** — see **Q40**.

### D34 — Local LLMs: Ollama and vLLM, first class

Researchers with a decent GPU should be able to run this with **zero API spend**.

- **Near-zero plan change:** LiteLLM (D10) already speaks `ollama/*` natively and reaches vLLM
  through its OpenAI-compatible endpoint. This is configuration, not architecture.
- **Named, first-class entries** in the model settings (D26) — **not** hidden under "Custom /
  OpenAI-compatible." Each takes a **base URL and no API key**; the UI must not require one.
- **Model discovery:** query the endpoint for available models rather than making the user type
  a model string.
- **Tool-calling varies wildly across local models.** D20 node 6's **prompted-structured-output
  fallback** is what makes this usable, and it becomes load-bearing rather than a nicety.
- **Invariant #1 holds, and is under active threat here:** once a local model server is running,
  routing embeddings through it looks natural. **Do not.** Embeddings stay on the pinned local
  `gte-modernbert-base` forever (D11/D24).
- **GPU contention is real** — vLLM holding VRAM starves experiments (see **Q45**).

### D35 — Postgres: local, in Docker

`docker compose` brings up **Postgres + pgvector**, data under `.research-os/`, started and
health-checked by the sidecar on launch. Chosen over embedded binaries (`pgserver`) and over
SQLite + `sqlite-vec`: **Docker is already a hard dependency for D33**, so this adds one
container and zero new concepts, and it keeps **D5, D14, D21, and the whole retrieval design
byte-for-byte unchanged**. SQLite would have been simpler to ship but would have forced a
rewrite of the pgvector/tsvector hybrid retrieval and the Postgres job queue — a large cost for
a dependency already paid.

### D36 — What the pivot does *not* change

Recorded so no future session re-opens settled ground: **D5** (Postgres-only), **D6/D25**
(federated search), **D7/Q18** (extractive-only provenance), **D8** (OA + upload, never scrape
paywalls), **D11/D24** (fixed embedding model — **invariant #1**), **D15/D28** (graph + matrix),
**D19** (fat backend), **D20** (the harness, all 7 nodes), **D21** (global/project boundary, as
amended), **D22** (tool catalog), **D30** (3-pane shell + persistent Companion), and
`UI_DESIGN.md` (look and feel). **Invariant #2 also stands** — a local process now exists, but
Claude/ChatGPT *subscriptions* remain ToS-forbidden as app LLM access; BYO key or local model.

---

## Open questions — status

> **2026-08-01 — reopened by the desktop pivot.** The claim below ("nothing blocks building")
> was true for the *web* product. **D31–D36 settled the shape of the desktop product, but opened
> nine new questions (Q40–Q48)**, listed first. Two of them (**Q40** agent-execution consent and
> **Q42** the execution model) are genuine blockers for the experiment feature; the rest have
> safe defaults and can be decided at implementation time.

### Open — introduced by the desktop pivot (2026-08-01)

| # | Question | Recommendation |
|---|---|---|
| **Q40** ⛔ | **Does the agent run code without asking?** The sandbox contains the blast radius; it does not answer consent. | **Propose → user approves → run.** Show the diff and the container spec before the first run of an experiment; allow "approve this experiment's re-runs" afterwards. Never auto-run code the agent wrote from something it read. **Blocker — decide before building D33.** |
| **Q41** | **Multi-machine sync** (laptop ↔ desktop). Dropped Drive OAuth left this unanswered. | **Nothing in v1.** The vault is a plain folder — the user can point Syncthing or a private git repo at it. Do not build sync; do not let it shape the schema. |
| **Q42** ⛔ | **Execution model: Jupyter kernel (interactive, stateful) or script runs (batch, reproducible)?** They imply very different UIs and very different provenance stories. | **Script runs first** — reproducible by construction, matches `source: measured` (Q19 amendment), far simpler. A persistent kernel is the natural v2. **Blocker — this decides the experiment UI.** |
| **Q43** | **Base image contents + per-project dependency management** (`uv` / `requirements.txt` / conda?). | `uv` + a per-project `requirements.txt` committed in the experiment folder, layered on the base image. Decide at implementation. |
| **Q44** | **Vault file-watching.** Files are truth (D32) and the user may edit notes in Obsidian with the app closed — how are external edits detected and re-indexed, and what happens on a conflicting concurrent edit? | `watchdog` + debounce → re-embed changed files; **last-writer-wins on disk, DB always yields to the file.** Needs a decision on whether the app ever writes a note the user has open elsewhere. |
| **Q45** | **GPU arbitration** between a resident vLLM server and experiment containers competing for VRAM. | Detect VRAM pressure and surface an explicit "unload local model" control. No automatic eviction. |
| **Q46** | **Code editor component** — Monaco (VS Code's editor, the familiar feel) vs reusing **CodeMirror 6**, already chosen for LaTeX in D30. | **CodeMirror 6** — one editor stack, much lighter, already a dependency. Monaco only if the VS Code feel turns out to matter. |
| **Q47** | **Distribution for self-install** — AppImage, or just `git clone` + `npm run`? | `git clone` + a `make dev` target. Student scope; no packaging pipeline. |
| **Q48** | **Voice replacement is unvalidated.** The D2 amendment moves STT/TTS local (`faster-whisper` + Piper) because Web Speech does not work in Electron — but this has not been prototyped, and it adds real weight to the sidecar. | Validate before Slice 1 ends. Voice is post-Slice-1 (D23), so this is not blocking; if local STT proves heavy, voice slips rather than the pivot reversing. |

### Previously resolved (web product)

**Architecture (D1–D20), build-spec (D21–D24), detailed-spec (D25–D29), and frontend (D30) were
all resolved as of 2026-07-23** — several now amended or retired by D31–D36 above.

> **2026-07-31.** `FRONTEND_BRIEF.md` (screens, flows, style, schema) was **deleted** as
> redundant — this file is the spec. Recoverable from git history if a screen-by-screen
> restatement is ever wanted. Visual design now lives in **`UI_DESIGN.md`** (imported from the
> Claude Design project); it is **inspiration-rank — below this file** on behaviour, data,
> screens, and flows, and authoritative only on look-and-feel. Note it supersedes **D30's warm
> sepia palette** with a cool blueprint palette; the rest of D30 stands.

*(Next, on the user's instruction only: write PRD / TRD, with user-tagged skills.)*

Deferred to **implementation time** (not architecture blockers):
- Feed fetch-vs-rank tuning as the corpus grows; interest-profile refresh cadence.
- Full negative-example learning for feed dismissals (v1 = light down-weight only).
- Two-layer extractive→paraphrase card display (v1 = extractive-only).
- GROBID-as-a-service upgrade if docling's reference extraction proves insufficient.
- ~~Multi-tenancy / scaling widening~~ — **dropped 2026-08-01**, not a goal (D27 retired).

*(Closed/parked: Q6 rate-limiting — dissolved by single-user; ~~offline/desktop — moot, fully
web-based~~ **→ reopened and resolved as D31, 2026-08-01**; feed→harness wiring — feed is a D14
job, now catch-up-on-launch rather than cron.)*

**Dead by the 2026-08-01 pivot — do not re-derive:** Supabase (D13), GoTrue auth + `owner_id` +
RLS (D27), Cloudflare Pages / HF Space deployment (Q3), BYO Google Drive / OneDrive storage +
the three-blob-class scheme + storage quota (Q5), browser Web Speech API as the v1 voice
mechanism (D23), and the hosted demo URL.
