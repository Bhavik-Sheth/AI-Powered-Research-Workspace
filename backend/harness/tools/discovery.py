"""Discovery group (D19) — finding papers outside the project's library."""

from pydantic import BaseModel, Field

import search
from harness.models import Ref, ToolResult
from harness.registry import ToolContext, tool


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
