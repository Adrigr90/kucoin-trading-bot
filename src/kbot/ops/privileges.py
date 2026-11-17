"""Effective privileges, memberships and ownership of the live database (02 §9).

The same rules as ``tests/db/test_privileges.py``, as functions that return problems, so
that the server runs them too (stage E4b; review of E4b, E4b-04). They use PostgreSQL's own
``has_*_privilege`` functions, which include what is inherited through role membership and
predefined roles, so anything granted indirectly shows up. The expected privileges are
exactly the role matrix of ``tools/grant_policy.py`` (passed in as plain tuples) plus the
read-only access of ``kbot_backup`` through ``pg_read_all_data``: neither more nor fewer.
"""

from __future__ import annotations

from collections.abc import Iterable

import psycopg

from kbot.db.roles import BACKUP, DATABASE, FUNCTION_OWNER, LOGIN_ROLES, OWNER, SCHEMAS

# (role, privilege, kind, target), as tools.grant_policy.Grant.
Grant = tuple[str, str, str, str]

TABLE_PRIVILEGES = [
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
]
SCHEMA_PRIVILEGES = ["USAGE", "CREATE"]
CHECKED_SCHEMAS = sorted(SCHEMAS | {"public"})
# Every role that must not own anything, plus the PUBLIC pseudo-role.
NON_OWNERS = [*LOGIN_ROLES, FUNCTION_OWNER, "public"]
RELKINDS = "('r', 'p', 'v', 'm', 'f')"


def _difference(kind: str, live: set[tuple[str, ...]], expected: set[tuple[str, ...]]) -> list[str]:
    extra = [f"unexpected {kind}: {' '.join(item)}" for item in sorted(live - expected)]
    missing = [f"missing {kind}: {' '.join(item)}" for item in sorted(expected - live)]
    return extra + missing


def membership_problems(conn: psycopg.Connection) -> list[str]:
    """The only membership is kbot_backup in pg_read_all_data. Any other one, whatever its
    INHERIT or SET options, could reach another role's rights with SET ROLE."""
    rows = conn.execute(
        """
        SELECT r.rolname::text, m.rolname::text, m.rolsuper
        FROM pg_auth_members a
        JOIN pg_roles r ON r.oid = a.roleid
        JOIN pg_roles m ON m.oid = a.member
        WHERE r.rolname LIKE 'kbot\\_%' OR m.rolname LIKE 'kbot\\_%'
        """
    ).fetchall()
    problems = [
        f"unexpected membership: {member} in {role}"
        for role, member, member_is_superuser in rows
        if (role, member) != ("pg_read_all_data", BACKUP) and not member_is_superuser
    ]
    if ("pg_read_all_data", BACKUP, False) not in rows:
        problems.append(f"missing membership: {BACKUP} in pg_read_all_data")
    return problems


def table_problems(conn: psycopg.Connection, allowed: Iterable[Grant]) -> list[str]:
    granted = conn.execute(
        f"""
        SELECT who.name, c.oid::regclass::text, p.privilege
        FROM unnest(%s::text[]) AS who(name)
        CROSS JOIN pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS p(privilege)
        WHERE n.nspname = ANY(%s) AND c.relkind IN {RELKINDS}
          AND has_table_privilege(who.name, c.oid, p.privilege)
        """,  # noqa: S608 - a constant list of relation kinds
        (NON_OWNERS, TABLE_PRIVILEGES, CHECKED_SCHEMAS),
    ).fetchall()
    relations = conn.execute(
        f"""
        SELECT c.oid::regclass::text FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = ANY(%s) AND c.relkind IN {RELKINDS}
        """,  # noqa: S608
        (CHECKED_SCHEMAS,),
    ).fetchall()
    expected = {(BACKUP, relation, "SELECT") for (relation,) in relations}
    expected |= {
        (role, target, priv.upper()) for role, priv, kind, target in allowed if kind == "table"
    }
    return _difference("table privilege", {tuple(row) for row in granted}, expected)


def schema_problems(conn: psycopg.Connection, allowed: Iterable[Grant]) -> list[str]:
    granted = conn.execute(
        """
        SELECT who.name, s.name, p.privilege
        FROM unnest(%s::text[]) AS who(name)
        CROSS JOIN unnest(%s::text[]) AS s(name)
        CROSS JOIN unnest(%s::text[]) AS p(privilege)
        WHERE has_schema_privilege(who.name, s.name, p.privilege)
        """,
        (NON_OWNERS, CHECKED_SCHEMAS, SCHEMA_PRIVILEGES),
    ).fetchall()
    expected = {(BACKUP, schema, "USAGE") for schema in CHECKED_SCHEMAS}
    expected |= {
        (role, target, priv.upper()) for role, priv, kind, target in allowed if kind == "schema"
    }
    return _difference("schema privilege", {tuple(row) for row in granted}, expected)


def function_problems(conn: psycopg.Connection, allowed: Iterable[Grant]) -> list[str]:
    """EXECUTE on every function of the project schemas, for every role and for PUBLIC."""
    granted = conn.execute(
        """
        SELECT who.name, p.oid::regprocedure::text, 'EXECUTE'
        FROM unnest(%s::text[]) AS who(name)
        CROSS JOIN pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = ANY(%s) AND has_function_privilege(who.name, p.oid, 'EXECUTE')
        """,
        (NON_OWNERS, sorted(SCHEMAS)),
    ).fetchall()
    expected = {
        (role, target, priv.upper()) for role, priv, kind, target in allowed if kind == "function"
    }
    return _difference("function privilege", {tuple(row) for row in granted}, expected)


def database_problems(conn: psycopg.Connection) -> list[str]:
    """CONNECT for the login roles only; nobody creates schemas or temporary tables."""
    rows = conn.execute(
        """
        SELECT who.name, p.privilege
        FROM unnest(%s::text[]) AS who(name)
        CROSS JOIN unnest(ARRAY['CONNECT', 'CREATE', 'TEMPORARY']) AS p(privilege)
        WHERE has_database_privilege(who.name, %s, p.privilege)
        """,
        (NON_OWNERS, DATABASE),
    ).fetchall()
    expected = {(role, "CONNECT") for role in LOGIN_ROLES}
    return _difference("database privilege", {tuple(row) for row in rows}, expected)


def ownership_problems(conn: psycopg.Connection) -> list[str]:
    problems = []
    owner = conn.execute(
        "SELECT pg_get_userbyid(datdba)::text FROM pg_database WHERE datname = %s", (DATABASE,)
    ).fetchone()
    if owner != (OWNER,):
        problems.append(f"database owner: {owner}")
    schemas = dict(
        conn.execute(
            "SELECT nspname::text, pg_get_userbyid(nspowner)::text FROM pg_namespace"
            " WHERE nspname = ANY(%s)",
            (sorted(SCHEMAS),),
        ).fetchall()
    )
    if schemas != dict.fromkeys(SCHEMAS, OWNER):
        problems.append(f"schema owners: {sorted(schemas.items())}")
    public = conn.execute(
        "SELECT pg_get_userbyid(nspowner)::text FROM pg_namespace WHERE nspname = 'public'"
    ).fetchone()
    if public != ("pg_database_owner",):
        problems.append(f"owner of schema public: {public}")
    others = conn.execute(
        """
        SELECT c.oid::regclass::text, pg_get_userbyid(c.relowner)::text
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = ANY(%s) AND pg_get_userbyid(c.relowner) <> %s
        """,
        (CHECKED_SCHEMAS, OWNER),
    ).fetchall()
    problems += [f"object not owned by {OWNER}: {name} ({who})" for name, who in others]
    functions = conn.execute(
        """
        SELECT p.oid::regprocedure::text, pg_get_userbyid(p.proowner)::text
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = ANY(%s) AND pg_get_userbyid(p.proowner) NOT IN (%s, %s)
        """,
        (sorted(SCHEMAS), OWNER, FUNCTION_OWNER),
    ).fetchall()
    problems += [f"function with another owner: {name} ({who})" for name, who in functions]
    defaults = conn.execute("SELECT count(*) FROM pg_default_acl").fetchone()
    if defaults != (0,):
        problems.append(f"default privileges defined: {defaults}")
    return problems


def problems(conn: psycopg.Connection, allowed: Iterable[Grant]) -> list[str]:
    """Everything above; an empty list means the database has exactly the matrix of §9."""
    allowed = list(allowed)
    return [
        *membership_problems(conn),
        *table_problems(conn, allowed),
        *schema_problems(conn, allowed),
        *function_problems(conn, allowed),
        *database_problems(conn),
        *ownership_problems(conn),
    ]
