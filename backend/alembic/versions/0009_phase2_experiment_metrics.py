"""Phase 2.3: experiment_metrics

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "experiment_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("unit", sa.Text, nullable=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("recorded_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("source IN ('user', 'measured')", name="experiment_metrics_source_check"),
        sa.CheckConstraint(
            "source <> 'measured' OR run_id IS NOT NULL", name="experiment_metrics_measured_requires_run_check"
        ),
    )


def downgrade() -> None:
    op.drop_table("experiment_metrics")
