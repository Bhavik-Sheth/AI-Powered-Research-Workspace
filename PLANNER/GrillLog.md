# Grill Log — Research Companion OS

status: complete
mode: A
started: 2026-08-01
completed: 2026-08-01

## Raw idea

Verbatim from the user:

> "I am done with the grilling session and the decisions are in the DECISIONS.md file. Definition
> of done: when everything in v1 scope is completely flawlessly built. The Frontend Design is in
> the UI_DESIGN.md"

## Prior art (do not re-ask)

Three prior grilling sessions already produced, in the repo root:

- `DECISIONS.md` — 947 lines, D1–D37, "fully specified, nothing blocks building". Settles: target
  user, platform (Linux desktop, Electron + FastAPI sidecar), single-user/no-auth, vault-as-truth,
  Postgres+pgvector in Docker, LLM layer (BYO key + local, LiteLLM), fixed embedding model, the
  7-node harness, tool catalog, retrieval/federation, provenance rules, data model, experiments +
  Docker sandbox + consent gate, frontend shape, onboarding, voice. Plus Appendix A (retired
  paths — never re-derive) and an "Open at implementation time" list of explicit non-blockers.
- `Research Companion Workspace OS.md` — product vision (the *what*).
- `UI_DESIGN.md` — look-and-feel reference, inspiration-rank, with its own §8 "not designed yet"
  and §9.2 outstanding-corrections log.

Anything settled in those three files is off-limits for this session.

## Open

**All five are now answered — see `## Resolved` (R1–R5). Nothing remains open.** Retained below
as the record of what was asked and what was recommended.

Questions a PRD writer would otherwise be forced to guess at, most pivotal first.

1. **v1 scope boundary.** D5 defines five build slices plus a cross-cutting voice layer, but never
   states which of them constitute "v1". The user's definition of done is "everything in v1
   scope", so this is load-bearing. (Complication: DECISIONS.md's prose uses "v1" as if it means
   the whole feature set — "bundle zero MCP servers in v1", "v1 indexes the structured record
   only" (Slice 2), "v1 = extractive-only" (Slice 3 matrix), "no GPU arbitration in v1" — which
   implies all five slices. But D36 says voice "slips" if local STT proves too heavy, so voice is
   conditional. Never stated outright.)
   - *Recommendation:* v1 = all five slices + the voice layer, because the decisions' own "in v1"
     qualifiers already reach into Slices 2, 3 and 5. Voice ships as the module boundary + stub
     engine at minimum; the real `faster-whisper` engine is the one droppable piece.

2. **What "flawlessly built" is evidenced by.** None of the three documents mentions tests, CI, or
   acceptance criteria — not once. A PRD needs a definition-of-done per slice or it cannot say
   when anything is finished.
   - *Recommendation:* per-slice manual acceptance checklist derived from the PRD, plus automated
     pytest coverage only where correctness is invisible to the eye — the provenance substring
     validator (D24), the canonical-id dedup (D25), the quote/fuzzy locator (D33), and the
     `measured`-metric gate (D29). No coverage percentage target, no CI service.

3. **Contingency if the Slice-2 kernel spike fails.** DECISIONS.md calls the sidecar ↔ Jupyter
   kernel transport "the least-proven part of the design" and a hard prerequisite of Slice 2 —
   the slice that justified the desktop pivot. If the spike does not pan out, what gives?
   - *Recommendation:* descope, don't slip — Slice 2 falls back to non-interactive
     "restart-and-run-all only" (`docker run` the notebook via `nbclient`, stream logs), which
     still yields `source: measured` metrics and the consent gate. The interactive kernel becomes
     post-v1. D29/D30/D31 semantics survive intact.

4. **Delivery granularity of the PRD and of review.** One v1 PRD covering all five slices, or one
   PRD per slice? And does the build halt for user review at each slice boundary?
   - *Recommendation:* one v1 PRD covering all five slices (so cross-slice contracts — the tool
     catalog, the memory index, the anchor object — are designed once), built and reviewed slice
     by slice, with a hard stop for your sign-off at each slice boundary.

5. **Center-pane tabs.** `UI_DESIGN.md` §9.2 B flags this as explicitly unresolved: the mock draws
   three open-paper tabs plus a `+`, but D32's router owns one center pane per URL. It is a real
   interaction-model change (routing, persistence, whether the companion follows the active tab),
   not a styling detail.
   - *Recommendation:* single pane per route for v1, as §2 already defaults. The tab styling stays
     in the design file, unbuilt, if it is ever wanted.

## Resolved

### R1 — v1 scope boundary: all five phases + voice

**Q:** D5 defines five build units plus a cross-cutting voice layer but never states which
constitute "v1". What is in v1?

**A (user):** All five, plus voice. **They are PHASES, not slices** — verbatim: *"all 5 + voice
but they are 5 phases not slices (the terminology is important)"*.

**Binding vocabulary rule.** Every downstream document — PRD, TRD, tickets, commit messages,
and any future rewrite of `DECISIONS.md` — says **Phase 1 … Phase 5**. The word **"Slice" is
retired.** D5's table and every "Slice N" reference elsewhere in `DECISIONS.md` (D5, D19's
"Execution (Slice 2)" and "Later slices" rows, D29, D36's "right after Slice 1") are to be read
and restated as Phase N.

So v1 = the whole product:

| Phase | Contents |
|---|---|
| **Phase 1** | Project workspace + AI research search + reader with ask-about-highlight + notes + retrieval over everything read |
| **Phase 2** | Experiments — notebook UI, Docker sandbox, kernel, consent gate, structured experiment record (D29–D31) |
| **Phase 3** | Reader depth + literature matrix |
| **Phase 4** | Writing workspace (LaTeX) |
| **Phase 5** | Research feed |
| **Voice** | Cross-cutting layer, added right after Phase 1 (D36) |

**Voice in v1, precisely.** Voice ships at minimum as **the D37 module boundary plus the stub
engine** — `backend/voice/` with its engine registry, `frontend/src/voice/` with push-to-talk
capture and playback, the transport wired end to end. The real `faster-whisper` / Piper engines
are **the one droppable piece** of v1: if the D37 spike proves the local models too heavy, the
engines slip post-v1 and nothing else changes. This is exactly D36's "voice slips, nothing else
does", made explicit as a scope boundary.

This also confirms the reading that `DECISIONS.md`'s existing "in v1" qualifiers already assumed:
"v1 indexes the structured record only" (Phase 2), "v1 = extractive-only" for the matrix
(Phase 3), "bundle zero MCP servers in v1", "no GPU arbitration in v1".

### R2 — Definition of done: manual checklist per phase + targeted pytest

**Q:** "Flawlessly built" needs evidence, and none of the three source documents mentions tests,
CI, or acceptance criteria. What proves a phase is done?

**A (user):** **A per-phase manual acceptance checklist**, derived from the PRD, **plus pytest
only where correctness is invisible to the eye.** Four named targets for automated tests:

- the **D24 provenance substring validator** — does a claimed quote actually resolve at the
  claimed offsets;
- the **D25 canonical-id dedup** — DOI → arXiv id → OpenAlex/S2 normalisation;
- the **D33 fuzzy quote locator** — whitespace / hyphenation / ligature normalisation across the
  docling ↔ PDF.js text streams;
- the **D29 `measured`-metric gate** — only a clean restart-and-run-all may produce
  `source: measured`.

**No coverage percentage target. No CI service.** Everything outside those four is verified by
eye against the checklist.

Rationale for the split: those four are the places where the system can be confidently,
silently wrong — a hallucinated quote that renders as if verified, a paper duplicated across
projects, a highlight that lands on the wrong span, a number that gains provenance it did not
earn. Every other defect is visible on screen.

### R3 — Phase 2 kernel-spike contingency: descope, do not slip

**Q:** `DECISIONS.md` calls the sidecar ↔ Jupyter kernel transport "the least-proven part of the
design" and a hard prerequisite of Phase 2. If the spike fails, does v1 slip or does Phase 2
descope?

**A (user):** **Descope rather than slip. v1 still ships.**

Fallback shape if the in-container kernel does not pan out: **non-interactive
restart-and-run-all only** — execute the whole notebook in the container via `nbclient` under
`docker run`, stream logs and outputs to the UI over the existing WebSocket (D18 node 5). The
**interactive stateful kernel moves post-v1**.

What survives the fallback unchanged, and why this is an acceptable descope: the run that
produces evidence was *already* defined as a clean restart-and-run-all (D29), so
**`source: measured` provenance is untouched** — image digest, `requirements.txt` hash, notebook
hash, `run_id`. The **consent gate (D31) is untouched** — `propose_cell` still never executes,
`run_all` still requires explicit approval. **Invariants #4 and #5 both hold.** What is lost is
only *exploration* — running cells out of order against warm state — which D30 explicitly
classified as the non-evidential half of the workflow.

### R4 — Delivery: one v1 PRD, built and signed off phase by phase

**Q:** One PRD for all of v1, or one per phase? And does the build halt for review at phase
boundaries?

**A (user):** **One v1 PRD covering all five phases**, so that the **cross-phase contracts are
designed once**: the tool catalog (D19), the memory index (D18 node 4 / D25), and the shared
quote-anchor object (D33 — the same object serves extractive-card `char_offsets` and reader
highlights).

**Built and reviewed phase by phase, with a hard stop for the user's sign-off at each phase
boundary.** No phase begins before the previous one is signed off.

### R5 — Center-pane tabs: BUILD THEM

**Q:** `UI_DESIGN.md` §9.2 B is explicitly unresolved — the mock draws three open-paper tabs plus
a `+`, while D32's router owns one center pane per URL, and §2 defaults to single-pane-per-route.

**A (user):** **Build the tabs.** This **overrides `UI_DESIGN.md` §2's stated default and closes
§9.2 B.** The §2 tab styling (bottom-attached to the content panel, active = `--surface` +
`box-shadow: 0 -2px 0 var(--accent) inset` + trailing `✕`, inactive = `--surface-inactive`, `+`
glyph at the end, panel welds via `border-radius: 0 12px 14px 14px`) is now spec, not
contingency.

What the answer commits to:

- **Tab state persists** — it is real state, not view-local ephemera.
- **The router needs a tab stack**, not a single center-pane URL. D32's routing statement ("the
  URL owns project + center-pane content") needs restating in the PRD to cover a stack of open
  center-pane routes with one active.
- **The reader supports multiple open papers simultaneously.**

**Derived consequence — a derivation from D32, not a user answer.** The Companion remains **one
WebSocket session per project, not per tab.** It does **not** switch sessions when the active tab
changes; the active tab is simply reported in the `ui_state` payload (D18 node 5 — UI-state
snapshot on each `user_message` plus incremental `ui_state` pushes). This follows directly from
D32's "one WebSocket session per project, surviving center-pane navigation", which tabs do not
change — they make navigation richer, not multi-session. Flag this line for confirmation if the
PRD author reads it as a decision rather than a consequence.

## Escalations

_(none)_

---

# Fix Round 1 — post-V0 issue grill

status: complete
started: 2026-08-08
completed: 2026-08-08
source of issues: `PLANNER/Things_to_finish.md`

## Root causes found in the code before asking anything

- **Search order.** `backend/search/reranker.py` downloads a ~91MB cross-encoder on first use with
  a 30s bound and no local cache; on timeout `search_papers._rank` silently returns the raw
  fan-out order (arXiv's 30, then OpenAlex's 30, then S2's 30) and only names `reranker` in
  `sources_failed`. No top-N cap existed anywhere.
- **Empty References box.** `papers/parser.py` collects only docling items labelled
  `DocItemLabel.REFERENCE`, which docling rarely emits, so `paper_content.references_` is normally
  `[]`.
- **No graph tracebacks.** Nothing in the backend ever writes a `cites` edge — the relation is in
  the CHECK constraint and the `GraphEdge` vocabulary, but has no writer.
- **No code/dataset traces.** `parse_paper_job` writes `datasets=[]` and `code_links=[]`
  unconditionally, and `enrich_paper_job`'s only source (paperswithcode.com) is discontinued — its
  API 301s to huggingface.co.
- **Notebook loss.** The notebook file *is* local and bind-mounted from
  `~/ResearchOS/projects/<p>/experiments/<e>/notebook.ipynb`, but
  `LiveNotebookPanel`'s unmount cleanup stops and removes the container on every collapse or tab
  switch, discarding anything Jupyter had not autosaved (~2 min window).
- **Notebook aspect ratio.** The panel renders inside one of four kanban columns; JupyterLab's own
  UI breaks below 760px, which the CSS worked around with horizontal scrolling.
- **No status change.** `PATCH /api/projects/:id/experiments/:experimentId` already accepts
  `status`; no UI ever calls it.
- **Graph labels.** `text-max-width: 90px` + `text-wrap: ellipsis` truncates every title mid-word.

## Decisions taken

1. **Build order** — search → references → graph → experiments → dashboard. References before
   graph because both symptoms share one root cause.
2. **Firecrawl is the relevance authority.** Firecrawl `/search` ranks; arXiv/OpenAlex/S2 are
   demoted to enrichment (canonical id, citation count, OA PDF). Amends D21.
3. **Firecrawl degradation** — missing key / quota / failure falls back to the existing fan-out
   ranked by a new deterministic lexical score (exact title, phrase overlap, citations), surfaced
   through the existing `sources_failed` card. Search is never unusable.
4. **Top 5, reveal-then-widen.** First pass shows exactly 5. `Search more` reveals the rest of the
   already-fetched pool with no network call; only once exhausted does a further click widen the
   query for real.
5. **References: API first, PDF fallback.** OpenAlex `referenced_works` / S2 `references` for the
   top 5 by citation count; parse the PDF's References section only for papers with no API record.
6. **Referenced papers become metadata-only stub rows** — real title in the graph, clickable in the
   References box, full fetch/parse/extract only on an explicit `Add to library`.
7. **Code/dataset tracing** — harvest URLs verbatim from the paper's own text first, then
   HuggingFace's papers API (PwC's actual successor), then Firecrawl search. The dead PwC call is
   removed outright. Amends D26.
8. **Backfill** — old papers heal the first time they are opened; the existing `Retry` forces it.
   No mass migration job.
9. **Notebook lifetime** — the server survives navigation and stops only on explicit Stop or the
   4h ceiling; every stop path forces a save through Jupyter's REST API and confirms the vault
   file landed before removing the container.
10. **Notebook layout** — the board becomes a ~240px status-grouped rail plus a wide detail pane;
    the Companion pane auto-collapses when width would push the notebook under Jupyter's 760px
    floor.
11. **Status controls** — segmented control in the detail header *and* a dropdown on rail items.
12. **Graph labels** — wrap to 3 lines at ~160px with more layout spacing, plus a hover tooltip
    carrying the full title.
13. **Dashboard "currently working on"** — no new `research_questions` table; reuse the project's
    existing `focus_seed` plus in-progress experiment hypotheses.
14. **Progress meter** — experiment completion only, as one bar segmented by the four statuses.
15. **Top relevant papers** — drawn from the project's own library, ranked against the current
    focus with the existing embedding/rerank machinery (not the Feed's unseen items).
16. **Dashboard retains** "Continue where you left off" and "Needs attention"; the four bare count
    tiles fold into the new blocks.
17. **DECISIONS.md is amended as part of the work** (D21, D26), not afterwards, so the locked
    architecture stays the single source of truth.
18. **Verification** — each phase ends with a live app check of the exact symptom; automated tests
    only where the logic is genuinely tricky (ranking, reference resolution, save-then-stop).

## Flagged, decided knowingly

- `FIRECRAWL_API_KEY` is read from `backend/config.py` / `.env`, not through Settings Store's
  encrypted provider path. D13 governs *LLM provider* credentials; Firecrawl is an infra key of the
  same category as the Postgres DSN. Recorded in the D21 amendment rather than left implicit.
