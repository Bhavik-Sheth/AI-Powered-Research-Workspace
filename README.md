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

### 1. Backend

```bash
cd backend
uv sync
cp .env.example .env      # edit if you want to paste a provider key — see below
uv run python main.py
```

This checks the vault folder, brings up Postgres+pgvector in Docker, runs migrations, and starts
the API. It prints the port it bound to (e.g. `34585`) — keep this terminal open.

### 2. Give it a real model to test with

The app normally takes a provider key through the onboarding wizard in the UI (validated live,
then encrypted at rest — D13). To skip the wizard for quick backend testing, paste a key into
`backend/.env`:

```
GROQ_API_KEY=gsk_...
```

(a free key: <https://console.groq.com/keys>), then with the backend from step 1 still running, in
a second terminal:

```bash
cd backend
uv run python scripts/configure_provider.py
```

This saves it through the exact same validated, encrypted-storage path the UI uses — it isn't a
side door. Any LiteLLM-supported provider works the same way: `uv run python
scripts/configure_provider.py openai gpt-4.1-mini` with `OPENAI_API_KEY` set in `.env`.

### 3. Frontend

```bash
cd frontend
cp .env.development.example .env.development.local   # set VITE_DEV_PORT to the port from step 1
npm install
npm run dev
```

Open the printed `http://localhost:5173` URL.

### Or: the real desktop app

`npm run dev:desktop` from the repo root launches the actual Electron shell, which spawns the
sidecar and generates its own per-launch token itself — no `.env` needed for this path.

### Running the tests

```bash
BEARER_TOKEN=devtoken PYTHONPATH=backend uv run --project backend pytest tests/ -q
```

## What's built so far

Phase 1 (US1–US7) and Voice.1 are implemented and live-verified end to end:

- **Search & library** — federated search across arXiv/OpenAlex/Semantic Scholar, deduped and
  reranked; add a paper to a project's library.
- **Reader** — renders the real PDF via PDF.js, with a validated extractive card (problem / method
  / datasets / results / limitations, each a verbatim, offset-checked quote).
- **Companion** — select text in the reader for "Ask about this" / "Highlight" / "Explain"; the
  Companion answers over WebSocket with every factual claim backed by an inline citation, and a
  claim that fails validation is shown as `⚠ unverified` rather than trusted.
- **Notes** — plain markdown files, written and indexed in the same operation.
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
