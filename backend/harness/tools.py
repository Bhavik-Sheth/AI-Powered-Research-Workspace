"""Agent Harness's tool catalog and dispatch (D19) — internal to the
package, not a separate module (MODULES.md: "harness/'s internal files ...
are not separate modules").

Bug Fix Plan Phase 2.3's minimum viable slice of D19: enough of the
Discovery / Navigation / Mutations groups to make "open a paper, run a
search, or add a note" real tool calls rather than unreachable capability.
Later slices grow this catalog; the dispatch shape does not change.
"""

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import papers
import projects
import search
import vault
from papers.models import PaperInput, SourceIds
from vault.models import NoteInput


class ToolResult(BaseModel):
    """D18 node 3's dual-channel result: `model_view` is what re-enters the
    next completion call; `ui_view_result_id`, when set, is a `result_store`
    handle the frontend fetches lazily by id — the rich payload never enters
    LLM context. `ui_actions` are the UI commands D19's Navigation tools
    emit — a route transition, the same one the user's own click would
    produce."""

    model_view: str
    ui_view_result_id: str | None = None
    ui_actions: list[dict[str, Any]] = []


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "Search the academic literature for papers matching a query, and open the results in the UI.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search terms"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_paper",
            "description": "Add a paper to this project's library, by arXiv id, DOI, OpenAlex id, or Semantic Scholar id, and open it in the reader.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arxiv_id": {"type": "string"},
                    "doi": {"type": "string"},
                    "openalex_id": {"type": "string"},
                    "s2_id": {"type": "string"},
                    "title": {"type": "string", "description": "Used only if the paper cannot be resolved from an id"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_paper",
            "description": "Open a paper already in this project's library in the reader, by its paper id.",
            "parameters": {
                "type": "object",
                "properties": {"paper_id": {"type": "string", "description": "The paper's UUID"}},
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Save a note to this project and open it.",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
                "required": ["title", "body"],
            },
        },
    },
]


async def dispatch(session: AsyncSession, project_id: uuid.UUID, tool_name: str, args: dict) -> ToolResult:
    """Every tool call the harness's loop hands off passes through here —
    the tool catalog's one implementation."""
    if tool_name == "search_papers":
        result_set = await search.search_papers(args.get("query", ""))
        titles = "; ".join(hit.title for hit in result_set.results[:5])
        summary = f"Found {len(result_set.results)} paper(s)" + (f": {titles}" if titles else "")
        return ToolResult(
            model_view=summary,
            ui_view_result_id=result_set.result_id,
            ui_actions=[{"action": "open_search_results", "result_id": result_set.result_id}],
        )

    if tool_name == "add_paper":
        source_ids = SourceIds(
            doi=args.get("doi"), arxiv_id=args.get("arxiv_id"), openalex_id=args.get("openalex_id"), s2_id=args.get("s2_id")
        )
        try:
            paper = await papers.add_paper(session, PaperInput(source_ids=source_ids, title=args.get("title")))
        except ValueError:
            return ToolResult(model_view="No paper identifier (arXiv id, DOI, OpenAlex id, or S2 id) was given.")
        await projects.add_paper_to_project(session, project_id, paper.id)
        return ToolResult(
            model_view=f'Added "{paper.title}" to the project library.',
            ui_actions=[{"action": "open_paper", "paper_id": str(paper.id), "title": paper.title}],
        )

    if tool_name == "open_paper":
        try:
            paper_id = uuid.UUID(args.get("paper_id", ""))
        except ValueError:
            return ToolResult(model_view="That is not a valid paper id.")
        paper = await papers.get_paper(session, paper_id)
        if paper is None:
            return ToolResult(model_view="That paper could not be found in this project's library.")
        return ToolResult(
            model_view=f'Opened "{paper.title}".',
            ui_actions=[{"action": "open_paper", "paper_id": str(paper.id), "title": paper.title}],
        )

    if tool_name == "save_note":
        note = await vault.write_note(session, project_id, NoteInput(title=args.get("title", "Untitled"), body=args.get("body", "")))
        return ToolResult(
            model_view=f'Saved note "{note.title}".',
            ui_actions=[{"action": "open_note", "note_id": str(note.id), "title": note.title}],
        )

    return ToolResult(model_view=f"Unknown tool: {tool_name}")
