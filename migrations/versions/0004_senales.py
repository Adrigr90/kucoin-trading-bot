"""Phase 3, stage F3-E5b: the approved versions of the fichas and the signals.

Revision ID: 0004
Revises: 0003
"""

from pathlib import Path

from kbot.db.sqlfile import execute_sql_file

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file(Path(__file__).with_suffix(".sql"))


def downgrade() -> None:
    raise NotImplementedError("downgrades are not supported; write a new migration instead")
