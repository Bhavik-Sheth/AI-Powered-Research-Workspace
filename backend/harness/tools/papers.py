"""Mutations group (D19) — adding a paper to the project library."""

from pydantic import BaseModel

import projects
from harness.models import Ref, ToolResult
from harness.registry import ToolContext, tool
from papers import add_paper as _add_paper
from papers.models import PaperInput, SourceIds


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
