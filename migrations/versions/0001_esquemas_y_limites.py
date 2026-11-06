"""Phase 1: empty schemas, hardened public schema and the DM risk limits.

Revision ID: 0001
Revises:
"""

from pathlib import Path

from kbot.db.sqlfile import execute_sql_file

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file(Path(__file__).with_suffix(".sql"))


def downgrade() -> None:
    raise NotImplementedError("downgrades are not supported; write a new migration instead")
