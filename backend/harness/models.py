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


TurnEvent = StatusEvent | TextDeltaEvent | TurnCompleteEvent | ErrorEvent
