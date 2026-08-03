"""Phase 3.3: idea_edges

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "idea_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("src_type", sa.Text, nullable=False),
        sa.Column("src_id", sa.Text, nullable=False),
        sa.Column("dst_type", sa.Text, nullable=False),
        sa.Column("dst_id", sa.Text, nullable=False),
        sa.Column("relation", sa.Text, nullable=False),
        sa.Column("provenance", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "src_type IN ('note','experiment','paper','dataset','method','concept','highlight')",
            name="idea_edges_src_type_check",
        ),
        sa.CheckConstraint(
            "dst_type IN ('note','experiment','paper','dataset','method','concept','highlight')",
            name="idea_edges_dst_type_check",
        ),
        sa.CheckConstraint(
            "relation IN ('inspired_by','uses_dataset','references_note','relates_to','contradicts')",
            name="idea_edges_relation_check",
        ),
        sa.CheckConstraint("provenance IN ('metadata', 'llm', 'user')", name="idea_edges_provenance_check"),
        sa.UniqueConstraint(
            "project_id", "src_type", "src_id", "dst_type", "dst_id", "relation", "provenance", name="idea_edges_identity_key"
        ),
    )


def downgrade() -> None:
    op.drop_table("idea_edges")
