"""Voice.5: api_keys.voice_engine default -> faster_whisper, api_keys.voice_ptt_binding

Voice Layer Plan V10/V12: a fresh install now gets real voice out of the
box instead of the stub, and the push-to-talk chord is a per-install
setting instead of a hardcoded one. The `voice_engine` CHECK already
permits `faster_whisper` (0001) — only the default changes, and existing
rows are not rewritten (Voice Layer Plan §5: "an installed app keeps
whatever it has and can move via the new Settings control").

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("api_keys", "voice_engine", existing_type=sa.Text(), server_default="faster_whisper")
    op.add_column(
        "api_keys",
        sa.Column("voice_ptt_binding", sa.Text(), nullable=False, server_default="Ctrl+Shift"),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "voice_ptt_binding")
    op.alter_column("api_keys", "voice_engine", existing_type=sa.Text(), server_default="stub")
