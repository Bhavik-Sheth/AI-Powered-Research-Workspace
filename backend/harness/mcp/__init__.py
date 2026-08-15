"""MCP adapter package entry point (HarnessPlan H10, §3.11).

`connect_configured_servers` is the one line `main.py`'s lifespan calls —
everything else here (`client.py`'s transports, `bridge.py`'s `ToolSpec`
wrapping) is an implementation detail this function sequences. Connection
happens once, at startup, "never in a request path" (Rules.md); nothing in
the harness's per-turn loop calls into this package again — the tools it
registered stay in the shared registry for the process lifetime.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

import settings as settings_module
from harness.mcp.bridge import bridge_server

logger = logging.getLogger(__name__)


async def connect_configured_servers(session: AsyncSession) -> int:
    """Reads `settings.get_mcp_servers` and bridges each one, one at a time.

    Each server's `bridge_server` call is wrapped in its own try/except here
    too, on top of `bridge_server`'s own internal catch — this is the
    largest new attack surface in the app (HarnessPlan §8 risk #1), so one
    misbehaving server can never prevent the next server, or the rest of
    startup, from proceeding. Returns the total tool count registered, for
    the one startup log line.
    """
    servers = await settings_module.get_mcp_servers(session)
    total_tools = 0
    for server_config in servers:
        name = server_config.get("name", "unknown")
        try:
            specs = await bridge_server(server_config)
        except Exception:
            logger.exception("event=mcp_connect_unexpected_failure server=%s", name)
            continue
        total_tools += len(specs)
        logger.info("event=mcp_server_connected server=%s tools=%d", name, len(specs))
    return total_tools
