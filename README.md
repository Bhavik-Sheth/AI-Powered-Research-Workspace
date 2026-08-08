# AI-Powered-Research-Workspace

**Research Companion OS** — a local desktop workspace for doing all your research, without having
to waste time searching and organising knowledge.

Search the literature, read papers with an AI companion that cites its evidence, keep persistent
project memory, run experiments in a Docker sandbox you approve, and write it up — all against
plain files in a folder you own.

- **What it is:** `Research Companion Workspace OS.md`
- **How it's built, and what's in scope:** `DECISIONS.md` (D1–D37) — authoritative
- **How it looks:** `UI_DESIGN.md` — look-and-feel only

## Running it locally

**Prerequisites:** Docker, Python 3.12+ with [`uv`](https://docs.astral.sh/uv/), Node 20+.

### One-time setup

```bash
cd backend && uv sync && cp .env.example .env && cd ..
cd frontend && npm install && cp .env.development.example .env.development.local && cd ..
npm install   # root workspaces (frontend, desktop, api-client)
```

Then paste a key into `backend/.env` (see "Give it a model to test with" below) and run
`uv run --project backend python scripts/configure_provider.py`.

### Run everything: one command

```bash
npm start
```

From the repo root. This builds the frontend, launches the Electron desktop app, which in turn
spawns the Python backend as its sidecar — bringing up Postgres in Docker, running migrations,
and starting the API — and loads the built frontend into the app window. Closing the window stops
the backend (and the Docker container) with it.

Prefer separate terminals with hot-reload instead? Use the steps below.

### 1. Backend

```bash
cd backend
uv sync
cp .env.example .env
uv run python main.py
```

Brings up Postgres in Docker, runs migrations, starts the API on port `41500`. Keep it running.

Auto-reload on save instead:

```bash
uv run uvicorn main:app --reload --host 127.0.0.1 --port 41500
```

### 2. Give it a model to test with

Paste a key into `backend/.env`, then run the config script:

```bash
GROQ_API_KEY=gsk_...   # free key: https://console.groq.com/keys
```

```bash
uv run python scripts/configure_provider.py
```

Other providers: `configure_provider.py openai gpt-4.1-mini` with `OPENAI_API_KEY` set. Local
GPU: set `OLLAMA_BASE_URL` in `.env`, then `configure_provider.py ollama` — no key needed.

### 3. Frontend

```bash
cd frontend
cp .env.development.example .env.development.local
npm install
npm run dev
```

Open the printed URL.

### Or: the desktop app

`npm run dev:desktop` from the repo root — launches Electron, which starts the backend itself and
loads the frontend from `frontend/dist` (run `npm run build:frontend` first, or just use
`npm start` above, which does both).

### Tests

```bash
BEARER_TOKEN=devtoken PYTHONPATH=backend uv run --project backend pytest tests/ -q
```

## What's built so far

Phase 1 (US1–US7) and Voice.1 are implemented and live-verified end to end:

- **Search & library** — federated search across arXiv/OpenAlex/Semantic Scholar, deduped and
  reranked; add a paper to a project's library and mark it relevant / somewhat / not relevant with
  your own note on why. A paper's *content* (PDF, parsed text, embeddings) is fetched once and
  shared if you add it to a second project, but its relevance mark and your note are scoped to
  **that** project — so "what papers mattered here" is always answerable per project, even for a
  paper that lives in several.
- **Reader** — renders the real PDF via PDF.js, with a validated extractive card (problem / method
  / datasets / results / limitations, each a verbatim, offset-checked quote).
- **Companion** — select text in the reader for "Ask about this" / "Highlight" / "Explain"; the
  Companion answers over WebSocket with every factual claim backed by an inline citation, and a
  claim that fails validation is shown as `⚠ unverified` rather than trusted.
- **Notes** — plain markdown files, written and indexed in the same operation, and always scoped
  to the project that owns them (`projects/<slug>/notes/*.md`) — there is no global notes pile.
- **Project memory** — `notes`/papers you've added are chunked and embedded; the Companion can
  search across them (hybrid dense+lexical retrieval, cross-encoder reranked) and cites the
  specific row an answer came from.
- **Tab stack** — the center pane persists multiple open tabs (papers, search, notes) across an
  app restart; a Dashboard shows "continue where you left off."
- **Interrupt** — stopping a Companion turn mid-answer is real: partial text is kept, not
  discarded.
- **Voice (stub)** — push-to-talk in the Companion: hold the mic button, speak, release — the
  audio goes through the same turn a typed message would. The speech-to-text/text-to-speech
  engines are stubbed (canned text / silence) pending the real local models (`faster-whisper` /
  Piper); everything around them — capture, transport, the module boundary — is real.

Not yet built: execution sandbox / experiments (Phase 2), literature matrix and knowledge graph
(Phase 3), the LaTeX writing workspace (Phase 4), and the research feed (Phase 5).

**On the Docker sandbox specifically:** Docker is already load-bearing today — it's how Postgres
runs (step 1 above) and it's a hard, verified-at-onboarding dependency for the whole app, not an
optional extra. What's *not* built yet is Phase 2's per-experiment kernel: a notebook cell the
Companion can propose but never run on its own, executed only inside its own throwaway container
(pinned image, no network by default, only that experiment's folder mounted) after you explicitly
approve the run. That approval gate and the sandbox are independent, both mandatory, and neither
is a config flag — see `DECISIONS.md` invariants #4–#5.
