"""Run a reviewed SQL file inside the current Alembic migration.

Migrations keep all their SQL in ``.sql`` files so that the CI whitelist analyzer
(``tools/sql_guard.py``) sees every statement that reaches the database.
"""

from __future__ import annotations

from pathlib import Path

from alembic import op


def execute_sql_file(path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    # Without parameters, psycopg sends the file with the simple query protocol, which
    # accepts several statements and does not treat '%' as a placeholder.
    op.get_bind().exec_driver_sql(sql, execution_options={"no_parameters": True})
