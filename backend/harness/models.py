"""Wire/value shapes for Agent Harness (Rules.md: names match the wire shape).

`UIState` ships with only `selection` in Phase 1.5 — `activeTab` / `openPanes`
/ `workingSet` (TRD §4.1) remain unshipped; Phase 3.1 adds `open_paper_ids`
(the reader tabs currently open), the minimum slice of `openTabs` the
cross-paper compare tool needs to know a paper is "in the read set" (US4).
"""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from vault.models import QuoteAnchorInput


class SelectionState(BaseModel):
    paper_id: uuid.UUID
    anchor: QuoteAnchorInput


class UIState(BaseModel):
    selection: SelectionState | None = None
    open_paper_ids: list[uuid.UUID] = Field(default_factory=list)


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


class ErrorEvent(BaseModel):
    event: Literal["error"] = "error"
    code: str
    message: str
    recoverable: bool
    what_still_worked: str | None = None


TurnEvent = StatusEvent | TextDeltaEvent | ToolCallEvent | ToolResultEvent | UIActionEvent | TurnCompleteEvent | ErrorEvent
