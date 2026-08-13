---
name: find_related_work
description: Use when the user asks to find related work, find papers similar to this one, see what else has been written on this topic, or check what this paper cites or is cited by.
tools: [search_papers, add_paper, get_paper, open_paper]
---

1. If a paper is currently selected or open, `get_paper(paper_id)` first so the search below is
   grounded in its actual problem/method, not a guess at the topic.
2. `search_papers(query)` with 1-2 query variants built from that paper's problem and method (or
   from the user's own description if no paper is selected) — a single narrow query under-covers
   this task.
3. For each result that looks genuinely related (shared problem, method, or dataset — not just a
   shared keyword), `add_paper` it so it has a stable `paper_id`, then `open_paper` it so the user
   can see it in the reader.
4. Report the set as a short list: title plus one clause on *how* each relates to the source paper
   (same method, competing approach, dataset it introduced, etc.) — every relational claim needs a
   `<cite>` quote from the candidate's own card, not an inferred similarity.
5. If nothing found clears the bar in step 3, say so plainly rather than padding the list with
   loosely related results.

Note for maintainers: this is the deliberately-manual fallback shape of this skill. Once
`deep_research` (HarnessPlan H9's subagent tool) exists, steps 2-3 should be replaced with a single
`deep_research(question, max_sources=10)` call and `deep_research` added to this skill's `tools:`
list above — do not add it before the tool itself is registered, since a skill naming an
unregistered tool fails `skills.load_index()`'s startup validation.
