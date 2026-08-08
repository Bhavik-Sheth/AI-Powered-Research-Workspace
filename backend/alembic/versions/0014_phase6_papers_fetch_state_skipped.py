"""Phase 6.3: papers.fetch_state gains 'skipped' for metadata-only reference stubs

A reference-stub row (Phase 6.3's add_reference_stub) is deliberately never
fetched — it exists to hold a real title/canonical-id for the References box
and the graph until the user explicitly promotes it. That is a distinct state
from 'degraded' (an OA fetch was attempted and found nothing), so it needs its
own CHECK value rather than overloading 'degraded'.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-08
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_CHECK = "fetch_state IN ('queued', 'running', 'done', 'failed', 'degraded')"
NEW_CHECK = "fetch_state IN ('queued', 'running', 'done', 'failed', 'degraded', 'skipped')"


def upgrade() -> None:
    op.drop_constraint("papers_fetch_state_check", "papers", type_="check")
    op.create_check_constraint("papers_fetch_state_check", "papers", NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint("papers_fetch_state_check", "papers", type_="check")
    op.create_check_constraint("papers_fetch_state_check", "papers", OLD_CHECK)
