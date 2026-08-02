"""SQLAlchemy 2.x ORM models, one class per Schema.md table.

Table shapes are Schema.md's, byte-for-byte (column names, CHECK value lists,
naming conventions). This module only maps them — see Alembic revisions for
DDL and FK/CHECK constraints, which are the authoritative contract at the DB
gate (Rules.md). Every `server_default` here mirrors one already set in a
migration; it does not itself define the default, it just tells the ORM one
exists, so an INSERT that leaves the column unset omits it rather than
sending an explicit NULL.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 768


class Base(DeclarativeBase):
    pass


class ApiKeys(Base):
    """The single-row local settings store (Schema.md `api_keys`, Phase 1)."""

    __tablename__ = "api_keys"
    __table_args__ = (CheckConstraint("id = 1", name="api_keys_single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default=text("1"))
    providers: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    primary_model: Mapped[str | None] = mapped_column(String, nullable=True)
    auxiliary_model: Mapped[str | None] = mapped_column(String, nullable=True)
    vault_path: Mapped[str | None] = mapped_column(String, nullable=True)
    voice_engine: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'stub'"))
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ScheduledJobs(Base):
    """The catch-up-on-launch cursor (Schema.md `scheduled_jobs`, Phase 1).

    `project_id` has no FK constraint until Phase 1.2's migration adds it —
    `projects` does not exist yet in this phase's migration order.
    """

    __tablename__ = "scheduled_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    job_kind: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    interval_seconds: Mapped[int] = mapped_column(nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    next_due_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


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
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class Project(Base):
    """One research project (Schema.md `projects`, Phase 1).

    `tab_stack`/`active_tab` are exercised starting Phase 1.8, and
    `corpus_centroid` starting Phase 5 — the full row shape is created now
    because Schema.md assigns the whole table to Phase 1, not per column.
    """

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    focus_seed: Mapped[str | None] = mapped_column(String, nullable=True)
    interest_profile: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("""'{"categories":[],"keywords":[]}'::jsonb""")
    )
    corpus_centroid: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    tab_stack: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    active_tab: Mapped[str | None] = mapped_column(String, nullable=True)
    last_opened_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Notes(Base):
    """User-authored markdown notes (Schema.md `notes`, Phase 1).

    `id` carries no `server_default`: it is generated by Vault Writer, written
    into the note's own YAML frontmatter, and never re-derived — that id, not
    `file_path`, is the identity every highlight, edge and citation uses (D4).
    `file_path` is a locator only, kept unique per project so the writer can
    place a new note without colliding with an existing one.

    `eager_defaults`: Vault Writer reads `updated_at` back immediately after
    an UPDATE (on every note edit) to build the response. Without this,
    asyncpg has no synchronous fallback to lazily reload the `onupdate`-
    generated value and raises `MissingGreenlet`; `eager_defaults` makes the
    flush itself fetch it via `RETURNING`.
    """

    __tablename__ = "notes"
    __table_args__ = (UniqueConstraint("project_id", "file_path", name="notes_project_id_file_path_key"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False)
    frontmatter: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
