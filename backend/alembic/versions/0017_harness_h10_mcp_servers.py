"""HarnessPlan H10: api_keys.mcp_servers — configured MCP server list

Each entry: {"name": str, "transport": "stdio"|"http", "command": [str,...]
(stdio) | "url": str (http), "env"/"headers": dict (optional)}. Read once at
startup by `harness/mcp/__init__.py:connect_configured_servers` — never in a
request path (Rules.md).

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("mcp_servers", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "mcp_servers")
