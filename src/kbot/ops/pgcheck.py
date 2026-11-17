"""The checks of 02 §9 on the live PostgreSQL (stage E4b; F1-A6 on the server, point 31).

Run as the administrator (``postgres``) over the Unix socket. It compares the cluster
with what the repository fixes: the SECURITY DEFINER catalog of
``sql/security_definer_catalog.json``, the role attributes of 02 §9, ``pg_hba.conf`` with
only ``local ... peer`` rules, no TCP listener, the migration head of the code, no
setting tied to a database, and the effective privileges, memberships and ownership of
``kbot.ops.privileges``. The same
functions run in the CI against the ephemeral cluster, so the server check is the one
the CI already trusts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import psycopg

from kbot.db.roles import DATABASE, SERVICE_ROLES, SOCKET_DIR
from kbot.ops import privileges

# Role attributes table of 02 §9: (login, connection limit, role settings).
EXPECTED_ATTRIBUTES: dict[str, tuple[bool, int, list[str]]] = {
    "kbot_owner": (False, -1, []),
    "kbot_fn_owner": (False, -1, []),
    **{role: (True, 5, ["statement_timeout=30s"]) for role in SERVICE_ROLES},
    "kbot_backup": (True, 2, ["statement_timeout=0"]),
    "kbot_archive": (True, 2, ["statement_timeout=1h"]),
}
CATALOG_QUERY = """
SELECT p.oid::regprocedure::text,
       pg_get_userbyid(p.proowner)::text,
       COALESCE(p.proconfig, '{}'),
       encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex')
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE p.prosecdef
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY 1
"""
ATTRIBUTES_QUERY = """
SELECT a.rolname, a.rolcanlogin, a.rolconnlimit, a.rolpassword IS NULL,
       a.rolsuper OR a.rolcreatedb OR a.rolcreaterole OR a.rolreplication OR a.rolbypassrls,
       COALESCE(s.setconfig, '{}')
FROM pg_authid a
LEFT JOIN pg_db_role_setting s ON s.setrole = a.oid AND s.setdatabase = 0
WHERE a.rolname LIKE 'kbot\\_%'
ORDER BY a.rolname
"""


def live_catalog(conn: psycopg.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(CATALOG_QUERY).fetchall()
    return [
        {"signature": signature, "owner": owner, "config": list(config), "body_sha256": body}
        for signature, owner, config, body in rows
    ]


def catalog_problems(live: list[dict[str, Any]], expected: list[dict[str, Any]]) -> list[str]:
    if live == expected:
        return []
    live_by = {f["signature"]: f for f in live}
    expected_by = {f["signature"]: f for f in expected}
    problems = [f"unexpected definer function: {s}" for s in live_by.keys() - expected_by.keys()]
    problems += [f"missing definer function: {s}" for s in expected_by.keys() - live_by.keys()]
    problems += [
        f"definer function differs: {s}"
        for s in live_by.keys() & expected_by.keys()
        if live_by[s] != expected_by[s]
    ]
    return problems


def attribute_problems(rows: list[tuple[Any, ...]]) -> list[str]:
    """Rows of ATTRIBUTES_QUERY against the table of 02 §9."""
    problems = []
    found = {}
    for name, login, limit, no_password, powerful, config in rows:
        found[name] = (login, limit, list(config))
        if not no_password:
            problems.append(f"{name} has a password")
        if powerful:
            problems.append(f"{name} has a superuser-like attribute")
    for name, expected in EXPECTED_ATTRIBUTES.items():
        if name not in found:
            problems.append(f"role missing: {name}")
        elif found[name] != expected:
            problems.append(f"{name}: attributes {found[name]}, expected {expected}")
    for name in found.keys() - EXPECTED_ATTRIBUTES.keys():
        problems.append(f"unexpected role: {name}")
    return problems


def hba_problems(rules: list[tuple[Any, ...]], *, allow_map: bool) -> list[str]:
    """Rows of pg_hba_file_rules (type, auth_method, options, error): only local peer, and
    on the server no map at all (P-E4-3)."""
    if not rules:
        return ["pg_hba.conf has no rules"]
    problems = []
    for rule_type, method, options, error in rules:
        if error is not None:
            problems.append(f"pg_hba error: {error}")
        if (rule_type, method) != ("local", "peer"):
            problems.append(f"pg_hba rule not local peer: {rule_type} {method}")
        if not allow_map and options:
            problems.append(f"pg_hba rule has options on the server: {list(options)}")
    return problems


SETTINGS_QUERY = """
SELECT COALESCE(r.rolname::text, '(every role)'), d.datname::text, s.setconfig
FROM pg_db_role_setting s
LEFT JOIN pg_roles r ON r.oid = s.setrole
JOIN pg_database d ON d.oid = s.setdatabase
WHERE d.datname = %s OR r.rolname LIKE 'kbot\\_%%'
ORDER BY 1, 2
"""


def setting_problems(rows: list[tuple[Any, ...]]) -> list[str]:
    """Rows of SETTINGS_QUERY: settings tied to a database. There must be none: a setting
    of a role in a database overrides the role's own one, so the timeout a session really
    gets would differ from the table of 02 §9 (review of E4b, E4b-05)."""
    return [
        f"setting in database {database} for {role}: {list(config)}"
        for role, database, config in rows
    ]


def expected_head(alembic_ini: Path) -> str:
    """The head of the migrations of the code deployed."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(Config(str(alembic_ini))).get_heads()
    if len(heads) != 1:
        raise ValueError(f"the code has {len(heads)} migration heads")
    return str(heads[0])


def migration_problems(rows: list[tuple[Any, ...]], head: str) -> list[str]:
    """Rows of alembic_version against the head of the code (review of E4b, E4b-06)."""
    versions = [str(row[0]) for row in rows]
    if versions != [head]:
        return [f"migrations at {versions or 'nothing'}, the code is at {head}"]
    return []


def run(
    conn: psycopg.Connection,
    catalog: Path,
    *,
    head: str,
    allowed_grants: Iterable[privileges.Grant],
    allow_map: bool = False,
) -> list[str]:
    """Every problem found; an empty list means the cluster matches 02 §9."""
    problems = []
    if conn.info.dbname != DATABASE:
        problems.append(f"connected to {conn.info.dbname}, not {DATABASE}")
    expected = json.loads(catalog.read_text(encoding="utf-8"))
    problems += catalog_problems(live_catalog(conn), expected)
    problems += attribute_problems(conn.execute(ATTRIBUTES_QUERY).fetchall())
    problems += setting_problems(conn.execute(SETTINGS_QUERY, (DATABASE,)).fetchall())
    problems += privileges.problems(conn, allowed_grants)
    rules = conn.execute(
        "SELECT type, auth_method, options, error FROM pg_hba_file_rules ORDER BY rule_number"
    ).fetchall()
    problems += hba_problems(rules, allow_map=allow_map)
    listen = conn.execute("SHOW listen_addresses").fetchone()
    if listen != ("",):
        problems.append(f"listen_addresses = {listen[0] if listen else '?'}")
    locks = conn.execute("SHOW max_locks_per_transaction").fetchone()
    if locks is None or int(locks[0]) < 256:
        problems.append(f"max_locks_per_transaction = {locks[0] if locks else '?'} (02 §3.9: 256)")
    problems += migration_problems(
        conn.execute("SELECT version_num FROM alembic_version").fetchall(), head
    )
    return problems


def policy_grants(repo: Path) -> list[privileges.Grant]:
    """The role matrix of tools/grant_policy.py of the repository deployed."""
    sys.path.insert(0, str(repo))
    try:
        from tools.grant_policy import ALLOWED_GRANTS
    finally:
        sys.path.remove(str(repo))
    return [(g.role, g.privilege, g.kind, g.target) for g in ALLOWED_GRANTS]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="checks of 02 §9 on the live PostgreSQL")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="the repository deployed: its catalog, its migrations and its role matrix",
    )
    parser.add_argument("--allow-map", action="store_true", help="the CI, which uses a map")
    arguments = parser.parse_args(argv)
    repo: Path = arguments.repo
    dsn = os.environ.get("KBOT_ADMIN_DSN", f"host={SOCKET_DIR} dbname={DATABASE} user=postgres")
    with psycopg.connect(dsn, autocommit=True) as conn:
        problems = run(
            conn,
            repo / "sql" / "security_definer_catalog.json",
            head=expected_head(repo / "alembic.ini"),
            allowed_grants=policy_grants(repo),
            allow_map=arguments.allow_map,
        )
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print(
            "OK   PostgreSQL matches 02 §9: catalog, roles, settings, privileges, pg_hba,"
            " no TCP, locks, migrations"
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
