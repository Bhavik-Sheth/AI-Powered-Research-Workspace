"""Mutations group (D19) — adding a paper to the project library, and papers.py-scoped queries."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

import projects
from harness.models import Ref, ToolResult
from harness.registry import ToolContext, tool
from papers import add_paper as _add_paper
from papers import get_paper as _get_paper
from papers import get_paper_card as _get_paper_card
from papers.models import PaperInput, SourceIds
from projects import set_paper_relevance as _set_paper_relevance


class AddPaperArgs(BaseModel):
    arxiv_id: str | None = None
    doi: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None
    title: str | None = None  # used only if the paper cannot be resolved from an id


@tool(name="add_paper", group="papers", kind="action", per_turn_budget=5)
async def add_paper(ctx: ToolContext, args: AddPaperArgs) -> ToolResult:
    """Add a paper to this project's library, by arXiv id, DOI, OpenAlex id, or Semantic Scholar id, and open it in the reader."""
    source_ids = SourceIds(doi=args.doi, arxiv_id=args.arxiv_id, openalex_id=args.openalex_id, s2_id=args.s2_id)
    try:
        paper = await _add_paper(ctx.session, PaperInput(source_ids=source_ids, title=args.title))
    except ValueError:
        return ToolResult(model_view="No paper identifier (arXiv id, DOI, OpenAlex id, or S2 id) was given.")
    await projects.add_paper_to_project(ctx.session, ctx.project_id, paper.id)
    return ToolResult(
        model_view=f'Added "{paper.title}" to the project library.',
        refs=[Ref(kind="paper", id=str(paper.id), title=paper.title)],
        ui_actions=[{"action": "open_paper", "paper_id": str(paper.id), "title": paper.title}],
    )


class GetPaperArgs(BaseModel):
    paper_id: str = Field(description="The paper's UUID")


@tool(name="get_paper", group="papers", kind="query", core=True)
async def get_paper(ctx: ToolContext, args: GetPaperArgs) -> ToolResult:
    """Fetch one paper's title, abstract, and extracted card fields by id, without opening it in the UI."""
    try:
        paper_id = uuid.UUID(args.paper_id)
    except ValueError:
        return ToolResult(model_view="That is not a valid paper id.")
    paper = await _get_paper(ctx.session, paper_id)
    if paper is None:
        return ToolResult(model_view="That paper could not be found.")
    card = await _get_paper_card(ctx.session, paper_id)
    lines = [f'"{paper.title}"']
    if paper.abstract:
        lines.append(f"Abstract: {paper.abstract}")
    if card:
        lines.append("Extracted fields (validated verbatim quotes — cite these exactly to make a claim):")
        for field in card:
            heading = f" (§{field.section_heading})" if field.section_heading else ""
            lines.append(f'- {field.field_key}{heading}: "{field.value}"')
    else:
        lines.append("No extracted fields yet.")
    return ToolResult(model_view="\n".join(lines), refs=[Ref(kind="paper", id=str(paper.id), title=paper.title)])


class MarkRelevantArgs(BaseModel):
    paper_id: str = Field(description="The paper's UUID")
    relevance: Literal["relevant", "somewhat", "not", "unset"]
    why_relevant: str | None = None


@tool(name="mark_relevant", group="papers", kind="action", core=False)
async def mark_relevant(ctx: ToolContext, args: MarkRelevantArgs) -> ToolResult:
    """Set how relevant a paper in this project's library is to the research question — relevant, somewhat, not, or unset — with an optional reason."""
    try:
        paper_id = uuid.UUID(args.paper_id)
    except ValueError:
        return ToolResult(model_view="That is not a valid paper id.")
    row = await _set_paper_relevance(ctx.session, ctx.project_id, paper_id, args.relevance, args.why_relevant)
    if row is None:
        return ToolResult(model_view="That paper is not in this project's library.")
    paper = await _get_paper(ctx.session, paper_id)
    title = paper.title if paper else str(paper_id)
    return ToolResult(
        model_view=f'Marked "{title}" as {row.relevance}.',
        refs=[Ref(kind="paper", id=str(paper_id), title=title)],
        ui_actions=[{"action": "mark_relevant", "paper_id": str(paper_id), "relevance": row.relevance}],
    )
