"""Phase 2: experiments

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("hypothesis", sa.Text, nullable=True),
        sa.Column("setup", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'planned'")),
        sa.Column("notebook_path", sa.Text, nullable=True),
        sa.Column("network_optin", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("gpu_optin", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('planned', 'remaining', 'in-progress', 'done')", name="experiments_status_check"),
        sa.UniqueConstraint("project_id", "slug", name="experiments_project_id_slug_key"),
    )
    op.create_index("experiments_project_id_created_at_idx", "experiments", ["project_id", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_table("experiments")
