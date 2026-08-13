"""HarnessPlan H1: turn_traces — one row per agent turn

Created `running` at turn start, finalized at the end (HarnessPlan §3.10).
Doubles as turn state for the orphan sweep (H11).

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUS_CHECK = "status IN ('running', 'complete', 'interrupted', 'failed')"


def upgrade() -> None:
    op.create_table(
        "turn_traces",
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_turn_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("turn_traces.turn_id", ondelete="CASCADE"), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'running'")),
        sa.Column("iterations", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("context_blocks", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("retrieval", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("citations", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("skill", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_check_constraint("turn_traces_status_check", "turn_traces", STATUS_CHECK)
    # The orphan sweep (H11) scans for stuck turns on session connect.
    op.create_index("ix_turn_traces_status", "turn_traces", ["status"])
    op.create_index("ix_turn_traces_conversation_id", "turn_traces", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_turn_traces_conversation_id", table_name="turn_traces")
    op.drop_index("ix_turn_traces_status", table_name="turn_traces")
    op.drop_table("turn_traces")
