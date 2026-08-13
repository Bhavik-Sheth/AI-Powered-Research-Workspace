"""Memory group (D19) — `query_memory` as a real dispatchable tool.

HarnessPlan H5, §3.5: retrieval used to be a pre-turn LLM-gated call
(`context.maybe_retrieve`, deleted) that ran once before the loop started
and could never fire again after iteration 1. `query_memory` is now a tool
the model calls whenever *it* decides it needs the project's own notes,
papers, or past conversations — as many times, at whatever point in the
loop, as the question actually requires.
"""

from pydantic import BaseModel, Field

from harness.models import Ref, ToolResult
from harness.registry import ToolContext, tool
from memory import query_memory_with_diagnostics as _query_memory_with_diagnostics
from memory.models import CitedRow


class QueryMemoryArgs(BaseModel):
    query: str = Field(description="What to search for in this project's own notes, papers, and past conversations")


def format_memory_rows(rows: list[CitedRow]) -> str:
    """The `model_view` text for a retrieval — every returned row, labelled
    by its section heading when it has one, else its source type. Moved
    here from `context.py` (H5): band 4 (a dedicated always-present
    "retrieved from memory" context block) is gone now that retrieval is
    tool-mediated — the tool's own `tool_result` message, already part of
    `turn_exchange` in `loop.py`, is the only place this text needs to
    exist."""
    lines = ["Retrieved from the project's memory:"]
    for row in rows:
        label = f"§{row.section_heading}" if row.section_heading else row.source_type
        lines.append(f'- ({label}) "{row.text}"')
    return "\n".join(lines)


def _refs_for(rows: list[CitedRow]) -> list[Ref]:
    """A `Ref` for each row whose source maps onto one of `Ref.kind`'s four
    values. `paper_section`/`abstract` rows carry a `paper_id` and become a
    `paper` ref; `note` rows become a `note` ref keyed by their own
    `source_id`. `conversation_summary` (and `experiment`, once that
    chunking lands) have no fitting `Ref` kind and are simply omitted
    rather than forced into the wrong one."""
    refs: list[Ref] = []
    for row in rows:
        if row.paper_id is not None:
            refs.append(Ref(kind="paper", id=str(row.paper_id), title=row.section_heading or row.source_type))
        elif row.source_type == "note":
            refs.append(Ref(kind="note", id=row.source_id, title=row.section_heading or "note"))
    return refs


@tool(name="query_memory", group="memory", kind="query", core=True)
async def query_memory(ctx: ToolContext, args: QueryMemoryArgs) -> ToolResult:
    """Search this project's own notes, papers, and past conversations for material relevant to a question."""
    rows, diagnostics = await _query_memory_with_diagnostics(ctx.project_id, args.query)
    # HarnessPlan H5's citation-validator design: `loop.py` passes the same
    # `memory_rows`/`retrieval_trace` lists into every `ToolContext` it
    # builds this turn, so appending here is how a row retrieved mid-loop
    # becomes something a later `<cite>` span can validate against, and how
    # `turn_traces.retrieval` gets populated — without widening `ToolResult`.
    ctx.memory_rows.extend(rows)
    ctx.retrieval_trace.append(diagnostics)
    if not rows:
        return ToolResult(model_view="No relevant material was found in this project's notes, papers, or past conversations for that query.")
    return ToolResult(model_view=format_memory_rows(rows), refs=_refs_for(rows))
