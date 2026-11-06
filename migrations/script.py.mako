"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

from pathlib import Path

from kbot.db.sqlfile import execute_sql_file

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    execute_sql_file(Path(__file__).with_suffix(".sql"))


def downgrade() -> None:
    raise NotImplementedError("downgrades are not supported; write a new migration instead")
