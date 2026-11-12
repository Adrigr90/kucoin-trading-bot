"""The checks of 02 §9 on the live PostgreSQL (stage E4b; F1-A6 on the server, point 31).

Run as the administrator (``postgres``) over the Unix socket. It compares the cluster
with what the repository fixes: the SECURITY DEFINER catalog of
``sql/security_definer_catalog.json``, the role attributes of 02 §9, ``pg_hba.conf`` with
only ``local ... peer`` rules, no TCP listener, and the migration head. The same
functions run in the CI against the ephemeral cluster, so the server check is the one
the CI already trusts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg

from kbot.db.roles import DATABASE, SERVICE_ROLES, SOCKET_DIR

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


def run(conn: psycopg.Connection, catalog: Path, *, allow_map: bool = False) -> list[str]:
    """Every problem found; an empty list means the cluster matches 02 §9."""
    problems = []
    if conn.info.dbname != DATABASE:
        problems.append(f"connected to {conn.info.dbname}, not {DATABASE}")
    expected = json.loads(catalog.read_text(encoding="utf-8"))
    problems += catalog_problems(live_catalog(conn), expected)
    problems += attribute_problems(conn.execute(ATTRIBUTES_QUERY).fetchall())
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
    head = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    if len(head) != 1:
        problems.append(f"alembic_version has {len(head)} rows")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="checks of 02 §9 on the live PostgreSQL")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--allow-map", action="store_true", help="the CI, which uses a map")
    arguments = parser.parse_args(argv)
    dsn = os.environ.get("KBOT_ADMIN_DSN", f"host={SOCKET_DIR} dbname={DATABASE} user=postgres")
    with psycopg.connect(dsn, autocommit=True) as conn:
        problems = run(conn, arguments.catalog, allow_map=arguments.allow_map)
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print("OK   PostgreSQL matches 02 §9: catalog, roles, pg_hba, no TCP, locks, migrations")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
