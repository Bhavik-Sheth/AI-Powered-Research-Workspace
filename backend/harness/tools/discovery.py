"""Discovery group (D19) — finding papers outside the project's library."""

import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

import search
from db.models import ResultStore
from harness.models import Ref, ToolResult
from harness.registry import ToolContext, tool
from search.models import SearchFilters


class SearchPapersArgs(BaseModel):
    query: str = Field(description="Search terms")


@tool(name="search_papers", group="discovery", kind="query", per_turn_budget=6)
async def search_papers(ctx: ToolContext, args: SearchPapersArgs) -> ToolResult:
    """Search the academic literature for papers matching a query, and open the results in the UI."""
    result_set = await search.search_papers(args.query)
    titles = "; ".join(hit.title for hit in result_set.results[:5])
    summary = f"Found {len(result_set.results)} paper(s)" + (f": {titles}" if titles else "")
    return ToolResult(
        model_view=summary,
        ui_view_result_id=result_set.result_id,
        refs=[Ref(kind="search_result", id=hit.canonical_id, title=hit.title) for hit in result_set.results[:5]],
        ui_actions=[{"action": "open_search_results", "result_id": result_set.result_id}],
    )


# Mirrors search/__init__.py's own TTL for a result_store row — not reused
# directly (that constant is private to search/), and a second one this
# small does not clear the rule-of-three bar for extracting a shared
# constant yet.
_RESULT_TTL = timedelta(hours=1)


class CompareArgs(BaseModel):
    paper_ids: list[str] = Field(description="Two or more paper UUIDs to compare, already in this project's library")


@tool(name="compare", group="discovery", kind="query", core=False)
async def compare(ctx: ToolContext, args: CompareArgs) -> ToolResult:
    """Fetch two or more papers' abstracts and extracted card fields side by side, for the model to compare — this tool does not judge which is better, it only gathers the evidence."""
    import papers as papers_module

    parsed_ids: list[uuid.UUID] = []
    for raw in args.paper_ids:
        try:
            parsed_ids.append(uuid.UUID(raw))
        except ValueError:
            return ToolResult(model_view=f'"{raw}" is not a valid paper id.')
    if len(parsed_ids) < 2:
        return ToolResult(model_view="Give at least two paper ids to compare.")

    entries = []
    refs: list[Ref] = []
    for paper_id in parsed_ids:
        paper = await papers_module.get_paper(ctx.session, paper_id)
        if paper is None:
            return ToolResult(model_view=f"Paper {paper_id} could not be found.")
        card = await papers_module.get_paper_card(ctx.session, paper_id)
        entries.append(
            {
                "paper_id": str(paper.id),
                "title": paper.title,
                "abstract": paper.abstract,
                "card": [{"field_key": f.field_key, "value": f.value, "section_heading": f.section_heading} for f in card],
            }
        )
        refs.append(Ref(kind="paper", id=str(paper.id), title=paper.title))

    result_id = str(uuid.uuid4())
    ctx.session.add(
        ResultStore(
            result_id=result_id,
            project_id=ctx.project_id,
            tool_name="compare",
            ui_view={"papers": entries},
            model_view=f"Comparing {len(entries)} papers.",
            expires_at=datetime.now(UTC) + _RESULT_TTL,
        )
    )
    titles = "; ".join(e["title"] for e in entries)
    return ToolResult(
        model_view=f"Gathered evidence for comparing: {titles}. Cite a verbatim quote from each paper's abstract or extracted fields for any claim.",
        ui_view_result_id=result_id,
        refs=refs,
    )


class RefineResultsArgs(BaseModel):
    result_id: str = Field(description="The result_id of a previous search_papers call")
    year_min: int | None = None
    year_max: int | None = None
    venue: str | None = None
    has_code: bool | None = None
    author: str | None = None


@tool(name="refine_results", group="discovery", kind="query", core=False)
async def refine_results(ctx: ToolContext, args: RefineResultsArgs) -> ToolResult:
    """Narrow a previous search_papers result set by year, venue, code availability, or author, without issuing a new search."""
    filters = SearchFilters(year_min=args.year_min, year_max=args.year_max, venue=args.venue, has_code=args.has_code, author=args.author)
    try:
        result_set = await search.refine_results(args.result_id, filters)
    except ValueError:
        return ToolResult(model_view=f'No cached search result "{args.result_id}" was found — run search_papers again first.')
    titles = "; ".join(hit.title for hit in result_set.results[:5])
    summary = f"{len(result_set.results)} paper(s) match the refined filters" + (f": {titles}" if titles else "")
    return ToolResult(
        model_view=summary,
        ui_view_result_id=result_set.result_id,
        refs=[Ref(kind="search_result", id=hit.canonical_id, title=hit.title) for hit in result_set.results[:5]],
        ui_actions=[{"action": "open_search_results", "result_id": result_set.result_id}],
    )
