"""Phase 1.7: paper_chunks, project_chunks

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 768


def upgrade() -> None:
    op.create_table(
        "paper_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("char_span", postgresql.INT4RANGE, nullable=False),
        sa.Column("section_heading", sa.Text, nullable=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("tsv", postgresql.TSVECTOR, sa.Computed("to_tsvector('english', text)", persisted=True), nullable=False),
        sa.CheckConstraint("source_type IN ('paper_section', 'abstract')", name="paper_chunks_source_type_check"),
    )
    op.create_index("paper_chunks_paper_id_idx", "paper_chunks", ["paper_id"])
    op.create_index("paper_chunks_tsv_idx", "paper_chunks", ["tsv"], postgresql_using="gin")
    op.execute("CREATE INDEX paper_chunks_embedding_idx ON paper_chunks USING hnsw (embedding vector_cosine_ops)")

    op.create_table(
        "project_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("char_span", postgresql.INT4RANGE, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("tsv", postgresql.TSVECTOR, sa.Computed("to_tsvector('english', text)", persisted=True), nullable=False),
        sa.CheckConstraint("source_type IN ('note', 'experiment', 'conversation_summary')", name="project_chunks_source_type_check"),
    )
    op.create_index("project_chunks_project_id_source_type_idx", "project_chunks", ["project_id", "source_type"])
    op.create_index("project_chunks_tsv_idx", "project_chunks", ["tsv"], postgresql_using="gin")
    op.execute("CREATE INDEX project_chunks_embedding_idx ON project_chunks USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.drop_table("project_chunks")
    op.drop_table("paper_chunks")
