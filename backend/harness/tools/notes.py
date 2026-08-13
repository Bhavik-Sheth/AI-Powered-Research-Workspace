"""Mutations group (D19) — saving a note into the vault, and highlights."""

import uuid

from pydantic import BaseModel, Field

from db.models import Notes
from harness.models import Ref, ToolResult
from harness.registry import ToolContext, tool
from papers import get_paper as _get_paper
from provenance import validate_and_anchor as _validate_and_anchor
from vault import write_highlight as _write_highlight
from vault import write_note as _write_note
from vault.models import NoteInput


class SaveNoteArgs(BaseModel):
    title: str = "Untitled"
    body: str = ""


@tool(name="save_note", group="notes", kind="action", per_turn_budget=3)
async def save_note(ctx: ToolContext, args: SaveNoteArgs) -> ToolResult:
    """Save a note to this project and open it."""
    note = await _write_note(ctx.session, ctx.project_id, NoteInput(title=args.title, body=args.body))
    return ToolResult(
        model_view=f'Saved note "{note.title}".',
        refs=[Ref(kind="note", id=str(note.id), title=note.title)],
        ui_actions=[{"action": "open_note", "note_id": str(note.id), "title": note.title}],
    )


class CreateHighlightArgs(BaseModel):
    paper_id: str = Field(description="The paper's UUID")
    quote: str = Field(description="Exact verbatim text to highlight, copied from the paper")
    prefix: str = Field(default="", description="A few words immediately before the quote, for disambiguation")
    suffix: str = Field(default="", description="A few words immediately after the quote, for disambiguation")
    comment: str | None = None
    color: str | None = None


@tool(name="create_highlight", group="notes", kind="action", core=False)
async def create_highlight(ctx: ToolContext, args: CreateHighlightArgs) -> ToolResult:
    """Highlight a verbatim quote from a paper and save it to this project, with an optional comment."""
    try:
        paper_id = uuid.UUID(args.paper_id)
    except ValueError:
        return ToolResult(model_view="That is not a valid paper id.")
    anchor = await _validate_and_anchor(ctx.session, paper_id, args.quote, args.prefix, args.suffix)
    if anchor is None:
        return ToolResult(model_view="That quote could not be found verbatim in the paper's text, so no highlight was created.")
    try:
        highlight = await _write_highlight(
            ctx.session,
            project_id=ctx.project_id,
            paper_id=paper_id,
            anchor_id=anchor.id,
            quote=anchor.quote,
            prefix=anchor.prefix,
            suffix=anchor.suffix,
            section_heading=anchor.section_heading,
            char_start=anchor.char_start,
            char_end=anchor.char_end,
            comment=args.comment,
            color=args.color,
        )
    except ValueError:
        return ToolResult(model_view="That paper is not in this project.")
    paper = await _get_paper(ctx.session, paper_id)
    title = paper.title if paper else str(paper_id)
    return ToolResult(
        model_view=f'Highlighted: "{highlight.quote}"',
        refs=[Ref(kind="paper", id=str(paper_id), title=title)],
        ui_actions=[{"action": "scroll_to", "paper_id": str(paper_id), "anchor_id": str(highlight.anchor_id)}],
    )


class UpdateNoteArgs(BaseModel):
    note_id: str = Field(description="The note's UUID (frontmatter id)")
    title: str | None = None
    body: str | None = None


@tool(name="update_note", group="notes", kind="action", core=False)
async def update_note(ctx: ToolContext, args: UpdateNoteArgs) -> ToolResult:
    """Patch an existing note's title and/or body, leaving any field not given unchanged."""
    try:
        note_id = uuid.UUID(args.note_id)
    except ValueError:
        return ToolResult(model_view="That is not a valid note id.")
    existing = await ctx.session.get(Notes, note_id)
    if existing is None or existing.project_id != ctx.project_id:
        return ToolResult(model_view="That note could not be found in this project.")
    note = await _write_note(
        ctx.session,
        ctx.project_id,
        NoteInput(
            frontmatter_id=note_id,
            title=args.title if args.title is not None else existing.title,
            body=args.body if args.body is not None else existing.body,
        ),
    )
    return ToolResult(
        model_view=f'Updated note "{note.title}".',
        refs=[Ref(kind="note", id=str(note.id), title=note.title)],
        ui_actions=[{"action": "open_note", "note_id": str(note.id), "title": note.title}],
    )
