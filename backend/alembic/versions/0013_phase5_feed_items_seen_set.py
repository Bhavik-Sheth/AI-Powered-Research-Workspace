"""Phase 5.1: feed_items, seen_set

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feed_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("why_relevant", postgresql.JSONB, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default="new"),
        sa.Column("polled_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('new', 'saved', 'dismissed')", name="feed_items_state_check"),
        sa.UniqueConstraint("project_id", "canonical_id", name="feed_items_project_canonical_uq"),
    )

    op.create_table(
        "seen_set",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_id", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("recorded_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("reason IN ('read', 'library', 'surfaced', 'dismissed')", name="seen_set_reason_check"),
        sa.PrimaryKeyConstraint("project_id", "canonical_id", "reason", name="seen_set_pkey"),
    )


def downgrade() -> None:
    op.drop_table("seen_set")
    op.drop_table("feed_items")
