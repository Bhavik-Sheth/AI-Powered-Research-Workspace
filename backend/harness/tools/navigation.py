"""Navigation group (D19) — moving the UI to something already in the library."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from harness.models import Ref, ToolResult
from harness.registry import ToolContext, tool
from papers import get_paper as _get_paper
from papers import get_paper_content as _get_paper_content
from provenance import get_anchor as _get_anchor
from provenance import locate as _locate


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


class OpenReferenceArgs(BaseModel):
    paper_id: str = Field(description="The paper whose reference list to look in")
    ref_id: str = Field(description="The reference's id within that paper's reference list")


@tool(name="open_reference", group="navigation", kind="query", core=False)
async def open_reference(ctx: ToolContext, args: OpenReferenceArgs) -> ToolResult:
    """Resolve a paper's reference/citation to the paper it points to, and open that paper in the reader if it is in the library."""
    try:
        paper_id = uuid.UUID(args.paper_id)
    except ValueError:
        return ToolResult(model_view="That is not a valid paper id.")
    content = await _get_paper_content(ctx.session, paper_id)
    if content is None:
        return ToolResult(model_view="This paper has not been parsed yet, so its references are not available.")
    reference = next((r for r in content.references if r.ref_id == args.ref_id), None)
    if reference is None:
        return ToolResult(model_view=f'No reference "{args.ref_id}" was found in this paper.')
    if reference.paper_id is None:
        return ToolResult(model_view=f'Reference "{reference.title or reference.raw}" has not been resolved to a paper in the library yet.')
    target = await _get_paper(ctx.session, reference.paper_id)
    if target is None:
        return ToolResult(model_view="The resolved paper could not be found.")
    return ToolResult(
        model_view=f'Opened "{target.title}".',
        refs=[Ref(kind="paper", id=str(target.id), title=target.title)],
        ui_actions=[{"action": "open_paper", "paper_id": str(target.id), "title": target.title}],
    )


class ScrollToArgs(BaseModel):
    anchor_id: str = Field(description="The quote anchor's UUID to scroll the reader to")


@tool(name="scroll_to", group="navigation", kind="query", core=False)
async def scroll_to(ctx: ToolContext, args: ScrollToArgs) -> ToolResult:
    """Scroll the reader to a specific quote anchor already validated in a paper's text."""
    try:
        anchor_id = uuid.UUID(args.anchor_id)
    except ValueError:
        return ToolResult(model_view="That is not a valid anchor id.")
    anchor = await _get_anchor(ctx.session, anchor_id)
    if anchor is None:
        return ToolResult(model_view="That anchor could not be found.")
    return ToolResult(
        model_view=f'Scrolled to "{anchor.quote}".',
        ui_actions=[{"action": "scroll_to", "paper_id": str(anchor.paper_id), "anchor_id": str(anchor.id)}],
    )


class HighlightSpanArgs(BaseModel):
    paper_id: str = Field(description="The paper's UUID")
    quote: str = Field(description="Exact verbatim text to momentarily highlight in the reader")


@tool(name="highlight_span", group="navigation", kind="query", core=False)
async def highlight_span(ctx: ToolContext, args: HighlightSpanArgs) -> ToolResult:
    """Momentarily highlight a verbatim span in the reader, without saving a persistent highlight."""
    try:
        paper_id = uuid.UUID(args.paper_id)
    except ValueError:
        return ToolResult(model_view="That is not a valid paper id.")
    content = await _get_paper_content(ctx.session, paper_id)
    if content is None or not content.full_text:
        return ToolResult(model_view="This paper's text is not available yet.")
    span = _locate(args.quote, content.full_text)
    if span is None:
        return ToolResult(model_view="That quote could not be found verbatim in the paper's text.")
    return ToolResult(
        model_view=f'Highlighted "{args.quote}" in the reader.',
        ui_actions=[{"action": "highlight_span", "paper_id": str(paper_id), "char_start": span.start, "char_end": span.end}],
    )


class OpenViewArgs(BaseModel):
    view: Literal["library", "reader", "notes", "matrix", "graph", "feed", "experiments"] = Field(
        description="Which view/tab to switch to"
    )


@tool(name="open_view", group="navigation", kind="query", core=True)
async def open_view(ctx: ToolContext, args: OpenViewArgs) -> ToolResult:
    """Switch the active view/tab — library, reader, notes, matrix, graph, feed, or experiments."""
    return ToolResult(model_view=f"Switched to the {args.view} view.", ui_actions=[{"action": "open_view", "view": args.view}])
