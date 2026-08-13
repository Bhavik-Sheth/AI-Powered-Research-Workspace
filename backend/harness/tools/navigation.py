"""Navigation group (D19) — moving the UI to something already in the library."""

import uuid

from pydantic import BaseModel, Field

from harness.models import Ref, ToolResult
from harness.registry import ToolContext, tool
from papers import get_paper as _get_paper


class OpenPaperArgs(BaseModel):
    paper_id: str = Field(description="The paper's UUID")


@tool(name="open_paper", group="navigation", kind="query")
async def open_paper(ctx: ToolContext, args: OpenPaperArgs) -> ToolResult:
    """Open a paper already in this project's library in the reader, by its paper id."""
    try:
        paper_id = uuid.UUID(args.paper_id)
    except ValueError:
        return ToolResult(model_view="That is not a valid paper id.")
    paper = await _get_paper(ctx.session, paper_id)
    if paper is None:
        return ToolResult(model_view="That paper could not be found in this project's library.")
    return ToolResult(
        model_view=f'Opened "{paper.title}".',
        refs=[Ref(kind="paper", id=str(paper.id), title=paper.title)],
        ui_actions=[{"action": "open_paper", "paper_id": str(paper.id), "title": paper.title}],
    )
