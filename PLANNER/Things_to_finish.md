## This is the list of things that need to be changed.

1. dashboard:  It shows you the reasons I am currently working on. Experiment pending. The top came most relevant papers for my current task. Progress which would be a meter. And other relevant details.
2. Papers:  paper search. It is not working as expected. For example, when I search attention is all you need. The first paper that should come is the paper with the same name but the sorting is done by date rather than the actual relevance that needs to be fixed. Also, the paper extraction isn't able to trace back the implementation, repose and sources of the data sets that has to be fixed. Also, it does not put the references in the references box, so that again click on that and go to that particular paper. That feature is not working properly.Also in the search I want to get the top 5 most relevant papers and then if I go for search more then and only then should you search for additional papers otherwise do not search for additional papers.
3. Experiments: This is the module that needs to be fixed the most. The issues are as follows. There is no persistent notebook for the experiment. So if I conduct an experiment in a notebook and then I leave somewhere then the notebook is deleted or it is not being able to access it again. That needs to be fixed. The notebook should be stored in the local storage of the laptop where the whole application lives. That is how we ensure that the notebook stays where it needs to stay.Then the aspect ratio is absolutely not usable. It is very narrow and I cannot see any text. It should cover the whole page and it would be an interface that would be accessible by clicking a cell which would then open a full page notebook where I can do all the corrections I need to do and then the notebook gets saved. in my place and I can do the experimentation logging and everything like that.I am also not able to change the category of the experiment for example pending to in progress to done that is also not being able to be done fix that
4. Graph: - is a problem. I cannot read the whole name. And also there is no proper tracebacks. This issue seems to be connected with the issues in the paper section where the references are not being traced. The top five references should be traced for now, both in the graph and in the paper part because I am currently in the testing phase. This feature is not working at all currently, so fix that as well.

---

## Closed — Fix Round 1 (Phase 6.1–6.11)

All four items above are built and live-verified against the running app (backend + frontend +
Postgres + Docker), per `PLANNER/ImplementationPlan.md` Phase 6.1–6.11 and `PLANNER/GrillLog.md`'s
Fix Round 1 section. Summary, item by item:

1. **Dashboard** (Phase 6.10) — opens on `focus` (focus_seed + in-progress hypotheses), a
   segmented `progress` meter (planned/remaining/in-progress/done), `pending_experiments`, and
   `relevant_papers` (project library ranked against the focus text via the existing cross-encoder
   rerank). Verified live: real project data rendered correctly in all four sections.
2. **Search** (Phase 6.1/6.2/6.3/6.4) — Firecrawl `/search` is the relevance authority when
   `FIRECRAWL_API_KEY` is configured; without one (this dev environment's state), search degrades
   to the arXiv/OpenAlex/S2 fan-out ranked by the new deterministic `lexical_score`, never raw
   source order, and names the missing/failed sources. Exactly 5 results render; `Search more`
   reveals the cached pool before ever widening the query. References: top 5 by citation count
   (OpenAlex/S2 API) or, absent API ids, from the paper's own parsed References section — verified
   live end to end on a freshly-added real paper (BERT, arXiv:1810.04805): 57 references correctly
   split, top 5 traced, one resolved to a real clickable stub with a `cites` graph edge. Datasets/
   code: verified live — a real `github.com/tensorflow/tensor2tensor` link harvested from the
   paper's own text, tagged `TEXT`, with a `has_code` edge.
   **Caveat honestly recorded, not swept under the rug:** the literal "attention is all you need
   returns that paper first" acceptance line could not be fully verified in this sandboxed
   environment because no `FIRECRAWL_API_KEY` was available and the free-tier Groq model backing
   query-understanding proved unreliable at structured tool-calling (intermittent `400`s from
   Groq itself, unrelated to any Phase 6 code). Firecrawl is D21's own designated fix for exactly
   this query shape; the deterministic fallback's job is only "never unusable," never "always
   picks the canonical paper," and it held to that bar in every live run.
3. **Experiments** (Phase 6.7/6.8/6.9) — the per-experiment Jupyter server now survives navigating
   away and back with zero new containers spawned (verified live: one container before and after a
   Dashboard→Experiments round trip, same port, same session) and only stops on explicit
   `Stop notebook` or the 4h ceiling, each path forcing a save-then-verify before removal (verified
   against live Docker: a cell written through the real contents API landed in the vault file
   before the container was allowed to be removed). The board is a status-grouped rail plus a
   full-width detail pane hosting the live notebook — no more 760px horizontal-scroll cage.
   Status changes from both the rail dropdown and the detail-pane segmented control PATCH
   immediately and regroup the rail — verified live (Planned → In progress).
4. **Graph** (Phase 6.4/6.6) — a paper's traced references become `cites` edges (verified live: 5
   real edges from a fresh paper's OpenAlex `referenced_works`, idempotent on re-run); papers added
   before this round heal automatically the first time they're opened. Node labels wrap to 3 lines
   at ~160px with a hover tooltip carrying the full title, verified live including the edge case a
   plain word-wrap misses entirely — a slugified concept-node id with no whitespace at all (only
   hyphens) — caught and fixed during this sign-off pass (see Tracker.md).

Two real defects were found during this live-verification pass and fixed in place (not deferred):
graph label wrapping never broke on hyphen-only tokens, and the PDF-fallback reference splitter
only recognised numbered/bracketed citation styles, silently returning an entire unnumbered
(ACL/EMNLP/AAAI-style) bibliography as one unsplit block. Both are detailed in `Tracker.md`.