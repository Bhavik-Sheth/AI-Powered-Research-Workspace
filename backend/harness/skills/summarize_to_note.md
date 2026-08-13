---
name: summarize_to_note
description: Use when the user asks to summarize a paper, save a summary, take notes on what they're reading, or turn the current discussion into a note.
tools: [get_paper, save_note, update_note, query_memory]
---

1. Identify the paper to summarize — the currently selected paper, or one the user names. If
   neither is clear, ask which paper before writing anything.
2. `query_memory` for the paper's title or topic to check whether a note already exists for it. If
   one does, prefer `update_note` on that note over creating a duplicate with `save_note`.
3. `get_paper(paper_id)` to pull its extracted fields.
4. Write the note in this fixed shape, every time:
   - **Problem** — one sentence, `<cite>` quoting the card's problem/motivation field.
   - **Method** — one sentence, `<cite>` quoting the card's method field.
   - **Results** — one or two sentences, `<cite>` quoting the card's results field.
   - **Limitations** — one sentence; `<cite>` if the card states one, otherwise write "not stated".
   - **Why it matters** — one sentence of your own judgement, outside any `<cite>` tag.
5. If a card field is missing, write "not stated" for that line rather than inventing content or
   skipping the line — the shape stays the same even when a field is empty.
6. `save_note` (or `update_note`, per step 2) with the finished note, then tell the user in one
   short sentence that the note was saved.
