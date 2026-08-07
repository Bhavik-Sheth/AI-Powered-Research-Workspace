# Research Companion OS — Product Vision

A **local desktop workspace** for a **solo researcher**, with an AI companion beside the work.

The goal is **not to generate research or write papers**, but to eliminate research friction. The
researcher remains in control of all thinking and writing, while AI handles discovery, retrieval,
organisation, navigation, experiment support and context management.

Everything related to a research project lives in one persistent workspace — **plain files in a
folder you own**, readable with the app closed.

> **Scope note.** This file is the **what**; `DECISIONS.md` is the **how**, and **`DECISIONS.md`
> decides scope.** Where this file reads as broader than the decisions, the decisions win. It is
> a description of the product, not a licence to add features.

---

# Core Principles

- AI assists, never replaces the researcher.
- The researcher writes all content.
- Evidence over generated text — nothing is attributed to a paper without a verbatim quote from it.
- Persistent memory across the entire project.
- Your data is plain local files you own.
- **The agent never runs code without your approval**, and all code runs sandboxed in Docker.
- Voice as a natural interface to the workspace, not a separate mode.
- One workspace for the complete research lifecycle.

---
# Features
---
## Project Workspace

Each research project is a self-contained workspace containing:

- Papers (relevant-to-this-project, with your reason why)
- Notes
- Ideas
- Experiments (each with a status: planned / remaining / in-progress / done)
- References
- Conversations with the Companion
- Knowledge graph

Everything is linked together and searchable. Notes and experiments belong to the project; paper
*content* is parsed once and shared across projects, while relevance, notes and highlights stay
project-scoped.

---

## AI Research Search

A research-first search engine that searches academic and technical sources instead of the
general web.

Sources:
- arXiv, OpenAlex, Semantic Scholar (searched on every query)
- Papers with Code, GitHub (enrichment when you open a paper — code, datasets, benchmarks)
- Crossref (to resolve a DOI the others missed)

Supports natural language and voice queries.

**Two stages, deliberately.** The results list shows the abstract and metadata — title, venue,
year, citations, code link. When you *open* a paper, it is split into a structured card:

- Problem
- Proposed method
- Datasets
- Results
- Limitations
- Code availability
- Why it is relevant

Every field in that card is a **verbatim quote from the paper**, linked to the exact span. If the
paper doesn't state something, the field reads **"not stated"** — never a guess.

---

## Reading Workspace

An interactive reader — the real PDF, with AI as a reading companion.

While reading, you can:

- Ask questions about any highlighted text
- Ask about equations and figures
- Compare methods with previously read papers
- Jump to referenced papers
- Open implementation repositories
- View cited datasets
- Mark the paper as relevant, somewhat relevant, or not very relevant

Answers about the paper carry inline citations to the spans they came from, shown visually
distinct from the model's own reasoning.

The goal is to understand papers without leaving the workspace.

---

## Research Memory

Persistent memory for the entire research project.

The system remembers:

- Papers read
- Notes
- Previous conversations
- Research ideas
- Important concepts
- Experiment history

Example:

> "Didn't we already read a similar method?"

It retrieves the relevant papers, notes and discussions — **and cites the rows they came from**,
so you can check.

---

## Experiments

Running the experiment is half of research, so it lives in the workspace rather than outside it.

Each experiment is a record — hypothesis, setup, metrics, notes, status — attached to a
**notebook that runs in a Docker container**:

- The Companion can **write code into a cell**, but **you approve every run.** It never executes
  anything on its own.
- Everything runs sandboxed: pinned base image, per-experiment dependencies, no network by
  default, only the experiment folder mounted.
- A metric counts as **measured** only when it comes from a clean *restart-and-run-all* — captured
  with its image, dependencies and notebook hash. Anything else is a number you vouched for by
  hand. **The AI never authors a result.**

Measured metrics sit as comparable rows next to papers' extracted results.

---

## Knowledge Graph

Builds connections between:

- Papers
- Methods
- Models
- Datasets
- Authors
- Research ideas
- Personal notes

Citation, authorship, dataset and code edges come from the source APIs, so they are exact.
Method- and idea-level edges are extracted **only from papers you actually opened** — a graph
whose value is trustworthiness cannot afford invented edges.

Allows visual exploration of relationships and research directions.

---

## Literature Matrix

A comparison table across papers you select.

Typical columns:

- Problem
- Method
- Dataset
- Results
- Strengths
- Limitations
- Personal notes

Standard columns are a **projection of the extractive cards already built** — no re-extraction,
so provenance holds. You can add custom columns, and every cell is editable; edits are labelled
as yours rather than overwriting what the paper said.

---

## Research Feed

A personalised feed of newly published work, based on the project's interest profile — a
`{categories, keywords}` profile you can read and edit, seeded when you create the project and
refined as your library grows.

Tracks new papers in your categories, with their code and datasets where available. Ranking is
deterministic — keyword match, embedding similarity, reranking — with no LLM in the scoring path,
and each item explains why it surfaced.

Runs as a catch-up job when you open the app, not a live request.

---

## Writing Workspace

A distraction-free LaTeX editor integrated with the workspace: source on one side, live preview
on the other.

- Citation search and insertion (pulls from the project's references)
- Reference management
- Figure and table management
- Cross-reference support
- Consistency checks
- Missing citation detection

**The AI never writes sections of the paper.** It assists with verification, structure and
citations; the researcher is the author.

---

## Voice Companion

Voice is available throughout the workspace, running **entirely on your machine** — push-to-talk,
local speech recognition and local speech output. Nothing is sent anywhere to be transcribed.

Examples:

> "Find papers newer than this."

> "Compare this method with RAPTOR."

> "Open the implementation."

> "Show papers using this dataset."

> "Summarize my notes from yesterday."

Voice is a way to talk to the same Companion already on screen — the same tools, the same memory,
the same answers. It navigates the workspace instead of replacing it.

---

# Vision

Research Companion OS is a persistent local workspace that combines research search, paper
reading, note-taking, experimentation, writing, memory and project organisation into a single
system.

Instead of replacing researchers, it helps them spend less time searching and organising
information, and more time thinking and doing research.
