"""MCP tool bridge (HarnessPlan H10, §3.11) — turns a configured MCP server
into registry `ToolSpec`s the loop can call exactly like any static tool.

**Security note, stated once, per the plan.** A third-party tool description
and its JSON Schema are untrusted text and untrusted structure entering a
context that has DB-mutating tools available. Every tool this module
produces is `tier="confirm"` and `core=False`, unconditionally — there is no
per-server override to relax that default, on purpose (HarnessPlan §8 risk
#1: "do not relax per-server casually"). `kind="action"` for the same
reason: an MCP tool's real effects are unknown to this codebase, so it is
never eligible for a query-only subagent (H8/H9's registry enforcement).

**Degrade, never fail a turn.** `bridge_server` catches every transport-level
failure and returns `[]` — a dead or misconfigured server simply contributes
no tools, exactly as if it were not configured. The caller in
`harness/mcp/__init__.py` wraps each server's call in its own try/except too
(belt and suspenders), so one bad server config can never block another
server, or startup itself, from succeeding.
"""

import logging
from typing import Any

from pydantic import BaseModel, create_model

from harness.mcp.client import HttpMCPClient, MCPClient, MCPToolDescriptor, MCPTransportError, StdioMCPClient
from harness.models import ToolResult
from harness.registry import ToolContext, ToolSpec, register_dynamic

logger = logging.getLogger(__name__)

# JSON Schema `type` -> Python type, for the properties this bridge can
# express directly. Anything else (union types, `$ref`, enums with a type
# already covered, etc.) falls back to `Any` — permissive on purpose: the
# handler passes the validated dict straight to `call_tool`, so a slightly
# looser field type costs nothing except never catching a mistake `dispatch`
# would otherwise have anyway (the same corrective-model_view path).
_JSON_SCHEMA_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _python_type_for(prop_schema: dict) -> type:
    return _JSON_SCHEMA_TYPES.get(prop_schema.get("type"), Any)  # type: ignore[arg-type]


def _args_model_from_json_schema(tool_name: str, schema: dict) -> type[BaseModel]:
    """Builds a Pydantic model at runtime from an MCP tool's JSON Schema
    input, via `pydantic.create_model` — a standard Pydantic API for exactly
    this, not hand-rolled. This is what lets a dynamically-discovered MCP
    tool flow through the *same* `ToolSpec.schema()` / `registry.dispatch()`
    path as every statically-declared tool: `dispatch()` never special-cases
    an MCP tool, it just calls `args_model.model_validate(raw_args)` like
    always."""
    properties: dict = (schema or {}).get("properties", {}) or {}
    required = set((schema or {}).get("required", []) or [])
    fields: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        py_type = _python_type_for(prop_schema)
        if prop_name in required:
            fields[prop_name] = (py_type, ...)
        else:
            fields[prop_name] = (py_type | None, None)
    safe_suffix = "".join(ch if ch.isalnum() else "_" for ch in tool_name)
    model_name = f"MCPArgs_{safe_suffix}"
    return create_model(model_name, **fields)  # type: ignore[call-overload, no-any-return]


def _stringify_mcp_result(result: dict) -> str:
    """MCP tools return unstructured content blocks (text/image/resource/…
    per the spec). Handle text properly; degrade anything else to a short,
    honest note rather than crashing or guessing at a rendering."""
    if not isinstance(result, dict):
        return "returned non-text content"
    content = result.get("content")
    if not content:
        return "(no content returned)"
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        else:
            parts.append(f"[{block.get('type', 'unknown')} content — not shown]")
    if not parts:
        return "returned non-text content"
    return "\n".join(parts)


def _make_handler(client: MCPClient, mcp_tool_name: str):
    async def handler(ctx: ToolContext, args: BaseModel) -> ToolResult:
        try:
            result = await client.call_tool(mcp_tool_name, args.model_dump(exclude_none=True))
        except MCPTransportError as exc:
            return ToolResult(model_view=f'MCP tool "{mcp_tool_name}" failed: {exc}')
        return ToolResult(model_view=_stringify_mcp_result(result))

    return handler


def spec_from_descriptor(server_name: str, descriptor: MCPToolDescriptor, client: MCPClient) -> ToolSpec:
    """One discovered MCP tool -> one `ToolSpec`, prefixed `mcp__<server>__<tool>`
    so two servers that both ship a `search` never collide."""
    args_model = _args_model_from_json_schema(descriptor.name, descriptor.input_schema)
    return ToolSpec(
        name=f"mcp__{server_name}__{descriptor.name}",
        group="mcp",
        kind="action",
        args_model=args_model,
        handler=_make_handler(client, descriptor.name),
        description=descriptor.description or f'MCP tool "{descriptor.name}" from server "{server_name}".',
        tier="confirm",
        core=False,
        per_turn_budget=None,
    )


def _client_for(server_config: dict) -> MCPClient:
    transport = server_config.get("transport")
    if transport == "stdio":
        command = server_config.get("command")
        if not command:
            raise ValueError("stdio MCP server config missing 'command'")
        return StdioMCPClient(command=list(command), env=server_config.get("env"))
    if transport == "http":
        url = server_config.get("url")
        if not url:
            raise ValueError("http MCP server config missing 'url'")
        return HttpMCPClient(url=url, headers=server_config.get("headers"))
    raise ValueError(f"unknown MCP transport {transport!r} (expected 'stdio' or 'http')")


async def bridge_server(server_config: dict) -> list[ToolSpec]:
    """Connects to one configured MCP server, lists its tools, registers each
    as a `ToolSpec` (via `registry.register_dynamic`), and returns them.
    Never raises past a connection failure — logs and returns `[]` instead,
    so a dead server degrades rather than failing startup or a turn."""
    server_name = server_config.get("name") or "unknown"
    try:
        client = _client_for(server_config)
    except ValueError as exc:
        logger.warning("event=mcp_bad_server_config server=%s error=%s", server_name, exc)
        return []

    try:
        descriptors = await client.list_tools()
    except MCPTransportError as exc:
        logger.warning("event=mcp_server_unreachable server=%s error=%s", server_name, exc)
        return []
    except Exception:
        # Belt and suspenders (HarnessPlan §8 risk #1): this is explicitly
        # the largest new attack surface in the app, so no exception from a
        # third-party server is allowed to propagate past this function.
        logger.exception("event=mcp_server_unexpected_failure server=%s", server_name)
        return []

    specs: list[ToolSpec] = []
    for descriptor in descriptors:
        spec = spec_from_descriptor(server_name, descriptor, client)
        register_dynamic(spec)
        specs.append(spec)
    return specs
