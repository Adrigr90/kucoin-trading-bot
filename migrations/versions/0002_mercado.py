"""Phase 2, stage E2: market data tables, insert-only for kbot_ingest.

Revision ID: 0002
Revises: 0001
"""

from pathlib import Path

from kbot.db.sqlfile import execute_sql_file

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file(Path(__file__).with_suffix(".sql"))


def downgrade() -> None:
    raise NotImplementedError("downgrades are not supported; write a new migration instead")
