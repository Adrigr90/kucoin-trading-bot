"""Acceptance test A6: roles, authentication and privileges (spec 02 §9).

(a) every role has no password and the login, connection limit and statement_timeout of
    the role attributes table, and a real session of each service sees its timeout;
(b) pg_hba.conf only has local peer rules;
(c) listen_addresses is empty and PostgreSQL has no TCP port open;
(d) a system user outside the map is rejected, and the owners cannot log in;
(e) no service can create anything, in any schema, and PUBLIC has no rights.
Password failure is deduced from (a) and (b), never from a TCP attempt.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import psycopg
import pytest
from psycopg import errors

from kbot.db.roles import OWNER_ROLES, SERVICE_ROLES

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]

# Role attributes table of spec 02 §9: (login, connection limit, role settings).
EXPECTED_ATTRIBUTES = {
    "kbot_owner": (False, -1, []),
    "kbot_fn_owner": (False, -1, []),
    **{role: (True, 5, ["statement_timeout=30s"]) for role in SERVICE_ROLES},
    "kbot_backup": (True, 2, ["statement_timeout=0"]),
}


def test_a6a_role_attributes(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        """
        SELECT a.rolname, a.rolcanlogin, a.rolconnlimit, a.rolpassword IS NULL,
               a.rolsuper OR a.rolcreatedb OR a.rolcreaterole OR a.rolreplication
                   OR a.rolbypassrls,
               COALESCE(s.setconfig, '{}')
        FROM pg_authid a
        LEFT JOIN pg_db_role_setting s ON s.setrole = a.oid AND s.setdatabase = 0
        WHERE a.rolname LIKE 'kbot\\_%'
        ORDER BY a.rolname
        """
    ).fetchall()
    found = {name: (login, limit, list(config)) for name, login, limit, _, _, config in rows}
    assert found == EXPECTED_ATTRIBUTES
    for name, _login, _limit, no_password, powerful, _config in rows:
        assert no_password, f"{name} has a password"
        assert not powerful, f"{name} has a superuser-like attribute"


@pytest.mark.parametrize("role", [*SERVICE_ROLES, "kbot_backup"])
def test_a6a_each_session_sees_its_statement_timeout(connect: Connect, role: str) -> None:
    expected = "0" if role == "kbot_backup" else "30s"
    with connect(role) as conn:
        assert conn.execute("SHOW statement_timeout").fetchone() == (expected,)


def test_a6a_connection_limit_is_enforced(connect: Connect) -> None:
    opened = [connect("kbot_web") for _ in range(5)]
    try:
        with pytest.raises(psycopg.OperationalError, match="too many connections"):
            connect("kbot_web")
    finally:
        for conn in opened:
            conn.close()


def test_a6b_pg_hba_has_only_local_peer_rules(admin: psycopg.Connection) -> None:
    rules = admin.execute(
        "SELECT type, auth_method, error FROM pg_hba_file_rules ORDER BY rule_number"
    ).fetchall()
    assert rules, "pg_hba.conf has no rules"
    for rule_type, method, error in rules:
        assert error is None
        assert (rule_type, method) == ("local", "peer")


def test_a6c_no_tcp_listener(admin: psycopg.Connection) -> None:
    assert admin.execute("SHOW listen_addresses").fetchone() == ("",)
    sockets = subprocess.run(
        ["sudo", "-n", "ss", "-H", "-l", "-t", "-n", "-p"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert "postgres" not in sockets.stdout, sockets.stdout


def test_a6d_a_system_user_outside_the_map_is_rejected(pghost: str) -> None:
    result = subprocess.run(
        [
            "sudo",
            "-n",
            "-u",
            "nobody",
            "psql",
            "-X",
            "-w",
            "-h",
            pghost,
            "-U",
            "kbot_ingest",
            "-d",
            "kbot",
            "-c",
            "SELECT 1",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        cwd="/",
    )
    assert result.returncode != 0
    assert "Peer authentication failed" in result.stderr, result.stderr


@pytest.mark.parametrize("role", OWNER_ROLES)
def test_a6d_owners_cannot_log_in(connect: Connect, role: str) -> None:
    with pytest.raises(psycopg.OperationalError, match="not permitted to log in"):
        connect(role)


def test_a6e_no_create_privilege_anywhere(admin: psycopg.Connection) -> None:
    roles = [*SERVICE_ROLES, "kbot_backup", "kbot_fn_owner"]
    schema_create = admin.execute(
        """
        SELECT r.rolname, n.nspname
        FROM pg_roles r CROSS JOIN pg_namespace n
        WHERE r.rolname = ANY(%s) AND has_schema_privilege(r.oid, n.oid, 'CREATE')
        """,
        (roles,),
    ).fetchall()
    assert schema_create == []
    temporary = admin.execute(
        """
        SELECT r.rolname FROM pg_roles r
        WHERE r.rolname = ANY(%s)
          AND (has_database_privilege(r.oid, 'kbot', 'TEMPORARY')
               OR has_database_privilege(r.oid, 'kbot', 'CREATE'))
        """,
        (roles,),
    ).fetchall()
    assert temporary == []


def test_a6e_public_has_no_rights(admin: psycopg.Connection) -> None:
    schemas = admin.execute(
        """
        SELECT n.nspname, a.privilege_type
        FROM pg_namespace n, aclexplode(n.nspacl) a
        WHERE a.grantee = 0 AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'
        """
    ).fetchall()
    assert schemas == []
    database = admin.execute(
        """
        SELECT a.privilege_type FROM pg_database d, aclexplode(d.datacl) a
        WHERE d.datname = 'kbot' AND a.grantee = 0
        """
    ).fetchall()
    assert database == []


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE market.intruder (a int)",
        "CREATE TABLE public.intruder (a int)",
        "CREATE TEMP TABLE intruder (a int)",
        "CREATE SCHEMA intruder",
    ],
)
def test_a6e_a_real_service_session_cannot_create(connect: Connect, statement: str) -> None:
    with connect("kbot_ingest") as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(statement)
