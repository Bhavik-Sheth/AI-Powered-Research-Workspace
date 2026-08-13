"""Context assembly (HarnessPlan H1/H3) — everything that decides what goes
into a turn's `llm_messages`, and how it is kept inside a fixed token
budget. H1 moved this logic out of `harness/__init__.py` unchanged; H3
(§3.2) adds the budget and eviction order on top of it: eviction removes
whole blocks — one history turn, one working-set line — never truncates
one, and always in the same order (history, then the working set); the
system prompt, tool schemas, and paper evidence for every open paper never
evict. Nothing outside `harness/` imports this module.

HarnessPlan H5 (§3.5) removes band 4 (a dedicated, always-present "retrieved
from the project's memory" block). Retrieval is no longer a pre-turn step
this module fetches rows for — `query_memory` is a tool the model calls
mid-loop (`harness/tools/memory.py`), and that call's own `tool_result`
already lands in `loop.py`'s `turn_exchange`, which is appended to
`llm_messages` after this module's blocks on every iteration. A separate
band-4 copy of the same text would be pure duplication of budget for
content the model has already seen once. `ContextBlock.band` keeps the
`Literal[1, 2, 3, 4]` shape (a future band could still use 4) but
`build_blocks` no longer produces one.
"""

import uuid
from typing import Literal, NamedTuple

import papers
from harness.models import Ref, SkillSpec, UIState
from llm import Message, count_tokens

SYSTEM_PROMPT = (
    "You are the Research Companion, helping a researcher with their project. "
    "Wrap every verbatim quote or specific fact drawn from the paper, a retrieved note, or a "
    "retrieved past conversation in <cite></cite> tags, copying the source's exact wording "
    "inside the tags. Put your own reasoning and commentary outside the tags. Make no factual "
    "claim from a source without a supporting quote."
)

# HarnessPlan H3, §3.2: headroom for a 32k-context local model (H1). A
# settings value in a later phase (`settings.context_budget_tokens`, §4); a
# constant for now so a 128k-context model can't yet raise it — that wiring
# is out of scope for this phase.
CONTEXT_BUDGET_TOKENS = 24_000


async def open_papers(session, open_paper_ids: list[uuid.UUID]) -> list[tuple[uuid.UUID, str]]:
    """Titles of the papers currently open as reader tabs — the read set a
    cross-paper claim (US4) is allowed to cite. A paper id that no longer
    resolves (closed/deleted mid-turn) is silently dropped rather than
    failing the turn."""
    out: list[tuple[uuid.UUID, str]] = []
    for paper_id in open_paper_ids:
        paper = await papers.get_paper(session, paper_id)
        if paper is not None:
            out.append((paper.id, paper.title))
    return out


# Bounded excerpt from `paper_content.full_text` when a paper has no
# extracted card yet — guaranteed substring-matchable against full_text,
# unlike the separate `papers.abstract` metadata column, so a verbatim
# copy the model makes still validates (D24).
_PAPER_EXCERPT_CHARS = 4000


async def paper_evidence(session, paper_id: uuid.UUID, title: str) -> str:
    """Deterministic evidence for one paper the turn can cite from — the
    selected paper and every open reader tab always get this. Prefers the
    extractive card's validated quotes; falls back to a bounded excerpt of
    the parsed text when extraction has not produced one yet. Band 1: never
    evicted."""
    card = await papers.get_paper_card(session, paper_id)
    if card:
        lines = [f'Extracted fields for "{title}" (validated verbatim quotes — cite these exactly to make a claim about this paper):']
        for field in card:
            heading = f" (§{field.section_heading})" if field.section_heading else ""
            lines.append(f'- {field.field_key}{heading}: "{field.value}"')
        return "\n".join(lines)

    content = await papers.get_paper_content(session, paper_id)
    if content is not None and content.full_text:
        excerpt = content.full_text[:_PAPER_EXCERPT_CHARS]
        return f'Excerpt from "{title}" (no extracted fields yet — quote verbatim from this excerpt to cite):\n{excerpt}'

    return f'No extracted text is available yet for "{title}".'


def format_open_papers(open_papers_: list[tuple[uuid.UUID, str]]) -> str:
    lines = ["Papers currently open in the reader (the read set for cross-paper comparison):"]
    for paper_id, title in open_papers_:
        lines.append(f"- {title} (paper_id {paper_id})")
    lines.append(
        "A claim comparing two papers must cite a verbatim quote from each of them. If asked to "
        "compare against a paper that is not in this list, say explicitly that the paper is not "
        "open in this project rather than answering from training knowledge."
    )
    return "\n".join(lines)


async def evidence_for(session, ui_state: UIState) -> tuple[list[tuple[uuid.UUID, str]], list[str], list[uuid.UUID]]:
    """The selected paper plus every open reader tab — the candidate set a
    `<cite>` span is checked against (US4 cross-paper compare), and the set
    each `paper_evidence` block is built for. De-duplicated, order
    preserved, selected paper first. Called once at turn start and again
    whenever the mid-turn UI merge (§3.2) finds the open-paper set changed.

    Returns `(open_papers, evidence_blocks_text, citation_paper_ids)`.
    """
    open_papers_ = await open_papers(session, ui_state.open_paper_ids)
    evidence_papers: list[tuple[uuid.UUID, str]] = []
    if ui_state.selection is not None:
        selected_paper = await papers.get_paper(session, ui_state.selection.paper_id)
        if selected_paper is not None:
            evidence_papers.append((selected_paper.id, selected_paper.title))
    for pid, title in open_papers_:
        if pid not in {p[0] for p in evidence_papers}:
            evidence_papers.append((pid, title))
    evidence_blocks_text = [await paper_evidence(session, pid, title) for pid, title in evidence_papers]
    return open_papers_, evidence_blocks_text, [pid for pid, _ in evidence_papers]


def format_ui_state(ui_state: UIState) -> str | None:
    """The live-UI-state line of band 1 (§3.2) — what the user is looking
    at right now, beyond just which papers are open. `None` when there is
    nothing to report (a bare session with no navigation yet), so the
    caller adds no empty block."""
    if ui_state.active_view is None and not ui_state.open_panes:
        return None
    parts = [f"The user is currently viewing: {ui_state.active_view or 'unknown'}"]
    if ui_state.active_id is not None:
        parts.append(f"(id {ui_state.active_id})")
    if ui_state.open_panes:
        parts.append(f"; open panes: {', '.join(ui_state.open_panes)}")
    return "".join(parts)


def format_working_set(items: list[Ref]) -> str | None:
    """Band 2 (§3.2) — the handles this turn can act on without inventing
    an id: whatever the frontend already had open, plus every `Ref` a tool
    result has surfaced so far this turn. `None` when empty."""
    if not items:
        return None
    lines = ["Working set — handles you can reference by id without another lookup:"]
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.kind, item.id)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f'- {item.kind} {item.id}: "{item.title}"')
    return "\n".join(lines)


class ContextBlock(NamedTuple):
    """One evictable unit of `llm_messages`. `band` follows §3.2's table:
    1 is never evicted; higher evicts first. Eviction removes a block
    whole — never truncates a message — so band 3 (history) is passed as
    one block per turn's worth of messages, oldest first, so a partial
    evict is possible within it."""

    name: str
    band: Literal[1, 2, 3, 4]
    message: Message


def build_blocks(
    ui_state: UIState,
    evidence_blocks_text: list[str],
    open_papers_: list[tuple[uuid.UUID, str]],
    history: list[Message],
    working_set_refs: list[Ref],
    message: str,
    skill_index_text: str,
    active_skill: SkillSpec | None = None,
) -> list[ContextBlock]:
    """The full block list for one loop iteration, in band order per §3.2's
    table — band 1 (system prompt, skill index, active skill body, selection,
    paper evidence, open-papers note, live UI state, current message) never
    evicts; band 2 (working set) evicts second; band 3 (history, oldest
    first) evicts first. Called fresh each iteration so a mid-turn
    `ui_state` change, a tool result's new `Ref`s, or a `load_skill` call are
    all reflected without re-reading anything that has not changed.

    `skill_index_text` (HarnessPlan H8, §3.6) is always present — the
    model's only way to discover a skill exists, since there is no
    classifier picking one for it. `active_skill`, when a `load_skill` call
    has landed this turn, adds its body as a second, never-evicted band-1
    block — "system is more salient to a small model" (§3.6) than a tool
    message, hence a block here rather than something folded into
    `turn_exchange`."""
    blocks = [ContextBlock("system_prompt", 1, Message(role="system", content=SYSTEM_PROMPT))]
    blocks.append(ContextBlock("skill_index", 1, Message(role="system", content=skill_index_text)))
    if active_skill is not None:
        blocks.append(ContextBlock("active_skill", 1, Message(role="system", content=active_skill.body)))
    if ui_state.selection is not None:
        blocks.append(
            ContextBlock(
                "selection",
                1,
                Message(role="system", content=f'The user has highlighted this passage from the paper:\n"{ui_state.selection.anchor.quote}"'),
            )
        )
    if evidence_blocks_text:
        blocks.append(ContextBlock("paper_evidence", 1, Message(role="system", content="\n\n".join(evidence_blocks_text))))
    if len(open_papers_) > 1:
        blocks.append(ContextBlock("open_papers", 1, Message(role="system", content=format_open_papers(open_papers_))))
    ui_state_text = format_ui_state(ui_state)
    if ui_state_text is not None:
        blocks.append(ContextBlock("ui_state", 1, Message(role="system", content=ui_state_text)))
    working_set_text = format_working_set(working_set_refs)
    if working_set_text is not None:
        blocks.append(ContextBlock("working_set", 2, Message(role="system", content=working_set_text)))
    for i, msg in enumerate(history):
        blocks.append(ContextBlock(f"history:{i}", 3, msg))
    blocks.append(ContextBlock("current_message", 1, Message(role="user", content=message)))
    return blocks


def assemble(blocks: list[ContextBlock], *, budget: int = CONTEXT_BUDGET_TOKENS) -> tuple[list[Message], dict[str, int]]:
    """Fits `blocks` inside `budget`, evicting whole blocks in band order —
    every band-3 block before any band-2 block — oldest first within a
    band, matching the order `blocks` was given in. Band-1 blocks are never
    evicted; if they alone exceed
    `budget`, the assembled context is still returned over budget and
    `llm.complete`'s `_fit_to_budget` is the last-resort guard (logged when
    it fires — this is meant to never happen).

    Returns the surviving messages in their original order, plus a
    `{block_name: tokens}` map of what actually made it in — `turn_traces`'s
    `context_blocks` column (§3.10), the bloat signal.
    """
    kept = list(blocks)
    while count_tokens([b.message for b in kept]) > budget:
        evictable = [b for b in kept if b.band > 1]
        if not evictable:
            break
        highest_band = max(b.band for b in evictable)
        victim = next(b for b in evictable if b.band == highest_band)
        kept.remove(victim)

    order = {id(b): i for i, b in enumerate(blocks)}
    kept.sort(key=lambda b: order[id(b)])
    totals: dict[str, int] = {}
    for block in kept:
        totals[block.name] = totals.get(block.name, 0) + count_tokens([block.message])
    return [b.message for b in kept], totals
