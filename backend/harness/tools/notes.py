"""Mutations group (D19) — saving a note into the vault."""

from pydantic import BaseModel

from harness.models import Ref, ToolResult
from harness.registry import ToolContext, tool
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
