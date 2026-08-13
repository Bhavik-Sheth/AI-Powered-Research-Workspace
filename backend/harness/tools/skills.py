"""Skills group (D19/H8) — `load_skill`, the tool the model uses to pull a
playbook's body into context and its declared tools into this turn's
visible schema list (§3.6). Its own dispatch is a plain lookup against
`harness.skills.load_index()`; the effect this tool exists for — a system
message in band 1, extra tool schemas — is not something a `ToolResult` can
carry to the model, so it goes through `ToolContext.loaded_skill_slot`
instead (see `registry.ToolContext`'s docstring) and `loop.py` reads it back
after dispatch."""

from pydantic import BaseModel, Field

from harness import skills
from harness.models import ToolResult
from harness.registry import ToolContext, tool


class LoadSkillArgs(BaseModel):
    name: str = Field(description="A skill's name, exactly as it appears in the skill index")


@tool(name="load_skill", group="skills", kind="query", core=True)
async def load_skill(ctx: ToolContext, args: LoadSkillArgs) -> ToolResult:
    """Load a skill's step-by-step instructions for this turn, given its name from the skill index."""
    index = skills.load_index()
    spec = index.get(args.name)
    if spec is None:
        known = ", ".join(sorted(index)) or "none"
        return ToolResult(model_view=f'Unknown skill "{args.name}". Available skills: {known}.')
    # Replaces the slot's contents rather than appending — "one skill at a
    # time; loading a second replaces the first" (§3.6). `loop.py` shares
    # this same list across every `ToolContext` built this turn, so it reads
    # back whichever skill was loaded most recently.
    ctx.loaded_skill_slot[:] = [spec]
    return ToolResult(model_view=f'Loaded skill "{spec.name}". Follow its instructions for the rest of this turn.')
