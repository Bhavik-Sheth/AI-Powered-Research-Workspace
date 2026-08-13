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

import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from harness.models import ToolResult


@dataclass(frozen=True)
class ToolContext:
    """Everything a tool handler receives beyond its typed arguments — the
    session its writes run on and the project they run against. A handler
    that needs more than this and its arguments is not a tool."""

    session: AsyncSession
    project_id: uuid.UUID


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


def all_specs() -> list[ToolSpec]:
    return list(_REGISTRY.values())


def get(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def core_schemas() -> list[dict]:
    """The always-visible schema list (H5's core set) — every non-core tool
    is invisible until a loaded skill adds it (H8), which this registry does
    not yet implement; H1's catalog is entirely core."""
    return [spec.schema() for spec in _REGISTRY.values() if spec.core]


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
