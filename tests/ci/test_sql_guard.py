"""SQL whitelist analyzer (spec 02 §9, B20) and the first case of acceptance test A8.

A8 (case 1): the CI rejects a statement outside the whitelist.
B20: a pglast version with the PostgreSQL 18 grammar parses every migration.
"""

from __future__ import annotations

from pathlib import Path

import pglast
import pytest

from tools.sql_guard import (
    check_migration_module,
    check_migration_sql,
    check_repository,
    check_roles_sql,
)


def test_b20_pglast_uses_the_postgresql_18_grammar() -> None:
    major, _minor = pglast.parser.get_postgresql_version()
    assert major == 18
    # Virtual generated columns are new in PostgreSQL 18; older grammars reject them.
    pglast.parse_sql("CREATE TABLE t (a int, b int GENERATED ALWAYS AS (a * 2) VIRTUAL)")


def test_the_repository_sql_is_inside_the_whitelist(repo_root: Path) -> None:
    assert [str(problem) for problem in check_repository(repo_root)] == []


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER SYSTEM SET listen_addresses = '*';",
        "DO $$ BEGIN NULL; END $$;",
        "CREATE ROLE kbot_ingest LOGIN PASSWORD 'secret';",
        "CREATE ROLE kbot_ingest LOGIN;",
        "CREATE ROLE kbot_ingest PASSWORD NULL;",
        "CREATE ROLE kbot_ingest LOGIN PASSWORD NULL SUPERUSER;",
        "CREATE ROLE kbot_ingest LOGIN PASSWORD NULL CREATEROLE;",
        "CREATE USER kbot_ingest PASSWORD NULL;",
        "CREATE ROLE intruder LOGIN PASSWORD NULL;",
        "ALTER ROLE kbot_ingest SET search_path = public;",
        "ALTER ROLE kbot_ingest PASSWORD 'secret';",
        "GRANT pg_write_all_data TO kbot_ingest;",
        "GRANT pg_read_all_data TO kbot_web;",
        "GRANT CONNECT ON DATABASE kbot TO PUBLIC;",
        "GRANT CREATE ON DATABASE kbot TO kbot_ingest;",
        "CREATE DATABASE other OWNER kbot_owner;",
        "COPY (SELECT 1) TO PROGRAM 'id';",
    ],
)
def test_roles_sql_rejects(sql: str) -> None:
    assert check_roles_sql(sql), sql


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE risk_limits (a int);",
        "CREATE TEMP TABLE config.x (a int);",
        "CREATE SCHEMA evil;",
        "CREATE SCHEMA market AUTHORIZATION kbot_ingest;",
        "GRANT CREATE ON SCHEMA market TO kbot_ingest;",
        "GRANT ALL ON SCHEMA market TO kbot_ingest;",
        "GRANT USAGE ON SCHEMA market TO PUBLIC;",
        "GRANT UPDATE ON TABLE config.risk_limits TO kbot_risk;",
        "GRANT DELETE ON TABLE config.risk_limits TO kbot_risk;",
        "GRANT SELECT ON TABLE config.risk_limits TO kbot_risk WITH GRANT OPTION;",
        "GRANT SELECT ON ALL TABLES IN SCHEMA config TO kbot_risk;",
        "GRANT USAGE ON SCHEMA config TO kbot_owner;",
        "INSERT INTO trading.signals VALUES (1);",
        "INSERT INTO config.risk_limits (version) SELECT 2;",
        "UPDATE config.risk_limits SET max_futures_leverage = 3;",
        "DELETE FROM config.risk_limits;",
        "DROP TABLE config.risk_limits;",
        "SET ROLE postgres;",
        "CREATE EXTENSION plpython3u;",
        "CREATE FUNCTION trading.f() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1';",
        "ALTER TABLE config.risk_limits OWNER TO kbot_ingest;",
        "this is not SQL;",
    ],
)
def test_migration_sql_rejects(sql: str) -> None:
    assert check_migration_sql(sql, "test.sql"), sql


@pytest.mark.parametrize(
    "module",
    [
        "from alembic import op\n\ndef upgrade():\n    op.execute('DROP SCHEMA config')\n",
        "import os\n",
        "from kbot.db.sqlfile import execute_sql_file as run\n",
        "from sqlalchemy import text\n",
    ],
)
def test_migration_modules_cannot_run_their_own_sql(module: str) -> None:
    assert check_migration_module(module, "test.py"), module


@pytest.fixture(scope="module")
def valid_module(repo_root: Path) -> str:
    module = next((repo_root / "migrations" / "versions").glob("*.py"))
    return module.read_text(encoding="utf-8")


def test_a_valid_migration_module_passes(valid_module: str) -> None:
    assert check_migration_module(valid_module, "valid.py") == []


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # SQL outside migrations/versions would never be analyzed.
        ('Path(__file__).with_suffix(".sql")', 'Path("sql/roles.sql")'),
        ('Path(__file__).with_suffix(".sql")', 'Path(__file__).parent / "other.sql"'),
        ('Path(__file__).with_suffix(".sql")', 'Path(__file__).with_suffix(".txt")'),
        ("def upgrade() -> None:\n", "def upgrade() -> None:\n    print('extra')\n"),
        ("def downgrade() -> None:\n", "@staticmethod\ndef downgrade() -> None:\n"),
        ("raise NotImplementedError(", "raise RuntimeError("),
        ('revision = "0001"', 'revision = open("rev").read()'),
        ("def downgrade", "def upgrade_extra():\n    pass\n\n\ndef downgrade"),
    ],
)
def test_tampered_migration_modules_are_rejected(valid_module: str, old: str, new: str) -> None:
    assert old in valid_module
    tampered = valid_module.replace(old, new, 1)
    assert check_migration_module(tampered, "tampered.py"), new


def test_extra_module_level_code_is_rejected(valid_module: str) -> None:
    assert check_migration_module(valid_module + "\nprint('side effect')\n", "extra.py")
