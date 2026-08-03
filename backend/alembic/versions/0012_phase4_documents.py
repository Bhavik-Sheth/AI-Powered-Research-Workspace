"""Phase 4.1: documents

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("citation_findings", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("last_compiled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_compile_engine", sa.Text, nullable=True),
        sa.CheckConstraint(
            "last_compile_engine IS NULL OR last_compile_engine IN ('swiftlatex', 'tectonic')",
            name="documents_last_compile_engine_check",
        ),
        sa.UniqueConstraint("project_id", "file_path", name="documents_project_id_file_path_key"),
    )


def downgrade() -> None:
    op.drop_table("documents")
