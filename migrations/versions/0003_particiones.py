"""Phase 2, stage E3: market tables partitioned by UTC day, and the historical archive.

Revision ID: 0003
Revises: 0002
"""

from pathlib import Path

from kbot.db.sqlfile import execute_sql_file

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file(Path(__file__).with_suffix(".sql"))


def downgrade() -> None:
    raise NotImplementedError("downgrades are not supported; write a new migration instead")
