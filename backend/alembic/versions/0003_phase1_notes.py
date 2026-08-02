"""Phase 1.6: notes

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notes",
        # No server_default: the frontmatter id is assigned by Vault Writer,
        # not the database (D4) — the row and the file's own YAML id must match.
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("frontmatter", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "file_path", name="notes_project_id_file_path_key"),
    )
    # Backs the notes list and CONTINUE WHERE YOU LEFT OFF (Schema.md Indexing Notes).
    op.create_index("ix_notes_project_id_updated_at", "notes", ["project_id", sa.text("updated_at DESC")])


def downgrade() -> None:
    op.drop_index("ix_notes_project_id_updated_at", table_name="notes")
    op.drop_table("notes")
