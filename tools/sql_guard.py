"""Whitelist analyzer for every SQL statement that reaches the database (spec 02 §9, B20).

It parses the SQL with the real PostgreSQL 18 grammar (pglast) and accepts only the
statement kinds and options listed here. Anything else fails the CI. A later phase that
needs a new kind of statement (for example SECURITY DEFINER functions) must add it here,
with its own checks, in a reviewed change.

Usage: ``python -m tools.sql_guard`` from the repository root.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pglast import ast as pg
from pglast import parse_sql
from pglast.enums import GrantTargetType, ObjectType, RoleSpecType, RoleStmtType
from pglast.parser import ParseError

from kbot.db import roles

REPO = Path(__file__).resolve().parents[1]

DATABASE = roles.DATABASE
PROJECT_SCHEMAS = roles.SCHEMAS
LOGIN_ROLES = frozenset(roles.LOGIN_ROLES)
PROJECT_ROLES = frozenset(roles.PROJECT_ROLES)

ROLE_OPTIONS = frozenset({"canlogin", "password", "connectionlimit"})
ROLE_SETTINGS = frozenset({"statement_timeout"})
GRANTABLE_SYSTEM_ROLES = {"pg_read_all_data": frozenset({roles.BACKUP})}
DATABASE_PRIVILEGES = frozenset({"connect"})
SCHEMA_PRIVILEGES = frozenset({"usage"})
# Services never get UPDATE or DELETE directly (02 §8); changes go through functions.
TABLE_PRIVILEGES = frozenset({"select", "insert"})
INSERT_TARGETS = frozenset({("config", "risk_limits")})

MIGRATION_MODULE_NAMES = frozenset(
    {"Path", "execute_sql_file", "NotImplementedError", "__file__", "with_suffix"}
)


@dataclass(frozen=True)
class Violation:
    source: str
    statement: int
    message: str

    def __str__(self) -> str:
        where = f"{self.source}, statement {self.statement}" if self.statement else self.source
        return f"{where}: {self.message}"


Checker = Callable[[pg.Node], list[str]]


def check_roles_sql(text: str, source: str = "sql/roles.sql") -> list[Violation]:
    """Cluster-wide objects: roles, their settings, the database and its privileges."""
    return _check_statements(
        text,
        source,
        {
            "CreateRoleStmt": _create_role,
            "AlterRoleSetStmt": _alter_role_set,
            "GrantRoleStmt": _grant_role,
            "CreatedbStmt": _create_database,
            "GrantStmt": _grant_on_database,
        },
    )


def check_migration_sql(text: str, source: str) -> list[Violation]:
    """Objects inside the project database, created by the migrations."""
    return _check_statements(
        text,
        source,
        {
            "CreateSchemaStmt": _create_schema,
            "CreateStmt": _create_table,
            "GrantStmt": _grant_in_database,
            "InsertStmt": _insert,
            "CommentStmt": lambda _stmt: [],
        },
    )


def check_migration_module(text: str, source: str) -> list[Violation]:
    """A migration module may only run its sibling .sql file through execute_sql_file."""
    problems: list[Violation] = []
    try:
        tree = ast.parse(text, filename=source)
    except SyntaxError as exc:
        return [Violation(source, 0, f"Python syntax error: {exc}")]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            problems.append(Violation(source, 0, "plain imports are not allowed"))
        elif isinstance(node, ast.ImportFrom):
            allowed = {("pathlib", "Path"), ("kbot.db.sqlfile", "execute_sql_file")}
            for alias in node.names:
                if (node.module, alias.name) not in allowed or alias.asname:
                    problems.append(
                        Violation(source, 0, f"import not allowed: {node.module}.{alias.name}")
                    )
        elif isinstance(node, ast.Name) and node.id not in MIGRATION_MODULE_NAMES:
            if not isinstance(node.ctx, ast.Store):
                problems.append(Violation(source, 0, f"name not allowed: {node.id}"))
        elif isinstance(node, ast.Attribute) and node.attr not in MIGRATION_MODULE_NAMES:
            problems.append(Violation(source, 0, f"attribute not allowed: {node.attr}"))
    return problems


def check_repository(root: Path = REPO) -> list[Violation]:
    """Check sql/roles.sql and every migration, as the CI does."""
    problems: list[Violation] = []
    roles = root / "sql" / "roles.sql"
    problems += check_roles_sql(_read(roles), _relative(roles, root))

    versions = root / "migrations" / "versions"
    sql_files = sorted(versions.glob("*.sql"))
    py_files = sorted(p for p in versions.glob("*.py") if p.name != "__init__.py")
    if not sql_files:
        problems.append(Violation("migrations/versions", 0, "no migration SQL files found"))
    for sql_file in sql_files:
        if not sql_file.with_suffix(".py").exists():
            problems.append(Violation(_relative(sql_file, root), 0, "SQL file without migration"))
        problems += check_migration_sql(_read(sql_file), _relative(sql_file, root))
    for py_file in py_files:
        if not py_file.with_suffix(".sql").exists():
            problems.append(Violation(_relative(py_file, root), 0, "migration without SQL file"))
        problems += check_migration_module(_read(py_file), _relative(py_file, root))
    return problems


def main() -> int:
    problems = check_repository()
    for problem in problems:
        print(f"REJECTED {problem}")
    if problems:
        print(f"sql_guard: {len(problems)} violation(s)")
        return 1
    print("sql_guard: every statement is in the whitelist")
    return 0


# --- statement checks -------------------------------------------------------------------


def _check_statements(text: str, source: str, allowed: dict[str, Checker]) -> list[Violation]:
    try:
        statements = parse_sql(text)
    except ParseError as exc:
        return [Violation(source, 0, f"SQL does not parse with the PostgreSQL 18 grammar: {exc}")]
    if not statements:
        return [Violation(source, 0, "no SQL statements")]

    problems: list[Violation] = []
    for number, raw in enumerate(statements, start=1):
        stmt = raw.stmt
        kind = type(stmt).__name__
        checker = allowed.get(kind)
        if checker is None:
            problems.append(Violation(source, number, f"statement not in the whitelist: {kind}"))
            continue
        problems += [Violation(source, number, message) for message in checker(stmt)]
    return problems


def _create_role(stmt: pg.CreateRoleStmt) -> list[str]:
    problems: list[str] = []
    if stmt.stmt_type != RoleStmtType.ROLESTMT_ROLE:
        problems.append("use CREATE ROLE, not CREATE USER or CREATE GROUP")
    if stmt.role not in PROJECT_ROLES:
        problems.append(f"unknown role: {stmt.role}")
    options = {option.defname: option for option in stmt.options or ()}
    for name in options.keys() - ROLE_OPTIONS:
        problems.append(f"role option not allowed: {name}")
    if "canlogin" not in options:
        problems.append("LOGIN or NOLOGIN must be explicit")
    password = options.get("password")
    if password is None:
        problems.append("PASSWORD NULL must be explicit")
    elif password.arg is not None:
        problems.append("roles must not have a password")
    return problems


def _alter_role_set(stmt: pg.AlterRoleSetStmt) -> list[str]:
    problems: list[str] = []
    if stmt.database is not None:
        problems.append("per-database role settings are not allowed")
    if _role_name(stmt.role) not in PROJECT_ROLES:
        problems.append(f"unknown role: {_role_name(stmt.role)}")
    if stmt.setstmt is None or stmt.setstmt.name not in ROLE_SETTINGS:
        name = stmt.setstmt.name if stmt.setstmt is not None else None
        problems.append(f"role setting not allowed: {name}")
    return problems


def _grant_role(stmt: pg.GrantRoleStmt) -> list[str]:
    problems: list[str] = []
    if not stmt.is_grant:
        return problems
    if stmt.opt:
        problems.append("role grants must not use WITH options")
    grantees = {_role_name(role) for role in stmt.grantee_roles or ()}
    for granted in stmt.granted_roles or ():
        allowed = GRANTABLE_SYSTEM_ROLES.get(granted.priv_name)
        if allowed is None:
            problems.append(f"role membership not allowed: {granted.priv_name}")
        elif not grantees <= allowed:
            problems.append(f"{granted.priv_name} may only go to {sorted(allowed)}")
    return problems


def _create_database(stmt: pg.CreatedbStmt) -> list[str]:
    problems: list[str] = []
    if stmt.dbname != DATABASE:
        problems.append(f"only the {DATABASE} database may be created")
    options = {option.defname: option.arg for option in stmt.options or ()}
    if set(options) != {"owner"} or _string_value(options["owner"]) != roles.OWNER:
        problems.append("the database must be created only with OWNER kbot_owner")
    return problems


def _grant_on_database(stmt: pg.GrantStmt) -> list[str]:
    if stmt.objtype != ObjectType.OBJECT_DATABASE:
        return ["only database privileges are granted in roles.sql"]
    problems = _grant_common(stmt, DATABASE_PRIVILEGES)
    names = {_string_value(obj) for obj in stmt.objects or ()}
    if names != {DATABASE}:
        problems.append(f"privileges only on the {DATABASE} database")
    return problems


def _create_schema(stmt: pg.CreateSchemaStmt) -> list[str]:
    problems: list[str] = []
    if stmt.schemaname not in PROJECT_SCHEMAS:
        problems.append(f"schema not in the project list: {stmt.schemaname}")
    if stmt.authrole is not None:
        problems.append("schemas belong to the migration role; AUTHORIZATION is not allowed")
    if stmt.schemaElts:
        problems.append("create schema elements in separate statements")
    return problems


def _create_table(stmt: pg.CreateStmt) -> list[str]:
    relation = stmt.relation
    if relation.schemaname not in PROJECT_SCHEMAS:
        return [f"table names must use an explicit project schema: {relation.relname}"]
    if relation.relpersistence != "p":
        return ["temporary and unlogged tables are not allowed"]
    return []


def _grant_in_database(stmt: pg.GrantStmt) -> list[str]:
    if stmt.objtype == ObjectType.OBJECT_SCHEMA:
        problems = _grant_common(stmt, SCHEMA_PRIVILEGES)
        if stmt.is_grant:
            for obj in stmt.objects or ():
                if _string_value(obj) not in PROJECT_SCHEMAS:
                    problems.append(f"grants only on project schemas: {_string_value(obj)}")
        return problems
    if stmt.objtype == ObjectType.OBJECT_TABLE:
        problems = _grant_common(stmt, TABLE_PRIVILEGES)
        for obj in stmt.objects or ():
            if getattr(obj, "schemaname", None) not in PROJECT_SCHEMAS:
                problems.append("table grants need an explicit project schema")
        return problems
    return [f"grants on {stmt.objtype.name} are not allowed"]


def _grant_common(stmt: pg.GrantStmt, allowed_privileges: frozenset[str]) -> list[str]:
    problems: list[str] = []
    if stmt.targtype != GrantTargetType.ACL_TARGET_OBJECT:
        problems.append("grants must name each object (no ALL ... IN SCHEMA)")
    if not stmt.is_grant:
        return problems
    if stmt.grant_option:
        problems.append("WITH GRANT OPTION is not allowed")
    if stmt.privileges is None:
        problems.append("GRANT ALL is not allowed; name each privilege")
    else:
        for privilege in stmt.privileges:
            if privilege.priv_name not in allowed_privileges:
                problems.append(f"privilege not allowed: {privilege.priv_name}")
            if privilege.cols:
                problems.append("column-level grants are not allowed")
    for grantee in stmt.grantees or ():
        if grantee.roletype == RoleSpecType.ROLESPEC_PUBLIC:
            problems.append("nothing is granted to PUBLIC")
        elif _role_name(grantee) not in LOGIN_ROLES:
            problems.append(f"grantee not allowed: {_role_name(grantee)}")
    return problems


def _insert(stmt: pg.InsertStmt) -> list[str]:
    relation = stmt.relation
    target = (relation.schemaname, relation.relname)
    if target not in INSERT_TARGETS:
        return [f"INSERT not allowed into {relation.schemaname}.{relation.relname}"]
    problems: list[str] = []
    select = stmt.selectStmt
    if stmt.returningClause or stmt.onConflictClause or stmt.withClause:
        problems.append("INSERT may not use RETURNING, ON CONFLICT or WITH")
    if not isinstance(select, pg.SelectStmt) or not select.valuesLists:
        return [*problems, "INSERT must use VALUES"]
    for row in select.valuesLists:
        for value in row:
            if not isinstance(value, pg.A_Const) or value.isnull:
                problems.append("INSERT values must be non-null constants")
    return problems


def _role_name(role: pg.RoleSpec | None) -> str | None:
    if role is None or role.roletype != RoleSpecType.ROLESPEC_CSTRING:
        return None
    return role.rolename


def _string_value(node: object) -> str | None:
    if isinstance(node, pg.String):
        return node.sval
    return None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


if __name__ == "__main__":
    sys.exit(main())
