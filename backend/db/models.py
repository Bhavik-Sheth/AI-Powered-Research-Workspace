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
from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
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


class Paper(Base):
    """One real-world paper, global and keyed by canonical id (Schema.md `papers`, Phase 1.4)."""

    __tablename__ = "papers"
    __table_args__ = (
        CheckConstraint(
            "canonical_id_source IN ('doi', 'arxiv', 'openalex', 's2')", name="papers_canonical_id_source_check"
        ),
        CheckConstraint(
            "pdf_origin IS NULL OR pdf_origin IN ('arxiv', 'unpaywall', 's2_oa', 'user_upload')",
            name="papers_pdf_origin_check",
        ),
        CheckConstraint(
            "fetch_state IN ('queued', 'running', 'done', 'failed', 'degraded')", name="papers_fetch_state_check"
        ),
        CheckConstraint(
            "parse_state IN ('queued', 'running', 'done', 'failed', 'degraded')", name="papers_parse_state_check"
        ),
        CheckConstraint(
            "embed_state IN ('queued', 'running', 'done', 'failed', 'degraded')", name="papers_embed_state_check"
        ),
        CheckConstraint(
            "extract_state IN ('queued', 'running', 'done', 'failed', 'degraded')", name="papers_extract_state_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    canonical_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    canonical_id_source: Mapped[str] = mapped_column(String, nullable=False)
    doi: Mapped[str | None] = mapped_column(String, nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String, nullable=True)
    openalex_id: Mapped[str | None] = mapped_column(String, nullable=True)
    s2_id: Mapped[str | None] = mapped_column(String, nullable=True)
    pwc_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    abstract: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String, nullable=True)
    pdf_origin: Mapped[str | None] = mapped_column(String, nullable=True)
    fetch_state: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'queued'"))
    parse_state: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'queued'"))
    embed_state: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'queued'"))
    extract_state: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'queued'"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PaperContent(Base):
    """The docling parse — the offset space every quote anchor resolves
    against (Schema.md `paper_content`, Phase 1.4)."""

    __tablename__ = "paper_content"

    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    full_text: Mapped[str] = mapped_column(String, nullable=False)
    sections: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    references_: Mapped[list] = mapped_column("references", JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    datasets: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    code_links: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    parser_version: Mapped[str] = mapped_column(String, nullable=False)
    parsed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class QuoteAnchors(Base):
    """The shared quote-anchor object (Schema.md `quote_anchors`, Phase 1.4) —
    the same row shape backs a card field, a highlight, and a matrix cell."""

    __tablename__ = "quote_anchors"
    __table_args__ = (
        CheckConstraint("char_start >= 0", name="quote_anchors_char_start_check"),
        CheckConstraint("char_end > char_start", name="quote_anchors_char_end_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    quote: Mapped[str] = mapped_column(String, nullable=False)
    prefix: Mapped[str] = mapped_column(String, nullable=False)
    suffix: Mapped[str] = mapped_column(String, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    section_heading: Mapped[str | None] = mapped_column(String, nullable=True)
    page_hint: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox_hint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class PaperCards(Base):
    """The extractive card, one row per field (Schema.md `paper_cards`, Phase 1.4).

    Absence of a row *is* "not stated" — there is no boolean for it."""

    __tablename__ = "paper_cards"
    __table_args__ = (
        CheckConstraint(
            "field_key IN ('problem', 'method', 'datasets', 'results', 'limitations')",
            name="paper_cards_field_key_check",
        ),
        UniqueConstraint("paper_id", "field_key", name="paper_cards_paper_id_field_key_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    field_key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False)
    anchor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quote_anchors.id", ondelete="CASCADE"), nullable=False
    )
    extracted_by_model: Mapped[str] = mapped_column(String, nullable=False)


class PaperEdges(Base):
    """The global knowledge graph: metadata + paper-intrinsic LLM edges
    (Schema.md `paper_edges`, Phase 1.4 written, Phase 3 surfaced)."""

    __tablename__ = "paper_edges"
    __table_args__ = (
        CheckConstraint(
            "src_type IN ('paper','author','dataset','repo','topic','method','concept')",
            name="paper_edges_src_type_check",
        ),
        CheckConstraint(
            "dst_type IN ('paper','author','dataset','repo','topic','method','concept')",
            name="paper_edges_dst_type_check",
        ),
        CheckConstraint(
            "relation IN ('cites','cited_by','authored_by','uses_dataset','has_code','has_topic','method_of','related_method')",
            name="paper_edges_relation_check",
        ),
        CheckConstraint("provenance IN ('metadata', 'llm')", name="paper_edges_provenance_check"),
        UniqueConstraint(
            "src_type", "src_id", "dst_type", "dst_id", "relation", "provenance", name="paper_edges_identity_key"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    src_type: Mapped[str] = mapped_column(String, nullable=False)
    src_id: Mapped[str] = mapped_column(String, nullable=False)
    dst_type: Mapped[str] = mapped_column(String, nullable=False)
    dst_id: Mapped[str] = mapped_column(String, nullable=False)
    relation: Mapped[str] = mapped_column(String, nullable=False)
    provenance: Mapped[str] = mapped_column(String, nullable=False)
    source_api: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class ProjectPapers(Base):
    """Project membership + the user's own relevance note (Schema.md
    `project_papers`, Phase 1.4) — the only place a paper becomes project-specific."""

    __tablename__ = "project_papers"
    __table_args__ = (
        CheckConstraint("relevance IN ('relevant', 'somewhat', 'not', 'unset')", name="project_papers_relevance_check"),
        PrimaryKeyConstraint("project_id", "paper_id"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    paper_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("papers.id", ondelete="RESTRICT"))
    relevance: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'unset'"))
    why_relevant: Mapped[str | None] = mapped_column(String, nullable=True)
    added_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    resume_position: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
