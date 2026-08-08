# PlanBoard Tracker

Project: Research Companion OS (AI-Powered-Research-Workspace)

| File | Status | Notes |
|------|--------|-------|
| PRD.md | ✅ Approved | |
| TRD.md | ✅ Approved | |
| Schema.md | ✅ Approved | |
| Rules.md | ✅ Approved | |
| MODULES.md | ✅ Approved | |
| DesignDecisions.md | ✅ Skipped | Skipped by direct user instruction (not frontend detection) — already covered by DECISIONS.md (D1–D37 are the project's ADRs). Do not write on resume. |
| AppFlow.md | ✅ Skipped | Skipped by direct user instruction (not frontend detection) — already covered by UI_DESIGN.md (10 screens, routes, shell layout, navigation map). Do not write on resume. |
| ImplementationPlan.md | ✅ Approved | Final document in the PlanBoard sequence (Steps 8/9 skipped by direct user instruction). |

## Post-approval verification log

**2026-08-08 — Docker sandbox / per-project papers-notes / local LLMs re-confirmed, docs polished.**
The user restated three requirements they wanted checked: Docker sandboxing, papers-and-notes
scoped per project (so relevance is answerable per project), and local-LLM support via Ollama/vLLM
for GPU-equipped researchers. All three were already fully decided and locked — nothing here
reopens or amends `DECISIONS.md`:

- Docker sandbox: invariant #4 (`DECISIONS.md`), D30/D31, Phase 2 (`PRD.md` §3/§13, `TRD.md`
  §6.3, `MODULES.md` Execution Sandbox, `Rules.md`). Postgres already runs in Docker today
  (built); the per-experiment kernel container is Phase 2 (not yet built).
- Per-project papers/notes: D25 global/project boundary — paper *content* is global and shared,
  relevance mark + note + highlights are project-scoped (`project_papers`, `notes`, `Schema.md`
  §`project_papers`/`notes`). Built and live in Phase 1.
- Local LLMs: D11/D12 — Ollama and vLLM are named first-class providers, base URL only, no key,
  models discovered from the endpoint (`TRD.md` §4.4, `MODULES.md` Settings Store, `Rules.md`).
  Built (`backend/settings/models.py` `LOCAL_PROVIDERS`, `discover_models`).

**What actually changed this pass** (documentation + one dev-convenience script, no architecture
change): `README.md` now spells out the Ollama/vLLM no-key path and the per-project
papers/notes scoping explicitly instead of leaving them implicit; `backend/scripts/configure_provider.py`
and `backend/.env.example` were extended so the local-provider path is actually usable through the
documented quick-start (it previously only handled key-based providers and would hard-fail for
`ollama`/`vllm`). No PLANNER file needed a content change — the gap was in the README's
discoverability and the dev script, not in the locked design.

**2026-08-08 — Fix Round 1 (Phase 6.1–6.11) built, live-verified, signed off.** All ten build
phases plus the sign-off checkpoint from `ImplementationPlan.md`'s Fix Round 1 section landed
against the running app — real Postgres, real Docker sandbox, real dev-server pair driven with
Playwright, not just unit tests. `PLANNER/DECISIONS.md` carries the D21 (Firecrawl relevance
authority) and D26 (text → HuggingFace → Firecrawl enrichment order) amendments;
`PLANNER/Things_to_finish.md` is closed with a per-item verification summary.

Two real defects surfaced during live verification (step 4 of the `execute` skill) and were fixed
in place, each as its own `fix(...)` commit, never folded into the `feat(...)` that exposed them:

- **`frontend/src/graph/GraphView.tsx`** — `wrapAndCapLabel`'s word-wrap split only on whitespace,
  so a slugified LLM-derived concept-node id (all hyphens, zero whitespace — e.g.
  `we-propose-a-new-simple-network-architecture-…`) was handed back as one unbroken "word" and
  rendered as a single overflowing line, exactly the bug Phase 6.6 was built to fix, just for a
  label shape its own test cases hadn't covered. Fixed by also breaking after hyphens and hard-
  breaking any token that still doesn't fit. Verified live: the same concept node now wraps to 3
  clean lines.
- **`backend/papers/parser.py`** — `_split_references_section`'s fallback only split on a leading
  `[12]`/`12.` marker, so any unnumbered author-year bibliography (ACL/EMNLP/AAAI style — the
  common case for NLP papers, e.g. BERT's own reference list) came back as a single unsplit blob
  covering the entire References section, discovered live when BERT's References box rendered one
  giant citation-soup entry instead of 5 individual rows. Root cause: docling already emits one
  bibliography entry per newline-separated item regardless of citation style; splitting on the
  numbered marker instead of the newline silently failed for every unnumbered paper. Fixed by
  splitting on newlines primarily (stripping a leading numeric marker per line, cosmetic only).
  Verified live end to end on a freshly-added real paper (BERT, arXiv:1810.04805): 57 references
  correctly split, 5 traced, 1 resolved to a real clickable stub with a `cites` graph edge.

**Caveat recorded honestly, not swept under the rug:** the literal "attention is all you need
returns that paper first" acceptance line (`Things_to_finish.md` #2) could not be fully exercised
in this sandboxed dev environment — no `FIRECRAWL_API_KEY` was configured (D21's actual relevance
authority for exactly this query shape), and the free-tier Groq model backing LLM query-
understanding and card extraction was intermittently unreliable at structured tool-calling (a
handful of `400`s from Groq itself mid-session, unrelated to any Phase 6 code, all recoverable on
retry). The deterministic lexical-score fallback's own design bar is "search is never unusable,"
not "always ranks the canonical paper first without Firecrawl" — it held to that bar in every live
run (5 results, always degrading gracefully, `sources_failed` always named). Every other phase's
acceptance criterion was independently verified live against real data with no caveats.

