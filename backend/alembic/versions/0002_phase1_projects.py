"""Phase 1: projects, plus the FK constraints deferred from migration 0001

`scheduled_jobs.project_id` and `result_store.project_id` were created
without their FK in 0001 because `projects` did not exist yet; this
migration adds both constraints now that it does.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("focus_seed", sa.Text, nullable=True),
        sa.Column(
            "interest_profile",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("""'{"categories":[],"keywords":[]}'::jsonb"""),
        ),
        sa.Column("corpus_centroid", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("tab_stack", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("active_tab", sa.Text, nullable=True),
        sa.Column("last_opened_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_foreign_key(
        "scheduled_jobs_project_id_fkey",
        "scheduled_jobs",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "result_store_project_id_fkey",
        "result_store",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("result_store_project_id_fkey", "result_store", type_="foreignkey")
    op.drop_constraint("scheduled_jobs_project_id_fkey", "scheduled_jobs", type_="foreignkey")
    op.drop_table("projects")
