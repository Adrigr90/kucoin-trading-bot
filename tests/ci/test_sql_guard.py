"""SQL whitelist analyzer (spec 02 §9, B20) and the first case of acceptance test F1-A8.

F1-A8 (case 1): the CI rejects a statement outside the whitelist.
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


# --- grants outside the role matrix of §9 (reviewer finding 1) -------------------------


@pytest.mark.parametrize(
    "sql",
    [
        # The reviewer's examples: §9 says kbot_ingest has nothing in trading.
        "GRANT USAGE ON SCHEMA trading TO kbot_ingest;",
        "GRANT INSERT ON TABLE trading.signals TO kbot_ingest;",
        # Approvals belong only to kbot_risk.
        "GRANT INSERT ON TABLE trading.approved_orders TO kbot_strategy;",
        "GRANT INSERT ON TABLE trading.risk_decisions TO kbot_broker;",
        # Nobody but kbot_owner writes the limits.
        "GRANT INSERT ON TABLE config.risk_limits TO kbot_web;",
        "GRANT INSERT ON TABLE config.risk_limits TO kbot_risk;",
        # Risk cannot execute; Telegram can only request a pause; backup never gets grants.
        "GRANT INSERT ON TABLE trading.paper_fills TO kbot_risk;",
        "GRANT INSERT ON TABLE trading.signals TO kbot_telegram;",
        "GRANT SELECT ON TABLE config.risk_limits TO kbot_backup;",
        # Compatible with §9 but not in the exact matrix yet: also rejected.
        "GRANT SELECT ON TABLE config.risk_limits TO kbot_web;",
        "GRANT USAGE ON SCHEMA market TO kbot_web;",
        "GRANT SELECT ON TABLE market.candles TO kbot_strategy;",
        "GRANT INSERT ON TABLE market.candles TO kbot_strategy;",
        # One allowed grant does not cover another in the same statement.
        "GRANT USAGE ON SCHEMA config TO kbot_risk, kbot_ingest;",
    ],
)
def test_grants_outside_the_role_matrix_are_rejected(sql: str) -> None:
    problems = check_migration_sql(sql, "test.sql")
    assert any("grant not allowed" in str(problem) for problem in problems), problems


def test_the_grants_of_the_matrix_are_accepted() -> None:
    sql = (
        "GRANT USAGE ON SCHEMA config TO kbot_risk, kbot_watchdog;"
        "GRANT SELECT ON TABLE config.risk_limits TO kbot_risk, kbot_watchdog;"
        "REVOKE ALL ON SCHEMA public FROM PUBLIC;"
    )
    assert check_migration_sql(sql, "test.sql") == []


# --- table definitions (reviewer finding 2) ---------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        # The reviewer's example: a CHECK with a side effect on every insert.
        "CREATE TABLE config.example (payload text, CHECK (pg_notify('canal', payload) IS NULL));",
        "CREATE TABLE config.x (a text CHECK (pg_notify('canal', a) IS NULL));",
        "CREATE TABLE config.x (a numeric CHECK (a > random()));",
        "CREATE TABLE config.x (a text CHECK (a = (SELECT 'x')));",
        "CREATE TABLE config.x (a text CHECK (a OPERATOR(public.=) 'x'));",
        "CREATE TABLE config.x (a bigint DEFAULT nextval('config.s'));",
        "CREATE TABLE config.x (a text DEFAULT set_config('search_path', 'public', false));",
        "CREATE TABLE config.x (a numeric DEFAULT pg_sleep(1));",
        "CREATE TABLE config.x (a numeric DEFAULT random());",
        "CREATE TABLE config.x (a text DEFAULT current_user);",
        "CREATE TABLE config.x (a timestamptz DEFAULT pg_catalog.clock_timestamp());",
        "CREATE TABLE config.x (a int GENERATED ALWAYS AS IDENTITY);",
        "CREATE TABLE config.x (a int, b int GENERATED ALWAYS AS (a * 2) STORED);",
        'CREATE TABLE config.x (a text COLLATE "C");',
        "CREATE TABLE config.x (a money);",
        "CREATE TABLE config.x (a public.evil_type);",
        "CREATE TABLE config.x (a text[]);",
        "CREATE TABLE config.x (LIKE config.risk_limits);",
        "CREATE TABLE config.x (a int) WITH (fillfactor = 70);",
        "CREATE TABLE config.x (a int) INHERITS (config.risk_limits);",
        "CREATE TABLE config.x (a int) PARTITION BY RANGE (a);",
        "CREATE TABLE IF NOT EXISTS config.x (a int);",
        "CREATE TABLE config.x (a int REFERENCES public.t (id));",
        "CREATE TABLE config.x (a int REFERENCES config.t (id) ON DELETE CASCADE);",
        "CREATE TABLE config.x (a int, EXCLUDE USING gist (a WITH =));",
        "CREATE TABLE config.x (a int PRIMARY KEY WITH (fillfactor = 70));",
    ],
)
def test_table_definitions_outside_the_whitelist_are_rejected(sql: str) -> None:
    assert check_migration_sql(sql, "test.sql"), sql


def test_an_ordinary_table_is_accepted() -> None:
    sql = """
        CREATE TABLE trading.example (
            id bigint PRIMARY KEY,
            parent_id bigint NOT NULL REFERENCES trading.parent (id),
            side text NOT NULL CHECK (side IN ('buy', 'sell')),
            qty numeric(38, 18) NOT NULL CHECK (qty > 0 AND qty <= 1000000),
            note text,
            settled boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CHECK (note IS NULL OR note <> ''),
            UNIQUE (parent_id, side)
        );
    """
    assert check_migration_sql(sql, "test.sql") == []


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


# --- partitioning and SECURITY DEFINER functions (02 §3.9) ---------------------------------

REBUILD = "migrations/versions/0003_particiones.sql"
DEFINER = (
    "CREATE FUNCTION market.seal_day(p_day date) RETURNS int4 LANGUAGE plpgsql"
    " SECURITY DEFINER SET search_path = market, pg_temp SET TimeZone = 'UTC'"
    " SET lock_timeout = '5s' AS $$ DECLARE n int8; BEGIN {body} RETURN 1; END $$;"
)
REVOKE = "REVOKE ALL ON FUNCTION market.seal_day(date) FROM PUBLIC;"


def definer(body: str = "") -> str:
    return DEFINER.format(body=body) + REVOKE


def test_a_definer_body_that_was_not_approved_is_rejected() -> None:
    """Closed validation (review of E3a, E3a-01): even a harmless body is rejected unless
    it is one of the bodies approved in sql/security_definer_catalog.json."""
    problems = [str(p) for p in check_migration_sql(definer(), "test.sql")]
    assert any("not the approved one" in problem for problem in problems), problems


def test_one_change_in_an_approved_body_is_rejected(repo_root: Path) -> None:
    text = (repo_root / REBUILD).read_text(encoding="utf-8")
    assert check_migration_sql(text, REBUILD) == []
    changed = text.replace("RETURN v_created;", "RETURN v_created + 0;")
    assert changed != text
    problems = [str(p) for p in check_migration_sql(changed, REBUILD)]
    assert any("prepare_partitions(): its body is not" in p for p in problems), problems


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "WITH gone AS (DELETE FROM config.risk_limits RETURNING 1)"
            " SELECT count(*) INTO n FROM gone;",
            "WITH may not modify",
        ),
        ("PERFORM pg_catalog.pg_terminate_backend(1);", "PERFORM may only take"),
        ("EXECUTE format('TRUNCATE market.%I', 'x');", "template not approved"),
        ("UPDATE market.archive_seals SET day = p_day;", "not allowed in a SECURITY DEFINER"),
    ],
)
def test_the_body_checks_hold_also_without_the_closed_list(body: str, message: str) -> None:
    problems = [str(p) for p in check_migration_sql(definer(body), "test.sql")]
    assert any(message in problem for problem in problems), problems


def test_revoking_only_the_grant_option_does_not_count() -> None:
    sql = DEFINER.format(body="") + (
        "REVOKE GRANT OPTION FOR ALL ON FUNCTION market.seal_day(date) FROM PUBLIC;"
    )
    problems = [str(p) for p in check_migration_sql(sql, "test.sql")]
    assert any("REVOKE ALL FROM PUBLIC missing" in p for p in problems), problems


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE market.instruments (a timestamptz NOT NULL) PARTITION BY RANGE (a);",
        "CREATE TABLE market.candles (open_time timestamptz) PARTITION BY LIST (open_time);",
        "CREATE TABLE market.candles (open_time text NOT NULL) PARTITION BY RANGE (open_time);",
        "CREATE TABLE market.candles_p1 PARTITION OF market.candles"
        " FOR VALUES FROM ('2026-11-10') TO ('2026-11-11');",
        "LOCK TABLE market.candles IN ACCESS EXCLUSIVE MODE;",
        "SET LOCAL lock_timeout = '5s';",
        "ALTER TABLE market.candles ADD CONSTRAINT x CHECK (false);",
        "DROP TABLE market.candles;",
        "CREATE TABLE market.x (a timestamptz, CHECK (date_trunc(a::text, a) = a));",
        "CREATE TABLE market.x (a timestamptz, CHECK (now() > a));",
        definer().replace("market.seal_day(p_day date)", "market.other(p_day date)"),
        definer().replace("SECURITY DEFINER", ""),
        definer().replace("SET lock_timeout = '5s'", ""),
        definer().replace("search_path = market, pg_temp", "search_path = pg_temp, market"),
        definer().replace("LANGUAGE plpgsql", "LANGUAGE sql"),
        definer().replace(REVOKE, ""),
        definer("EXECUTE 'DROP TABLE market.candles';"),
        definer("EXECUTE format('DROP TABLE market.%I CASCADE', 'x');"),
        definer("EXECUTE format('TRUNCATE market.%I', 'x');"),
        definer("UPDATE market.archive_seals SET day = p_day;"),
        definer("DELETE FROM market.candles;"),
        definer("INSERT INTO market.candles (series) VALUES ('last');"),
        definer(
            "WITH gone AS (DELETE FROM config.risk_limits RETURNING 1)"
            " SELECT count(*) INTO n FROM gone;"
        ),
        definer("EXECUTE format('DROP TABLE market.%I', 'instruments');"),
        definer("PERFORM pg_catalog.pg_terminate_backend(1);"),
        "GRANT EXECUTE ON FUNCTION market.seal_day(date) TO PUBLIC;",
        "GRANT EXECUTE ON FUNCTION market.retire_day(date) TO kbot_archive;",
        "GRANT EXECUTE ON FUNCTION market.seal_day(date) TO kbot_ingest;",
        "GRANT EXECUTE ON FUNCTION market.other(date) TO kbot_archive;",
    ],
)
def test_partitions_and_definer_functions_outside_the_design_are_rejected(sql: str) -> None:
    assert check_migration_sql(sql, "test.sql"), sql


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE market.candles CASCADE;",
        "DROP TABLE IF EXISTS market.candles;",
        "DROP TABLE market.instruments;",
        "LOCK TABLE market.candles IN SHARE MODE;",
        "LOCK TABLE market.instruments IN ACCESS EXCLUSIVE MODE;",
        "SET LOCAL statement_timeout = 0;",
        "SET lock_timeout = '5s';",
        "ALTER TABLE market.candles ADD CONSTRAINT x CHECK (false) NOT VALID;",
        "ALTER TABLE market.candles ADD CONSTRAINT x CHECK (true);",
        "ALTER TABLE market.instruments ADD CONSTRAINT x CHECK (false);",
        "ALTER TABLE market.candles DROP CONSTRAINT x;",
    ],
)
def test_the_rebuild_migration_only_locks_checks_and_drops_the_twelve(sql: str) -> None:
    assert check_migration_sql(sql, REBUILD), sql


def test_the_rebuild_statements_are_accepted_in_the_rebuild_migration() -> None:
    sql = (
        "SET LOCAL lock_timeout = '5s';"
        " LOCK TABLE market.candles IN ACCESS EXCLUSIVE MODE;"
        " ALTER TABLE market.candles ADD CONSTRAINT candles_vacia CHECK (false);"
        " DROP TABLE market.candles;"
    )
    assert check_migration_sql(sql, REBUILD) == []
