# Research Companion OS — Architecture Decisions

Recorded 2026-07-21. Companion to `Research Companion Workspace OS.md` (the product vision).
This file is the **how**; that file is the **what**.

---

## Product shape

**D1. Target — hosted, multi-user, free (v1), real use + portfolio.**
Not a solo local tool, not a pure demo. Auth and multi-tenancy from day one; no billing in v1.

**D2. Voice — WebRTC streaming STT → text agent → TTS.**
Not a local speech-to-speech model (incompatible with hosted multi-user) and not a realtime
s2s API (cost, hard to ground in workspace tools). Voice is a thin client over the same tool
layer the text UI uses, so it can be swapped for realtime s2s later without touching the agent.

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

**D5. Postgres only.**
`pgvector` for embeddings, `tsvector` for BM25, join tables + recursive CTEs for the knowledge
graph, JSONB for paper metadata. No Qdrant, no Neo4j. Split a store out only when a query
actually gets slow — it won't at solo-researcher data volumes.

**D13. Supabase — auth + Postgres + storage.**
GoTrue auth (email/Google/GitHub), managed Postgres with pgvector, S3-compatible storage for
PDFs, row-level security. FastAPI verifies the JWT and keeps full SQL control. Saves ~2 weeks
of undifferentiated auth/storage plumbing.

**D14. Background jobs — Postgres-backed queue (SAQ w/ Postgres backend, or pgqueuer).**
No Redis. Transactional enqueue, one less service to run. Jobs: PDF fetch/parse, embedding,
structured extraction, feed polling.

**D17. Repo — flat monorepo with npm workspaces.**

```
backend/               FastAPI
frontend/              Vite + React (Tauri wraps this later)
packages/api-client/   TS client generated from FastAPI's OpenAPI schema
```

The generated client is the load-bearing part: a backend field rename becomes a **frontend
compile error**, not a runtime `undefined`. `apps/` prefix was considered and dropped as
pure ceremony.

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

**D12. Voice/NL → actions: intent router + tool-calling agent.**
A small set of typed tools (`search_papers`, `open_paper`, `compare`, `filter_by_dataset`,
`query_memory`, …). Fast path: cheap classifier/regex handles the ~15 most common commands with
zero LLM call. Fallback: tool-calling agent. This degrades gracefully — common voice commands
still work on weak open-weight models that tool-call badly.

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

## Open questions — resume here

Grilling stopped mid-Q18. Unresolved:

1. **Provenance enforcement (Q18)** — how "evidence over generated text" is enforced in code.
   Span-citation schema? Reject-if-uncited? Confidence surfacing? Applies to reader answers,
   literature matrix cells, and memory recall.
2. **Experiment logging** — named in the vision (planned / remaining / in-progress / done),
   never designed. Data model, UI surface, and whether it links to the graph.
3. **Deployment target** — Railway / Fly.io / VPS; how GROBID and the embedding server are hosted.
4. **Research Feed mechanics** — polling cadence, dedup against already-seen papers, relevance
   scoring model, and whether it needs the partial local index from D6's rejected hybrid option.
5. **PDF storage limits** — per-user quota, retention, dedup of identical PDFs across users.
6. **Rate limiting & abuse** — per-user caps on federated API fan-out (arXiv/S2/OpenAlex all
   have limits shared across your whole user base — this is a real scaling constraint).
7. **Reader UI** — PDF.js highlight anchoring, how highlights survive re-parsing.
8. **Offline/desktop story** — what the Tauri build does without network.
