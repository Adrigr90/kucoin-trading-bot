"""Effective privileges, role memberships, ownership and default privileges (spec 02 §9).

The matrices use PostgreSQL's own has_table_privilege and has_schema_privilege, which
include privileges inherited through role membership and those of predefined roles such
as pg_read_all_data. Anything granted indirectly therefore shows up here as well.
"""

from __future__ import annotations

import psycopg
import pytest

from kbot.db.roles import BACKUP, DATABASE, FUNCTION_OWNER, LOGIN_ROLES, OWNER, SCHEMAS

pytestmark = pytest.mark.db

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
LIMIT_READERS = ("kbot_risk", "kbot_watchdog")
CHECKED_SCHEMAS = sorted(SCHEMAS | {"public"})
# Every role that must not own anything, plus the PUBLIC pseudo-role.
NON_OWNERS = [*LOGIN_ROLES, FUNCTION_OWNER, "public"]

RELATIONS_SQL = """
    SELECT c.oid::regclass::text
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
"""


def test_the_only_membership_is_backup_in_pg_read_all_data(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        """
        SELECT r.rolname, m.rolname, m.rolsuper
        FROM pg_auth_members a
        JOIN pg_roles r ON r.oid = a.roleid
        JOIN pg_roles m ON m.oid = a.member
        WHERE r.rolname LIKE 'kbot\\_%' OR m.rolname LIKE 'kbot\\_%'
        """
    ).fetchall()
    unexpected = [
        (role, member)
        for role, member, member_is_superuser in rows
        if (role, member) != ("pg_read_all_data", BACKUP) and not member_is_superuser
    ]
    assert unexpected == []
    assert ("pg_read_all_data", BACKUP, False) in rows


def test_effective_table_privileges(admin: psycopg.Connection) -> None:
    granted = admin.execute(
        """
        SELECT who.name, c.oid::regclass::text, p.privilege
        FROM unnest(%s::text[]) AS who(name)
        CROSS JOIN pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS p(privilege)
        WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND has_table_privilege(who.name, c.oid, p.privilege)
        """,
        (NON_OWNERS, TABLE_PRIVILEGES, CHECKED_SCHEMAS),
    ).fetchall()
    relations = [name for (name,) in admin.execute(RELATIONS_SQL, (CHECKED_SCHEMAS,))]
    assert "config.risk_limits" in relations

    expected = {(BACKUP, relation, "SELECT") for relation in relations}
    expected |= {(role, "config.risk_limits", "SELECT") for role in LIMIT_READERS}
    assert set(granted) == expected


def test_effective_schema_privileges(admin: psycopg.Connection) -> None:
    granted = admin.execute(
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
    expected |= {(role, "config", "USAGE") for role in LIMIT_READERS}
    assert set(granted) == expected


def test_project_objects_belong_to_kbot_owner(admin: psycopg.Connection) -> None:
    database_owner = admin.execute(
        "SELECT pg_get_userbyid(datdba)::text FROM pg_database WHERE datname = %s", (DATABASE,)
    ).fetchone()
    assert database_owner == (OWNER,)

    schema_owners = dict(
        admin.execute(
            "SELECT nspname::text, pg_get_userbyid(nspowner)::text FROM pg_namespace"
            " WHERE nspname = ANY(%s)",
            (sorted(SCHEMAS),),
        ).fetchall()
    )
    assert schema_owners == dict.fromkeys(SCHEMAS, OWNER)

    public_owner = admin.execute(
        "SELECT pg_get_userbyid(nspowner)::text FROM pg_namespace WHERE nspname = 'public'"
    ).fetchone()
    assert public_owner == ("pg_database_owner",)

    object_owners = admin.execute(
        """
        SELECT c.oid::regclass::text, pg_get_userbyid(c.relowner)::text
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = ANY(%s)
        """,
        (CHECKED_SCHEMAS,),
    ).fetchall()
    assert object_owners, "no objects found"
    assert {owner for _name, owner in object_owners} == {OWNER}, object_owners


def test_no_default_privileges_are_defined(admin: psycopg.Connection) -> None:
    assert admin.execute("SELECT count(*) FROM pg_default_acl").fetchone() == (0,)
