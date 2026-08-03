"""Phase 3.2: matrices, matrix_cells

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "matrices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "selected_paper_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "selected_experiment_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("column_defs", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )

    op.create_table(
        "matrix_cells",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("matrix_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("matrices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=True),
        sa.Column(
            "experiment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("experiments.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("column_key", sa.Text, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("anchor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("quote_anchors.id", ondelete="CASCADE"), nullable=True),
        sa.Column("cached_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("source IN ('extracted', 'user')", name="matrix_cells_source_check"),
        sa.CheckConstraint(
            "source <> 'extracted' OR anchor_id IS NOT NULL", name="matrix_cells_extracted_requires_anchor_check"
        ),
        sa.CheckConstraint(
            "(paper_id IS NOT NULL)::int + (experiment_id IS NOT NULL)::int = 1", name="matrix_cells_one_row_kind_check"
        ),
        sa.UniqueConstraint("matrix_id", "paper_id", "experiment_id", "column_key", name="matrix_cells_row_column_key"),
    )


def downgrade() -> None:
    op.drop_table("matrix_cells")
    op.drop_table("matrices")
