---
name: literature_review
description: Use when the user asks to do a literature review, survey the literature, build a reading list, or wants a structured pass over multiple papers on a topic (not a single-paper question).
tools: [search_papers, add_paper, get_paper, save_note, mark_relevant]
---

1. Confirm the topic and scope in one sentence back to the user (subfield, time range, or method
   the user cares about) before searching, unless they already gave enough detail to search
   directly.
2. `search_papers(query)` with the topic. If the first search returns fewer than 3 relevant
   results, try one alternate phrasing before giving up — do not report "nothing found" from a
   single query.
3. For each candidate that looks relevant from the search result's title/snippet, `add_paper` it
   to the project so it has a stable `paper_id` — never discuss or cite a paper that has not been
   added.
4. For each added paper, `get_paper(paper_id)` to pull its extracted fields (problem, method,
   results) before saying anything about its content.
5. `mark_relevant(paper_id, relevance)` for every paper you evaluated, even the ones you are
   excluding — a paper you looked at and rejected should not silently vanish from the record.
6. Once every candidate has been fetched and marked, `save_note` summarizing the set: one line per
   paper (title, one-sentence contribution, one-sentence limitation), each factual claim inside
   `<cite>` tags quoting the paper's own card field. Do not summarize a paper you have not called
   `get_paper` on in this turn.
7. Close with a short synthesis (2-4 sentences, outside any `<cite>` tag) noting where the papers
   agree, disagree, or leave a gap — this is your own judgement, not a quote.
