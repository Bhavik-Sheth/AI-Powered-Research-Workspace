"""Wire/value shapes for Agent Harness (Rules.md: names match the wire shape).

`UIState` shipped with only `selection` in Phase 1.5; Phase 3.1 added
`open_paper_ids` (the reader tabs currently open), the minimum slice of
`openTabs` the cross-paper compare tool needs to know a paper is "in the
read set" (US4). HarnessPlan H3 fills in the rest of TRD Node 2's Zustand
snapshot — `active_view`/`active_id`/`open_panes`/`working_set` — so context
assembly's band 1 (§3.2) can describe what the user is actually looking at,
not just which papers are open.
"""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from vault.models import QuoteAnchorInput


class SelectionState(BaseModel):
    paper_id: uuid.UUID
    anchor: QuoteAnchorInput


class Ref(BaseModel):
    """A stable handle to something a tool result named — `kind` + `id` +
    `title` (HarnessPlan H1, §3.4). This is the mechanism that stops a
    hallucinated UUID: a tool that names a domain object hands back a
    handle instead of the model reconstructing an id from memory later, and
    the working set (H3) is built from these plus whatever the frontend
    already had in `UIState.working_set` — one shape for both, since a
    working-set entry is exactly a ref the UI already holds."""

    kind: Literal["paper", "note", "experiment", "search_result"]
    id: str
    title: str


class UIState(BaseModel):
    selection: SelectionState | None = None
    open_paper_ids: list[uuid.UUID] = Field(default_factory=list)
    active_view: Literal["library", "reader", "notes", "matrix", "graph", "feed", "experiments"] | None = None
    active_id: uuid.UUID | None = None
    open_panes: list[str] = Field(default_factory=list)
    working_set: list[Ref] = Field(default_factory=list)


class SkillSpec(BaseModel):
    """A parsed `harness/skills/*.md` playbook (HarnessPlan H8, §3.6) — the
    YAML frontmatter's `name`/`description`/`tools` plus the Markdown body
    that follows it. Lives here, not in `harness/skills.py` itself, so
    `registry.py`'s `ToolContext` can hold one (the single-slot mechanism
    §3.6's `load_skill` tool uses to hand the active skill back to
    `loop.py`) without `registry.py` importing `harness/skills.py` — which
    itself imports `registry.get` to validate a skill's `tools:` list at
    load time, and two modules importing each other is a cycle neither of
    them needs."""

    name: str
    description: str
    tools: list[str] = Field(default_factory=list)
    body: str


class ToolResult(BaseModel):
    """D18 node 3's dual-channel result: `model_view` is the only field that
    ever enters LLM context; `ui_view_result_id`, when set, is a
    `result_store` handle the frontend fetches lazily — the rich payload
    never enters LLM context. `refs` names every domain object this result
    surfaced, and `ui_actions` are the UI commands D19's Navigation tools
    emit — the same route transition the user's own click would produce."""

    model_view: str
    ui_view_result_id: str | None = None
    refs: list[Ref] = Field(default_factory=list)
    ui_actions: list[dict] = Field(default_factory=list)


class AnchorCitation(BaseModel):
    """A `<cite>` span the substring validator resolved against a paper's
    parsed text (D24) — `anchor_id` is a `quote_anchors` row, fetched via
    `GET /api/anchors/:id` to drive `scroll_to` + `highlight_span` in the
    reader (Phase 6.1)."""

    kind: Literal["anchor"] = "anchor"
    anchor_id: uuid.UUID
    quote: str


class MemoryCitation(BaseModel):
    """A `<cite>` span resolved against a `query_memory` row instead of a
    live paper anchor — no reader position to scroll to, so this kind is
    rendered but not clickable."""

    kind: Literal["memory"] = "memory"
    row_id: uuid.UUID
    source_type: str
    quote: str


Citation = AnchorCitation | MemoryCitation


class SessionRef(BaseModel):
    """An opaque handle Session Transport passes in — the harness knows
    nothing about WebSocket framing (MODULES.md: "must not know transport
    details")."""

    project_id: uuid.UUID
    conversation_id: uuid.UUID


class StatusEvent(BaseModel):
    event: Literal["status"] = "status"
    text: str


class TextDeltaEvent(BaseModel):
    event: Literal["text_delta"] = "text_delta"
    delta: str


class ToolCallEvent(BaseModel):
    """D18 node 5's `tool_call` down-event — the model invoking a tool,
    before its result is known."""

    event: Literal["tool_call"] = "tool_call"
    tool_name: str
    args: dict


class ToolResultEvent(BaseModel):
    """D18 node 5's `tool_result(ref)` — `model_view` is the same text fed
    back into the loop; `result_id`, when present, is what the frontend
    fetches `ui_view` by (`GET /api/results/:resultId`), never inlined."""

    event: Literal["tool_result"] = "tool_result"
    tool_name: str
    model_view: str
    result_id: str | None = None


class UIActionEvent(BaseModel):
    """A UI command a tool emitted (D19 Navigation) — the same route
    transition the user's own click would produce."""

    event: Literal["ui_action"] = "ui_action"
    action: str
    payload: dict


class TurnCompleteEvent(BaseModel):
    event: Literal["turn_complete"] = "turn_complete"
    turn_id: uuid.UUID
    interrupted: bool
    iterations: int
    # Mirrors `messages.citations` for the message this turn just produced —
    # without this a citation is only clickable after a reload re-fetches
    # conversation history, since no earlier event on this stream carries it
    # (Phase 6.1).
    citations: list[Citation] = Field(default_factory=list)


class ErrorEvent(BaseModel):
    event: Literal["error"] = "error"
    code: str
    message: str
    recoverable: bool
    what_still_worked: str | None = None


class ApprovalRequestEvent(BaseModel):
    """HarnessPlan H7, §3.9: emitted in place of dispatching a `tier="confirm"`
    tool call — `run_all` and any future delete-shaped or MCP tool. `summary`
    and `risk` are short, human-readable strings `loop.py` builds per call
    from the tool's own args (real values where practical, e.g. `run_all`'s
    actual container network/GPU settings — never a placeholder). The loop
    then awaits `ws`'s matching `approval_response`, raced against
    `cancel_flag` and a timeout (`approval.py`)."""

    event: Literal["approval_request"] = "approval_request"
    request_id: str
    tool_name: str
    args: dict
    summary: str
    risk: str


TurnEvent = (
    StatusEvent
    | TextDeltaEvent
    | ToolCallEvent
    | ToolResultEvent
    | UIActionEvent
    | TurnCompleteEvent
    | ErrorEvent
    | ApprovalRequestEvent
)
