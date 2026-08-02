"""Phase 1.4: papers, paper_content, quote_anchors, paper_cards, paper_edges, project_papers

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 768


def upgrade() -> None:
    op.create_table(
        "papers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("canonical_id", sa.Text, nullable=False, unique=True),
        sa.Column("canonical_id_source", sa.Text, nullable=False),
        sa.Column("doi", sa.Text, nullable=True),
        sa.Column("arxiv_id", sa.Text, nullable=True),
        sa.Column("openalex_id", sa.Text, nullable=True),
        sa.Column("s2_id", sa.Text, nullable=True),
        sa.Column("pwc_id", sa.Text, nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("abstract", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("pdf_path", sa.Text, nullable=True),
        sa.Column("pdf_origin", sa.Text, nullable=True),
        sa.Column("fetch_state", sa.Text, nullable=False, server_default="queued"),
        sa.Column("parse_state", sa.Text, nullable=False, server_default="queued"),
        sa.Column("embed_state", sa.Text, nullable=False, server_default="queued"),
        sa.Column("extract_state", sa.Text, nullable=False, server_default="queued"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("canonical_id_source IN ('doi', 'arxiv', 'openalex', 's2')", name="papers_canonical_id_source_check"),
        sa.CheckConstraint(
            "pdf_origin IS NULL OR pdf_origin IN ('arxiv', 'unpaywall', 's2_oa', 'user_upload')",
            name="papers_pdf_origin_check",
        ),
        sa.CheckConstraint("fetch_state IN ('queued', 'running', 'done', 'failed', 'degraded')", name="papers_fetch_state_check"),
        sa.CheckConstraint("parse_state IN ('queued', 'running', 'done', 'failed', 'degraded')", name="papers_parse_state_check"),
        sa.CheckConstraint("embed_state IN ('queued', 'running', 'done', 'failed', 'degraded')", name="papers_embed_state_check"),
        sa.CheckConstraint("extract_state IN ('queued', 'running', 'done', 'failed', 'degraded')", name="papers_extract_state_check"),
    )

    op.create_table(
        "paper_content",
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("full_text", sa.Text, nullable=False),
        sa.Column("sections", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("references", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("datasets", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("code_links", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("parser_version", sa.Text, nullable=False),
        sa.Column("parsed_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )

    op.create_table(
        "quote_anchors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quote", sa.Text, nullable=False),
        sa.Column("prefix", sa.Text, nullable=False),
        sa.Column("suffix", sa.Text, nullable=False),
        sa.Column("char_start", sa.Integer, nullable=False),
        sa.Column("char_end", sa.Integer, nullable=False),
        sa.Column("section_heading", sa.Text, nullable=True),
        sa.Column("page_hint", sa.Integer, nullable=True),
        sa.Column("bbox_hint", postgresql.JSONB, nullable=True),
        sa.Column("validated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("char_start >= 0", name="quote_anchors_char_start_check"),
        sa.CheckConstraint("char_end > char_start", name="quote_anchors_char_end_check"),
    )

    op.create_table(
        "paper_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_key", sa.Text, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("anchor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("quote_anchors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("extracted_by_model", sa.Text, nullable=False),
        sa.CheckConstraint(
            "field_key IN ('problem', 'method', 'datasets', 'results', 'limitations')", name="paper_cards_field_key_check"
        ),
        sa.UniqueConstraint("paper_id", "field_key", name="paper_cards_paper_id_field_key_key"),
    )

    op.create_table(
        "paper_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("src_type", sa.Text, nullable=False),
        sa.Column("src_id", sa.Text, nullable=False),
        sa.Column("dst_type", sa.Text, nullable=False),
        sa.Column("dst_id", sa.Text, nullable=False),
        sa.Column("relation", sa.Text, nullable=False),
        sa.Column("provenance", sa.Text, nullable=False),
        sa.Column("source_api", sa.Text, nullable=True),
        sa.Column("confidence", sa.REAL, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "src_type IN ('paper','author','dataset','repo','topic','method','concept')", name="paper_edges_src_type_check"
        ),
        sa.CheckConstraint(
            "dst_type IN ('paper','author','dataset','repo','topic','method','concept')", name="paper_edges_dst_type_check"
        ),
        sa.CheckConstraint(
            "relation IN ('cites','cited_by','authored_by','uses_dataset','has_code','has_topic','method_of','related_method')",
            name="paper_edges_relation_check",
        ),
        sa.CheckConstraint("provenance IN ('metadata', 'llm')", name="paper_edges_provenance_check"),
        sa.UniqueConstraint("src_type", "src_id", "dst_type", "dst_id", "relation", "provenance", name="paper_edges_identity_key"),
    )

    op.create_table(
        "project_papers",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("papers.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("relevance", sa.Text, nullable=False, server_default="unset"),
        sa.Column("why_relevant", sa.Text, nullable=True),
        sa.Column("added_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resume_position", postgresql.JSONB, nullable=True),
        sa.CheckConstraint("relevance IN ('relevant', 'somewhat', 'not', 'unset')", name="project_papers_relevance_check"),
    )


def downgrade() -> None:
    op.drop_table("project_papers")
    op.drop_table("paper_edges")
    op.drop_table("paper_cards")
    op.drop_table("quote_anchors")
    op.drop_table("paper_content")
    op.drop_table("papers")
