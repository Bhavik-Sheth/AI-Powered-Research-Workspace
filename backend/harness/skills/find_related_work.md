---
name: find_related_work
description: Use when the user asks to find related work, find papers similar to this one, see what else has been written on this topic, or check what this paper cites or is cited by.
tools: [search_papers, add_paper, get_paper, open_paper, deep_research]
---

1. If a paper is currently selected or open, `get_paper(paper_id)` first so the search below is
   grounded in its actual problem/method, not a guess at the topic.
2. `deep_research(question, max_sources=10)` — build `question` from that paper's problem and method
   (or from the user's own description if no paper is selected). The subagent runs several query
   variants for you and already judges each candidate for genuine relevance — shared problem,
   method, or dataset, not just a shared keyword — so its survivors are the set to work from; for
   each one, `add_paper` it so it has a stable `paper_id`, then `open_paper` it so the user can see
   it in the reader.
3. Report the set as a short list: title plus one clause on *how* each relates to the source paper
   (same method, competing approach, dataset it introduced, etc.) — every relational claim needs a
   `<cite>` quote from the candidate's own card, not an inferred similarity.
4. If `deep_research` returns nothing, say so plainly rather than padding the list with loosely
   related results.
