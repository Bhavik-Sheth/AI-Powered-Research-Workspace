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

