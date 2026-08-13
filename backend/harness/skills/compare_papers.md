---
name: compare_papers
description: Use when the user asks how two or more papers differ, agree, or which is better, or asks any question that names more than one paper.
tools: [compare, get_paper, create_highlight]
---

1. Confirm every paper named is in the open-papers list (band 1's "Papers currently open in the
   reader" block). If one is not open, say so explicitly and stop rather than answering from
   training knowledge — do not compare against a paper that is not in the read set.
2. `get_paper(paper_id)` for each paper being compared, even if you already discussed one of them
   earlier this turn — the card is the only source of quotable fields. `compare(paper_ids)` gathers
   several papers' cards side by side in one call when there are more than two.
3. For each dimension the user asked about (method, results, dataset, limitations, etc.), quote one
   verbatim span per paper inside `<cite>` tags, copying the source's exact wording.
4. If a paper's card has no field for a dimension, write "not stated" for that paper on that
   dimension — never infer or fill it in from the other paper's value.
5. When a specific passage is central to the comparison, `create_highlight` on it so the user can
   jump straight to it in the reader.
6. End with one sentence of your own judgement (which paper is stronger on what, and why), written
   outside any `<cite>` tag so it reads clearly as your assessment, not a quote.
