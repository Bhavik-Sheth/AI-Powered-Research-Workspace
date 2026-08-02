"""SQLAlchemy 2.x ORM models, one class per Schema.md table.

Table shapes are Schema.md's, byte-for-byte (column names, CHECK value lists,
naming conventions). This module only maps them — see Alembic revisions for
DDL and FK/CHECK constraints, which are the authoritative contract at the DB
gate (Rules.md).
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApiKeys(Base):
    """The single-row local settings store (Schema.md `api_keys`, Phase 1)."""

    __tablename__ = "api_keys"
    __table_args__ = (CheckConstraint("id = 1", name="api_keys_single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    providers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    primary_model: Mapped[str | None] = mapped_column(String, nullable=True)
    auxiliary_model: Mapped[str | None] = mapped_column(String, nullable=True)
    vault_path: Mapped[str | None] = mapped_column(String, nullable=True)
    voice_engine: Mapped[str] = mapped_column(String, nullable=False, default="stub")
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class ScheduledJobs(Base):
    """The catch-up-on-launch cursor (Schema.md `scheduled_jobs`, Phase 1).

    `project_id` has no FK constraint until Phase 1.2's migration adds it —
    `projects` does not exist yet in this phase's migration order.
    """

    __tablename__ = "scheduled_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    job_kind: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    interval_seconds: Mapped[int] = mapped_column(nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    next_due_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class ResultStore(Base):
    """The server-side tool-result cache (Schema.md `result_store`, Phase 1).

    `project_id` has no FK constraint until Phase 1.2's migration adds it, for
    the same reason as `ScheduledJobs.project_id`.
    """

    __tablename__ = "result_store"

    result_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    ui_view: Mapped[dict] = mapped_column(JSONB, nullable=False)
    model_view: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
