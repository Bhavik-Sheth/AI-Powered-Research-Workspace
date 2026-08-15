"""Tool registry (HarnessPlan H1, §3.4) — the `@tool` decorator, `ToolSpec`,
and dispatch. This is the keystone the audit named: before this module, the
catalog was an `if`-chain (`tools.py:131`) next to a hand-written schema
literal (`tools.py:40`) — two edits in two places for every tool, free to
drift. A tool's JSON schema is now generated once from its Pydantic
arguments model, and that same model validates every call, so schema and
validation can never disagree again.

Model-floor consequence (H1): a validation failure never raises. It returns
a `model_view` that tells the model exactly what was wrong — a correction
the loop feeds straight back into the next completion, not a crash.
"""

import asyncio
import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from harness.models import Ref, SkillSpec, ToolResult
from memory.models import CitedRow


@dataclass(frozen=True)
class ToolContext:
    """Everything a tool handler receives beyond its typed arguments — the
    session its writes run on and the project they run against. A handler
    that needs more than this and its arguments is not a tool.

    `memory_rows`/`retrieval_trace` are the one exception (HarnessPlan H5,
    §3.5): `loop.py` constructs one list of each before a turn's loop
    starts and passes the *same* list into every `ToolContext` it builds for
    that turn, so `query_memory`'s handler can append what it retrieved and
    `loop.py` can read the accumulation back after `dispatch` returns —
    without widening the wire-facing `ToolResult` to carry structured
    `CitedRow`s it has no reason to expose to the model. `ToolContext` stays
    frozen (no field is ever reassigned); only the lists' contents mutate.

    `loaded_skill_slot` is the same pattern, shaped for a different rule
    (HarnessPlan H8, §3.6): "one skill at a time — loading a second replaces
    the first". `loop.py` builds one list, of length 0 or 1, before the
    turn's loop starts and passes it into every `ToolContext` the same way;
    `load_skill`'s handler replaces its entire contents (`slot[:] = [spec]`)
    rather than appending, so the slot can never hold more than one
    `SkillSpec`, and `loop.py` reads `slot[-1]` back after dispatch the same
    way it reads `memory_rows`. A plain `list` rather than some
    length-1-enforcing container because the frozen-dataclass mutable-box
    trick is already established by the fields above — a second, differently
    shaped box for the same trick would be inconsistency for no benefit."""

    session: AsyncSession
    project_id: uuid.UUID
    memory_rows: list[CitedRow] = field(default_factory=list)
    retrieval_trace: list[dict] = field(default_factory=list)
    loaded_skill_slot: list[SkillSpec] = field(default_factory=list)

    # HarnessPlan H9, §3.7: what `deep_research` (and any future subagent
    # tool) needs to call `subagents.run_subagent` — `loop.py` builds these
    # once per tool call the same way it builds `memory_rows` etc. above.
    # `turn_id`/`cancel_flag` let a subagent trace against its real parent
    # and inherit the parent's interruptibility rather than invent its own.
    # `status_callback`, when called, appends into a list `loop.py` owns and
    # reads back right after `dispatch` returns, turning each entry into a
    # `StatusEvent` — a plain `await registry.dispatch(...)` has no way to
    # stream anything mid-call, so this is a between-calls "status so far",
    # not a literal live stream (see `loop.py`'s tool-dispatch loop). `None`
    # for `turn_id`/`cancel_flag`/`status_callback` and an empty
    # `working_set` for every existing tool that never touches them."""
    turn_id: uuid.UUID | None = None
    cancel_flag: asyncio.Event | None = None
    status_callback: Callable[[str], None] | None = None
    working_set: list[Ref] = field(default_factory=list)


ToolHandler = Callable[[ToolContext, BaseModel], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    """One catalog entry — schema, dispatch target, and the policy the
    registry enforces on every call (D19).

    `kind` gates subagent eligibility (H8: subagents may only hold `query`
    tools). `tier` gates the approval flow (H7: `confirm` tools pause for a
    human). `core` controls default visibility (H5: non-core tools enter a
    turn's schema list only when a loaded skill declares them). None of
    `tier`/`per_turn_budget`'s enforcement exists yet in H1 — the fields are
    declared now so H7 wires policy onto them without reshaping the catalog
    a second time.
    """

    name: str
    group: str
    kind: Literal["query", "action"]
    args_model: type[BaseModel]
    handler: ToolHandler
    description: str
    tier: Literal["auto", "confirm"] = "auto"
    core: bool = True
    per_turn_budget: int | None = None

    def schema(self) -> dict:
        """The OpenAI-style function schema for this tool, generated from
        `args_model` — never hand-written."""
        parameters = self.args_model.model_json_schema()
        parameters.pop("title", None)
        for prop in parameters.get("properties", {}).values():
            prop.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": parameters},
        }


_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    *,
    name: str,
    group: str,
    kind: Literal["query", "action"],
    tier: Literal["auto", "confirm"] = "auto",
    per_turn_budget: int | None = None,
    core: bool = True,
):
    """Registers the decorated handler as a catalog tool.

    The handler's signature must be `(ctx: ToolContext, args: SomeArgsModel)
    -> ToolResult`; `SomeArgsModel` is read off the second parameter's type
    annotation and becomes both the generated JSON schema and the validator
    for every call to `name`. The handler's docstring first line becomes the
    tool's description — the same text the model sees, so it is written for
    the model, not for a maintainer.
    """

    def decorator(func: ToolHandler) -> ToolHandler:
        params = list(inspect.signature(func).parameters.values())
        if len(params) != 2:
            raise TypeError(f"tool handler {func.__name__!r} must take exactly (ctx, args)")
        args_model = params[1].annotation
        if not (isinstance(args_model, type) and issubclass(args_model, BaseModel)):
            raise TypeError(f"tool handler {func.__name__!r}'s second parameter must be a Pydantic model")
        description = (func.__doc__ or "").strip().split("\n")[0]
        if not description:
            raise ValueError(f"tool handler {func.__name__!r} needs a docstring to use as its description")
        if name in _REGISTRY:
            raise ValueError(f"tool {name!r} is already registered")
        _REGISTRY[name] = ToolSpec(
            name=name,
            group=group,
            kind=kind,
            tier=tier,
            args_model=args_model,
            handler=func,
            description=description,
            core=core,
            per_turn_budget=per_turn_budget,
        )
        return func

    return decorator


def register_dynamic(spec: ToolSpec) -> None:
    """Registers a `ToolSpec` built at runtime rather than via the `@tool`
    decorator — HarnessPlan H10, §3.11: an MCP server's tools are only known
    once its `tools/list` response arrives, at startup, so they can't go
    through `tool()`'s import-time decoration. Same `_REGISTRY` dict, same
    `dispatch()` path, same duplicate-name guard as a static tool — an MCP
    tool is indistinguishable from a hand-written one once it's in here.
    Re-registering the same name (e.g. a server reconnect) replaces the
    existing entry rather than raising, since this can legitimately happen
    more than once per process for the same server."""
    _REGISTRY[spec.name] = spec


def all_specs() -> list[ToolSpec]:
    return list(_REGISTRY.values())


def get(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def core_schemas() -> list[dict]:
    """The always-visible schema list (H5's core set) — every non-core tool
    is invisible until a loaded skill adds it (H8's `schemas_for`, below)."""
    return [spec.schema() for spec in _REGISTRY.values() if spec.core]


def schemas_for(names: list[str]) -> list[dict]:
    """Schemas for exactly these tools, in registry order — HarnessPlan H8
    (§3.6): a loaded skill's declared `tools:` list becomes this turn's
    extra visible schemas, added to `core_schemas()`. A name that does not
    resolve is skipped rather than raised here — `skills.load_index()`
    already validates every skill's `tools:` list against this same
    registry at startup, loudly, so a bad name reaching this function would
    mean that startup check regressed, not something this call site should
    paper over a second time."""
    return [spec.schema() for spec in _REGISTRY.values() if spec.name in names]


def schemas_for_turn(active_skill_tools: list[str] | None) -> list[dict]:
    """The complete schema list one loop iteration sends the model —
    `core_schemas()` plus a loaded skill's declared `tools:` (HarnessPlan
    H8, §3.6), deduplicated by name. A skill's `tools:` list is written for
    a human reading the skill file, not to avoid repeating a tool the core
    set already shows — `literature_review.md`, for one real example, lists
    `search_papers`/`add_paper`/`get_paper`/`save_note`, every one of which
    is already core. Sending the same function schema twice in one request
    is wasted budget at best and provider-dependent behaviour at worst, so
    this is the one place that composition happens and the one place that
    guards against it."""
    schemas = core_schemas()
    if not active_skill_tools:
        return schemas
    seen = {spec["function"]["name"] for spec in schemas}
    schemas.extend(spec for spec in schemas_for(active_skill_tools) if spec["function"]["name"] not in seen)
    return schemas


async def dispatch(ctx: ToolContext, tool_name: str, raw_args: dict) -> ToolResult:
    """Every tool call the loop makes passes through here. A tool name the
    model invented, or arguments that fail `args_model`, never raise —
    both come back as a `model_view` the model can act on, never an
    exception the loop has to special-case."""
    spec = _REGISTRY.get(tool_name)
    if spec is None:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        return ToolResult(model_view=f'Unknown tool "{tool_name}". Available tools: {known}.')
    try:
        args = spec.args_model.model_validate(raw_args)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "(top level)"
        return ToolResult(model_view=f'Invalid arguments for "{tool_name}": {first["msg"]} (field: {field}).')
    return await spec.handler(ctx, args)
