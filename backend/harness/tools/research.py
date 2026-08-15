"""Discovery group (D19) — `deep_research`, HarnessPlan H9's first subagent
tool (§3.7): runs several query variants across `search_papers` and
`query_memory` in a fresh-context sub-loop, judges each candidate for
genuine relevance rather than keyword overlap, and returns the survivors up
to `max_sources`. It never calls `add_paper` itself — "the main agent or the
user then calls `add_paper`... Your library never changes without a message
explaining why" (§3.7) — `deep_research`'s own tool list holds only
`kind="query"` tools, enforced by `SubagentSpec`'s own constructor-time
validator, so this could not mutate the library even if its prompt asked it
to."""

from pydantic import BaseModel, Field

from harness.models import ToolResult
from harness.registry import ToolContext, tool
from harness.subagents import SubagentSpec, run_subagent

_SYSTEM_PROMPT = """You are a literature-search subagent. You are given one research question and \
nothing else — no prior conversation, no assumed context beyond the question itself.

Your job:
1. Come up with several distinct query variants for the question — different phrasings, synonyms, \
narrower and broader framings. A single narrow query under-covers this task.
2. Run each variant through search_papers and, separately, through query_memory (the project's own \
notes, papers, and past conversations may already hold something relevant).
3. Judge every candidate for genuine relevance — a shared problem, method, or dataset with the \
question, not just an overlapping keyword. Discard anything that only superficially matches.
4. Stop calling tools once you have enough genuinely relevant survivors (or have tried enough query \
variants that further ones are unlikely to add anything new) and write your final answer: a short \
list of the survivors, each as a title plus one clause on how it relates to the question. Cite a \
verbatim quote from the candidate's own result for that relation where you can.

You cannot add anything to the library or change any state — you only report what you found."""


class DeepResearchArgs(BaseModel):
    question: str = Field(description="The research question to investigate")
    max_sources: int = Field(default=10, description="Maximum number of relevant sources to return")


@tool(name="deep_research", group="discovery", kind="query", core=False, per_turn_budget=1)
async def deep_research(ctx: ToolContext, args: DeepResearchArgs) -> ToolResult:
    """Run a multi-query literature search subagent that judges relevance and reports the survivors — never adds anything to the library itself."""
    if ctx.turn_id is None or ctx.cancel_flag is None:
        # Defensive (HarnessPlan H9, §3.7): every real call reaches this
        # tool through `loop.py`'s tool-dispatch block, which always sets
        # both — this only fires if that wiring regresses, and a tool must
        # never crash on missing context, so it degrades to an honest
        # `model_view` instead.
        return ToolResult(model_view="deep_research is unavailable right now — no turn context to trace or interrupt this subagent against.")

    spec = SubagentSpec(name="deep_research", system_prompt=_SYSTEM_PROMPT, tools=["search_papers", "query_memory"])
    task = (
        f"Research question: {args.question}\n\n"
        f"Return at most {args.max_sources} genuinely relevant sources."
    )
    return await run_subagent(
        spec,
        task,
        parent_turn_id=ctx.turn_id,
        project_id=ctx.project_id,
        cancel_flag=ctx.cancel_flag,
        status_callback=ctx.status_callback,
    )
