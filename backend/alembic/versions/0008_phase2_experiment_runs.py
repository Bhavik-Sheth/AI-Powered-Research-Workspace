"""Phase 2: experiment_runs

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "experiment_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer, nullable=True),
        sa.Column("image", sa.Text, nullable=False),
        sa.Column("reqs_hash", sa.Text, nullable=False),
        sa.Column("notebook_hash", sa.Text, nullable=False),
        sa.Column("stdout_ref", sa.Text, nullable=False),
        sa.Column("artifacts", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("run_kind", sa.Text, nullable=False),
        sa.Column("network_enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("gpu_enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("run_kind IN ('clean_run_all', 'interactive')", name="experiment_runs_run_kind_check"),
    )


def downgrade() -> None:
    op.drop_table("experiment_runs")
