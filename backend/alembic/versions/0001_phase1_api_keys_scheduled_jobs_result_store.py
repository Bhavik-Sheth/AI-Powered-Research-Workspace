"""Phase 1: api_keys, scheduled_jobs, result_store

`scheduled_jobs.project_id` and `result_store.project_id` are created without
their `projects` FK here — `projects` is created by the next migration
(Phase 1.2), which adds those two FK constraints. See Schema.md for both
tables' full shape.

Revision ID: 0001
Revises:
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.SmallInteger, primary_key=True, server_default=sa.text("1")),
        sa.Column("providers", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("primary_model", sa.Text, nullable=True),
        sa.Column("auxiliary_model", sa.Text, nullable=True),
        sa.Column("vault_path", sa.Text, nullable=True),
        sa.Column("voice_engine", sa.Text, nullable=False, server_default="stub"),
        sa.Column("onboarding_completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("id = 1", name="api_keys_single_row"),
        sa.CheckConstraint(
            "voice_engine IN ('stub', 'faster_whisper', 'whisper_cpp')", name="api_keys_voice_engine_check"
        ),
    )

    op.create_table(
        "scheduled_jobs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("job_kind", sa.Text, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("interval_seconds", sa.Integer, nullable=False),
        sa.Column("last_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_due_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "job_kind IN ('feed_poll', 'interest_profile_reextract')", name="scheduled_jobs_kind_check"
        ),
        sa.UniqueConstraint("job_kind", "project_id", name="scheduled_jobs_kind_project_uq"),
    )

    op.create_table(
        "result_store",
        sa.Column("result_id", sa.Text, primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.Text, nullable=False),
        sa.Column("ui_view", postgresql.JSONB, nullable=False),
        sa.Column("model_view", sa.Text, nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("result_store")
    op.drop_table("scheduled_jobs")
    op.drop_table("api_keys")
