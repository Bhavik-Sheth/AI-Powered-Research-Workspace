"""Phase 6.4: papers.references_state — tracks the reference-trace stage

Mirrors `parse_state`/`extract_state`'s exact CHECK shape. `trace_references_job`
(Phase 6.3) already runs on every freshly-parsed paper via `parse_paper_job`,
but nothing recorded whether it had ever run for a paper — this column lets
the open-paper read path heal a pre-Phase-6.3 paper exactly once (still
'queued' = never run) and lets the Library's `Retry` action re-drive it like
any other stage.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECK = "references_state IN ('queued', 'running', 'done', 'failed', 'degraded')"


def upgrade() -> None:
    op.add_column(
        "papers",
        sa.Column("references_state", sa.String(), nullable=False, server_default=sa.text("'queued'")),
    )
    op.create_check_constraint("papers_references_state_check", "papers", CHECK)


def downgrade() -> None:
    op.drop_constraint("papers_references_state_check", "papers", type_="check")
    op.drop_column("papers", "references_state")
