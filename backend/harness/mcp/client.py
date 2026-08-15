"""MCP wire client (HarnessPlan H10, §3.11) — two transports behind one
interface, just enough JSON-RPC 2.0 to call `initialize`, `tools/list`, and
`tools/call`.

**Dependency note, stated once here because this file is the reason for it.**
The official `mcp` Python SDK is the obviously-correct dependency for this in
a normal engineering context — full protocol coverage (resources, prompts,
sampling, proper capability negotiation), maintained upstream, exercised
against real servers. Adding a dependency requires asking first
(`PLANNER/Rules.md`, AI Agent Behaviour Rules), and this task could not get an
answer mid-session. This module is a deliberately minimal hand-rolled
stand-in scoped to exactly what `harness/mcp/bridge.py` needs — `tools/list`
and `tools/call` over stdio or HTTP+SSE — not a from-scratch reimplementation
of the spec. Adopting the real `mcp` package, once asked, is the natural
follow-up; this client should be deleted in favour of it, not extended
indefinitely.
"""

import asyncio
import itertools
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "research-companion-os", "version": "0.1.0"}
_DEFAULT_TIMEOUT = 15.0
_MAX_NOTIFICATION_SKIPS = 20  # stdio can interleave log notifications before a real response


class MCPTransportError(Exception):
    """Any transport- or protocol-level failure talking to an MCP server.
    The only exception `harness/mcp/bridge.py` catches — everything else
    is a genuine bug, not a "server is unreachable" condition."""


@dataclass(frozen=True)
class MCPToolDescriptor:
    """One entry from a server's `tools/list` response — name, human
    description, and its JSON Schema for arguments (not yet a Pydantic
    model; `bridge.py` builds one from `input_schema`)."""

    name: str
    description: str
    input_schema: dict


class MCPClient(Protocol):
    """The minimal surface both transports implement — list what a server
    offers, call one of its tools. No resources, no prompts, no sampling:
    `bridge.py` needs nothing else."""

    async def list_tools(self) -> list[MCPToolDescriptor]: ...

    async def call_tool(self, name: str, args: dict) -> dict: ...

    async def close(self) -> None: ...


def _tool_descriptors_from_result(result: dict | None) -> list[MCPToolDescriptor]:
    tools = (result or {}).get("tools", [])
    return [
        MCPToolDescriptor(
            name=entry["name"],
            description=entry.get("description") or "",
            input_schema=entry.get("inputSchema") or {"type": "object", "properties": {}},
        )
        for entry in tools
        if "name" in entry
    ]


class StdioMCPClient:
    """Spawns the server as a subprocess and speaks newline-delimited
    JSON-RPC over its stdin/stdout — MCP's stdio framing."""

    def __init__(self, command: list[str], env: dict[str, str] | None = None, timeout: float = _DEFAULT_TIMEOUT):
        if not command:
            raise ValueError("stdio MCP client needs a non-empty command")
        self._command = command
        self._env = env
        self._timeout = timeout
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = itertools.count(1)
        self._initialized = False

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
            )
        except (OSError, FileNotFoundError) as exc:
            raise MCPTransportError(f"failed to launch MCP server {self._command!r}: {exc}") from exc
        self._initialized = False
        return self._process

    async def _rpc(self, method: str, params: dict, *, notify: bool = False) -> dict | None:
        process = await self._ensure_process()
        if process.stdin is None or process.stdout is None:
            raise MCPTransportError("MCP stdio process has no stdin/stdout pipes")
        request_id = None if notify else next(self._next_id)
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            payload["id"] = request_id
        try:
            process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            raise MCPTransportError(f"MCP stdio write to {method!r} failed: {exc}") from exc
        if notify:
            return None

        for _ in range(_MAX_NOTIFICATION_SKIPS):
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=self._timeout)
            except asyncio.TimeoutError as exc:
                raise MCPTransportError(f"MCP stdio read for {method!r} timed out after {self._timeout}s") from exc
            if not raw:
                stderr = b""
                if process.stderr is not None:
                    stderr = await process.stderr.read(2048)
                raise MCPTransportError(
                    f"MCP server closed stdout while waiting for {method!r}: {stderr.decode(errors='replace')[:500]}"
                )
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue  # a non-JSON-RPC line (server log noise) — skip, don't fail the call
            if message.get("id") == request_id:
                if "error" in message:
                    raise MCPTransportError(f"MCP server error calling {method!r}: {message['error']}")
                return message.get("result", {})
            # a notification or a response to a different in-flight call — skip
        raise MCPTransportError(f"MCP server never answered {method!r} after {_MAX_NOTIFICATION_SKIPS} lines")

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        await self._rpc(
            "initialize",
            {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {}, "clientInfo": _CLIENT_INFO},
        )
        await self._rpc("notifications/initialized", {}, notify=True)
        self._initialized = True

    async def list_tools(self) -> list[MCPToolDescriptor]:
        await self._ensure_initialized()
        result = await self._rpc("tools/list", {})
        return _tool_descriptors_from_result(result)

    async def call_tool(self, name: str, args: dict) -> dict:
        await self._ensure_initialized()
        result = await self._rpc("tools/call", {"name": name, "arguments": args})
        return result or {}

    async def close(self) -> None:
        if self._process is None or self._process.returncode is not None:
            return
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5)
        except asyncio.TimeoutError:
            self._process.kill()


class HttpMCPClient:
    """POSTs JSON-RPC requests to a remote MCP endpoint over `httpx` and
    reads either a plain JSON body or an SSE stream back, per MCP's
    streamable-HTTP transport."""

    def __init__(self, url: str, headers: dict[str, str] | None = None, timeout: float = _DEFAULT_TIMEOUT):
        if not url:
            raise ValueError("http MCP client needs a url")
        self._url = url
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._next_id = itertools.count(1)
        self._initialized = False

    async def _rpc(self, method: str, params: dict, *, notify: bool = False) -> dict | None:
        request_id = None if notify else next(self._next_id)
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            payload["id"] = request_id
        headers = {**self._headers, "content-type": "application/json", "accept": "application/json, text/event-stream"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", self._url, json=payload, headers=headers) as response:
                    response.raise_for_status()
                    if notify:
                        return None
                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" in content_type:
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[len("data:"):].strip()
                            if not data:
                                continue
                            try:
                                message = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            if message.get("id") == request_id:
                                if "error" in message:
                                    raise MCPTransportError(f"MCP server error calling {method!r}: {message['error']}")
                                return message.get("result", {})
                        raise MCPTransportError(f"MCP HTTP stream for {method!r} ended without a matching response")
                    body = await response.aread()
                    try:
                        message = json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise MCPTransportError(f"MCP server sent invalid JSON for {method!r}: {exc}") from exc
                    if "error" in message:
                        raise MCPTransportError(f"MCP server error calling {method!r}: {message['error']}")
                    return message.get("result", {})
        except httpx.HTTPError as exc:
            raise MCPTransportError(f"MCP HTTP request for {method!r} failed: {exc}") from exc

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        await self._rpc(
            "initialize",
            {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {}, "clientInfo": _CLIENT_INFO},
        )
        await self._rpc("notifications/initialized", {}, notify=True)
        self._initialized = True

    async def list_tools(self) -> list[MCPToolDescriptor]:
        await self._ensure_initialized()
        result = await self._rpc("tools/list", {})
        return _tool_descriptors_from_result(result)

    async def call_tool(self, name: str, args: dict) -> dict:
        await self._ensure_initialized()
        result = await self._rpc("tools/call", {"name": name, "arguments": args})
        return result or {}

    async def close(self) -> None:
        return None  # a fresh httpx.AsyncClient is opened per request; nothing held open
