"""Whitelist analyzer for every SQL statement that reaches the database (spec 02 §9, B20).

It parses the SQL with the real PostgreSQL 18 grammar (pglast) and accepts only the
statement kinds and options listed here. Anything else fails the CI. A later phase that
needs a new kind of statement (for example SECURITY DEFINER functions) must add it here,
with its own checks, in a reviewed change.

Usage: ``python -m tools.sql_guard`` from the repository root.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import json
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pglast import ast as pg
from pglast import parse_sql
from pglast.enums import (
    A_Expr_Kind,
    AlterTableType,
    ConstrType,
    DropBehavior,
    GrantTargetType,
    ObjectType,
    PartitionStrategy,
    RoleSpecType,
    RoleStmtType,
    VariableSetKind,
)
from pglast.parser import ParseError, parse_plpgsql_json

from kbot.db import roles
from tools import grant_policy
from tools.grant_policy import ALLOWED_GRANTS, Grant, section_9_conflict

REPO = Path(__file__).resolve().parents[1]

DATABASE = roles.DATABASE
PROJECT_SCHEMAS = roles.SCHEMAS
LOGIN_ROLES = frozenset(roles.LOGIN_ROLES)
PROJECT_ROLES = frozenset(roles.PROJECT_ROLES)

ROLE_OPTIONS = frozenset({"canlogin", "password", "connectionlimit"})
ROLE_SETTINGS = frozenset({"statement_timeout"})
GRANTABLE_SYSTEM_ROLES = {"pg_read_all_data": frozenset({roles.BACKUP})}
DATABASE_PRIVILEGES = frozenset({"connect"})
SCHEMA_PRIVILEGES = grant_policy.SCHEMA_PRIVILEGES
# Services never get UPDATE or DELETE directly (02 §8); changes go through functions.
TABLE_PRIVILEGES = grant_policy.TABLE_PRIVILEGES
INSERT_TARGETS = frozenset({("config", "risk_limits")})

# Table definitions: what a CREATE TABLE may contain. Anything else is rejected until a
# reviewed change adds it.
COLUMN_TYPES = frozenset(
    {"int2", "int4", "int8", "numeric", "text", "bool", "timestamptz", "date", "uuid", "jsonb"}
)
EXPRESSION_OPERATORS = frozenset({"=", "<>", "<", ">", "<=", ">=", "~", "+", "-", "*", "/"})
EXPRESSION_KINDS = frozenset(
    {
        A_Expr_Kind.AEXPR_OP,
        A_Expr_Kind.AEXPR_IN,
        A_Expr_Kind.AEXPR_BETWEEN,
        A_Expr_Kind.AEXPR_NOT_BETWEEN,
    }
)
# The only function a DEFAULT may call. It is volatile but has no side effects; CHECK
# constraints may call no function at all.
DEFAULT_FUNCTIONS = frozenset({"clock_timestamp"})
# Foreign key actions: NO ACTION and RESTRICT. Cascades would change rows behind the
# insert-only rules of the ledger and the audit trail (02 §0, rule 2).
FOREIGN_KEY_ACTIONS = frozenset({"a", "r"})
TABLE_OPTIONS = (
    "inhRelations",
    "partbound",
    "ofTypename",
    "options",
    "tablespacename",
    "accessMethod",
    "if_not_exists",
)
COLUMN_OPTIONS = ("collClause", "compression", "storage_name", "fdwoptions", "raw_default")
INDEX_OPTIONS = ("options", "indexspace", "indexname", "including", "without_overlaps")

# Phase 2, stage E3 (02 §3.9): the twelve market tables partitioned by UTC day, the one
# migration that rebuilds them from 0002 and the three SECURITY DEFINER functions.
PARTITIONED_TABLES = frozenset(name.split(".", 1)[1] for name in grant_policy.PARTITIONED_TABLES)
REBUILD_MIGRATION = "migrations/versions/0003_particiones.sql"
ACCESS_EXCLUSIVE = 8  # LockStmt.mode of IN ACCESS EXCLUSIVE MODE
# Functions a CHECK may call, each with a constant first argument from its set: enough to
# align a timestamp to the UTC minute, and all immutable.
CHECK_CALLS: Mapping[tuple[str, ...], frozenset[str]] = {
    ("date_trunc",): frozenset({"minute", "day"}),
    ("pg_catalog", "timezone"): frozenset({"UTC"}),
}
DEFINER_FUNCTIONS = frozenset(grant_policy.PARTITION_FUNCTIONS)
DEFINER_SETTINGS = {
    "search_path": ("market", "pg_temp"),
    "timezone": ("UTC",),
    "lock_timeout": ("5s",),
}
# The only statements a SECURITY DEFINER function may build, always with format() and its
# %I and %L: create a day's partition, seal it, count it, detach it and drop it.
DYNAMIC_TEMPLATES = frozenset(
    {
        "CREATE TABLE market.%I PARTITION OF market.%I FOR VALUES FROM (%L) TO (%L)",
        "ALTER TABLE market.%I ADD CONSTRAINT %I CHECK (false) NOT VALID",
        "SELECT count(*) FROM market.%I",
        "ALTER TABLE market.%I DETACH PARTITION market.%I",
        "DROP TABLE market.%I",
    }
)
# Static statements of those functions: reads, and their own bookkeeping rows.
DEFINER_INSERTS = frozenset({("market", "archive_seals"), ("market", "archive_retirements")})
# Closed validation (review of E3a, E3a-01): a SECURITY DEFINER function is accepted only
# with one of the bodies approved in this file, which the CI also compares with the live
# catalog (tests/db/test_security_definer.py). A new body is a reviewed change of it.
APPROVED_BODIES = REPO / "sql" / "security_definer_catalog.json"
# PL/pgSQL statements no SECURITY DEFINER function may contain: dynamic loops, cursors and
# queries, procedure calls and transaction control.
DEFINER_FORBIDDEN = frozenset(
    {
        "PLpgSQL_stmt_dynfors",
        "PLpgSQL_stmt_open",
        "PLpgSQL_stmt_return_query",
        "PLpgSQL_stmt_call",
        "PLpgSQL_stmt_commit",
        "PLpgSQL_stmt_rollback",
    }
)

MIGRATION_IMPORTS = frozenset({("pathlib", "Path"), ("kbot.db.sqlfile", "execute_sql_file")})
MIGRATION_SETTINGS = frozenset({"revision", "down_revision", "branch_labels", "depends_on"})
# The only statement an upgrade() may contain: run the .sql file next to the module, which
# check_repository() analyzes. Any other path could point at SQL that is never analyzed.
SIBLING_SQL_CALL = ast.dump(
    ast.parse('execute_sql_file(Path(__file__).with_suffix(".sql"))', mode="eval").body
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
    """Objects inside the project database, created by the migrations.

    The migration that rebuilds the twelve partitioned tables (``REBUILD_MIGRATION``) may
    also lock them, set its lock timeout, check that they are empty and drop them; no other
    migration may. Every SECURITY DEFINER function is checked with its PL/pgSQL body, and
    must be revoked from PUBLIC in the same file (02 §9 and §3.9)."""
    allowed: dict[str, Checker] = {
        "CreateSchemaStmt": _create_schema,
        "CreateStmt": _create_table,
        "GrantStmt": _grant_in_database,
        "InsertStmt": _insert,
        "CommentStmt": lambda _stmt: [],
        "CreateFunctionStmt": lambda _stmt: [],  # checked with its text, below
    }
    if source == REBUILD_MIGRATION:
        allowed |= {
            "LockStmt": _lock,
            "VariableSetStmt": _set_lock_timeout,
            "AlterTableStmt": _check_empty,
            "DropStmt": _drop_partitioned,
        }
    problems = _check_statements(text, source, allowed)
    try:
        statements = parse_sql(text)
    except ParseError:
        return problems  # already reported
    return problems + _check_definer_functions(text, statements, source)


def check_migration_module(text: str, source: str) -> list[Violation]:
    """A migration module must have exactly the shape of migrations/script.py.mako.

    Allowed at module level: a docstring, the two imports, the four Alembic settings with
    constant values, ``upgrade()`` running only its sibling .sql file and ``downgrade()``
    raising NotImplementedError. Anything else is rejected.
    """
    try:
        tree = ast.parse(text, filename=source)
    except SyntaxError as exc:
        return [Violation(source, 0, f"Python syntax error: {exc}")]

    messages: list[str] = []
    functions: set[str] = set()
    settings: set[str] = set()
    for index, node in enumerate(tree.body):
        if index == 0 and _is_docstring(node):
            continue
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (node.module, alias.name) not in MIGRATION_IMPORTS or alias.asname:
                    messages.append(f"import not allowed: {node.module}.{alias.name}")
        elif isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if len(names) != 1 or len(node.targets) != 1 or names[0] not in MIGRATION_SETTINGS:
                messages.append("only the Alembic settings may be assigned")
            elif not _is_constant_setting(node.value):
                messages.append(f"{names[0]} must be a constant")
            else:
                settings.add(names[0])
        elif isinstance(node, ast.FunctionDef) and node.name in {"upgrade", "downgrade"}:
            functions.add(node.name)
            messages += _check_migration_function(node)
        else:
            messages.append(f"statement not allowed in a migration: {type(node).__name__}")

    if functions != {"upgrade", "downgrade"}:
        messages.append("a migration needs exactly upgrade() and downgrade()")
    if "revision" not in settings:
        messages.append("a migration needs its revision")
    return [Violation(source, 0, message) for message in messages]


def check_repository(root: Path = REPO) -> list[Violation]:
    """Check sql/roles.sql and every migration, as the CI does."""
    problems: list[Violation] = [
        Violation("tools/grant_policy.py", 0, message) for message in grant_policy.check_policy()
    ]
    roles_file = root / "sql" / "roles.sql"
    problems += check_roles_sql(_read(roles_file), _relative(roles_file, root))

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
    problems = [
        f"CREATE TABLE option not allowed: {option}"
        for option in TABLE_OPTIONS
        if getattr(stmt, option, None)
    ]
    if stmt.partspec is not None:
        problems += _partition_spec(stmt)
    for element in stmt.tableElts or ():
        if isinstance(element, pg.ColumnDef):
            problems += _column(element)
        elif isinstance(element, pg.Constraint):
            problems += _constraint(element)
        else:
            problems.append(f"table element not allowed: {type(element).__name__}")
    return problems


def _partition_spec(stmt: pg.CreateStmt) -> list[str]:
    """PARTITION BY RANGE of one timestamptz column, only on the tables of 02 §3.9."""
    relation, spec = stmt.relation, stmt.partspec
    if relation.schemaname != "market" or relation.relname not in PARTITIONED_TABLES:
        return [f"only the twelve market tables of 02 §3.9 are partitioned: {relation.relname}"]
    params = spec.partParams or ()
    if (
        spec.strategy != PartitionStrategy.PARTITION_STRATEGY_RANGE
        or len(params) != 1
        or not params[0].name
    ):
        return ["partition only BY RANGE of one plain column"]
    columns = {
        element.colname: [_string_value(name) for name in element.typeName.names or ()]
        for element in stmt.tableElts or ()
        if isinstance(element, pg.ColumnDef)
    }
    if columns.get(params[0].name, [])[-1:] != ["timestamptz"]:
        return [f"the partition key must be a timestamptz column: {params[0].name}"]
    return []


def _column(column: pg.ColumnDef) -> list[str]:
    problems = [
        f"column {column.colname}: option not allowed: {option}"
        for option in COLUMN_OPTIONS
        if getattr(column, option, None)
    ]
    problems += [f"column {column.colname}: {problem}" for problem in _type(column.typeName)]
    for constraint in column.constraints or ():
        problems += [f"column {column.colname}: {problem}" for problem in _constraint(constraint)]
    return problems


def _type(type_name: pg.TypeName) -> list[str]:
    names = [_string_value(name) for name in type_name.names or ()]
    if not names or (len(names) == 2 and names[0] != "pg_catalog") or len(names) > 2:
        return [f"type not allowed: {'.'.join(map(str, names))}"]
    problems = []
    if names[-1] not in COLUMN_TYPES:
        problems.append(f"type not allowed: {names[-1]}")
    if type_name.setof or type_name.pct_type or type_name.arrayBounds:
        problems.append("SETOF, %TYPE and array types are not allowed")
    for modifier in type_name.typmods or ():
        if not (isinstance(modifier, pg.A_Const) and isinstance(modifier.val, pg.Integer)):
            problems.append("type modifiers must be integer constants")
    return problems


def _constraint(constraint: pg.Constraint) -> list[str]:
    kind = constraint.contype
    if kind in (ConstrType.CONSTR_NOTNULL, ConstrType.CONSTR_NULL):
        return []
    if kind == ConstrType.CONSTR_DEFAULT:
        return _expression(constraint.raw_expr, "DEFAULT", DEFAULT_FUNCTIONS)
    if kind == ConstrType.CONSTR_CHECK:
        return _expression(constraint.raw_expr, "CHECK", frozenset(), CHECK_CALLS)
    if kind in (ConstrType.CONSTR_PRIMARY, ConstrType.CONSTR_UNIQUE):
        return [
            f"{kind.name}: option not allowed: {option}"
            for option in INDEX_OPTIONS
            if getattr(constraint, option, None)
        ]
    if kind == ConstrType.CONSTR_FOREIGN:
        problems = []
        if getattr(constraint.pktable, "schemaname", None) not in PROJECT_SCHEMAS:
            problems.append("foreign keys must reference a table of a project schema")
        if {constraint.fk_upd_action, constraint.fk_del_action} - FOREIGN_KEY_ACTIONS:
            problems.append("foreign keys may only use NO ACTION or RESTRICT")
        if constraint.fk_del_set_cols:
            problems.append("foreign keys may not set columns on delete")
        return problems
    return [f"constraint not allowed: {kind.name}"]


def _expression(
    node: object,
    context: str,
    functions: frozenset[str],
    calls: Mapping[tuple[str, ...], frozenset[str]] | None = None,
) -> list[str]:
    """Check a CHECK or DEFAULT expression against a whitelist of node kinds. ``functions``
    may be called without arguments; ``calls`` with two, the first a constant of its set."""
    problems: list[str] = []
    calls = calls or {}

    def visit(item: object) -> None:
        if item is None or isinstance(item, pg.A_Const):
            return
        if isinstance(item, tuple):
            for element in item:
                visit(element)
        elif isinstance(item, pg.ColumnRef):
            fields = item.fields or ()
            if len(fields) != 1 or not isinstance(fields[0], pg.String):
                problems.append(f"{context}: only plain column names may be referenced")
        elif isinstance(item, pg.A_Expr):
            operator = [_string_value(name) for name in item.name or ()]
            if item.kind not in EXPRESSION_KINDS or len(operator) != 1:
                problems.append(f"{context}: expression kind not allowed: {item.kind.name}")
            elif operator[0] not in EXPRESSION_OPERATORS:
                problems.append(f"{context}: operator not allowed: {operator[0]}")
            visit(item.lexpr)
            visit(item.rexpr)
        elif isinstance(item, pg.BoolExpr):
            visit(item.args)
        elif isinstance(item, pg.NullTest | pg.BooleanTest):
            visit(item.arg)
        elif isinstance(item, pg.TypeCast):
            problems.extend(f"{context}: {problem}" for problem in _type(item.typeName))
            visit(item.arg)
        elif isinstance(item, pg.FuncCall):
            parts = tuple(str(_string_value(part)) for part in item.funcname or ())
            name = ".".join(parts)
            modifiers = (
                item.agg_order
                or item.agg_filter
                or item.over
                or item.agg_star
                or item.agg_distinct
                or item.func_variadic
            )
            constants = calls.get(parts)
            if constants is not None and not modifiers and len(item.args or ()) == 2:
                first = item.args[0]
                if (
                    isinstance(first, pg.A_Const)
                    and isinstance(first.val, pg.String)
                    and first.val.sval in constants
                ):
                    visit(item.args[1:])
                else:
                    problems.append(f"{context}: {name}() needs a first argument in {constants}")
            elif name not in functions or item.args or modifiers:
                problems.append(f"{context} may not call {name}()")
        else:
            problems.append(f"{context}: expression not allowed: {type(item).__name__}")

    visit(node)
    return problems


def _grant_in_database(stmt: pg.GrantStmt) -> list[str]:
    if stmt.objtype == ObjectType.OBJECT_SCHEMA:
        problems = _grant_common(stmt, SCHEMA_PRIVILEGES)
        targets = [(grant_policy.SCHEMA, _string_value(obj)) for obj in stmt.objects or ()]
        if stmt.is_grant:
            for _kind, schema in targets:
                if schema not in PROJECT_SCHEMAS:
                    problems.append(f"grants only on project schemas: {schema}")
    elif stmt.objtype == ObjectType.OBJECT_TABLE:
        problems = _grant_common(stmt, TABLE_PRIVILEGES)
        targets = []
        for obj in stmt.objects or ():
            schema = getattr(obj, "schemaname", None)
            if schema not in PROJECT_SCHEMAS:
                problems.append("table grants need an explicit project schema")
            targets.append((grant_policy.TABLE, f"{schema}.{getattr(obj, 'relname', None)}"))
    elif stmt.objtype == ObjectType.OBJECT_FUNCTION:
        problems = _grant_common(stmt, grant_policy.FUNCTION_PRIVILEGES)
        targets = []
        for obj in stmt.objects or ():
            signature = _function_signature(obj.objname, obj.objargs)
            if signature not in DEFINER_FUNCTIONS:
                problems.append(f"privileges only on the functions of 02 §3.9: {signature}")
            targets.append((grant_policy.FUNCTION, signature))
    else:
        return [f"grants on {stmt.objtype.name} are not allowed"]

    if stmt.is_grant and not problems:
        problems += _grants_outside_the_matrix(stmt, targets)
    return problems


def _grants_outside_the_matrix(stmt: pg.GrantStmt, targets: list[tuple[str, str]]) -> list[str]:
    """Every (role, privilege, object) must be in the exact matrix of tools/grant_policy.py."""
    problems: list[str] = []
    for privilege in stmt.privileges or ():
        for kind, target in targets:
            for grantee in stmt.grantees or ():
                grant = Grant(str(_role_name(grantee)), privilege.priv_name, kind, target)
                if grant in ALLOWED_GRANTS:
                    continue
                reason = section_9_conflict(grant) or "not in the role matrix (grant_policy.py)"
                problems.append(f"grant not allowed: {grant}: {reason}")
    return problems


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


# --- the rebuild migration (REBUILD_MIGRATION only) --------------------------------------


def _partitioned(relation: pg.RangeVar) -> bool:
    return relation.schemaname == "market" and relation.relname in PARTITIONED_TABLES


def _lock(stmt: pg.LockStmt) -> list[str]:
    problems = [] if stmt.mode == ACCESS_EXCLUSIVE else ["LOCK only IN ACCESS EXCLUSIVE MODE"]
    problems += [
        f"LOCK only the twelve partitioned tables: {relation.relname}"
        for relation in stmt.relations or ()
        if not _partitioned(relation)
    ]
    return problems


def _set_lock_timeout(stmt: pg.VariableSetStmt) -> list[str]:
    if stmt.kind != VariableSetKind.VAR_SET_VALUE or not stmt.is_local:
        return ["only SET LOCAL lock_timeout is allowed"]
    return [] if stmt.name == "lock_timeout" else ["only SET LOCAL lock_timeout is allowed"]


def _check_empty(stmt: pg.AlterTableStmt) -> list[str]:
    """ALTER TABLE only to add a validated CHECK (false): it fails if the table has a row."""
    if not _partitioned(stmt.relation) or stmt.objtype != ObjectType.OBJECT_TABLE:
        return [f"ALTER TABLE only on the twelve partitioned tables: {stmt.relation.relname}"]
    commands = stmt.cmds or ()
    command = commands[0] if len(commands) == 1 else None
    constraint = getattr(command, "def_", None)
    if (
        command is None
        or command.subtype != AlterTableType.AT_AddConstraint
        or not isinstance(constraint, pg.Constraint)
        or constraint.contype != ConstrType.CONSTR_CHECK
        or constraint.skip_validation
        or not _is_false(constraint.raw_expr)
    ):
        return ["ALTER TABLE may only add a validated CHECK (false), to prove the table empty"]
    return []


def _is_false(node: object) -> bool:
    if isinstance(node, pg.TypeCast):  # older grammars: 'f'::bool
        node = node.arg
        return isinstance(node, pg.A_Const) and _string_value(node.val) in {"f", "false"}
    return (
        isinstance(node, pg.A_Const)
        and isinstance(node.val, pg.Boolean)
        and node.val.boolval is False
    )


def _drop_partitioned(stmt: pg.DropStmt) -> list[str]:
    if stmt.removeType != ObjectType.OBJECT_TABLE:
        return ["DROP only tables"]
    problems = []
    if stmt.behavior != DropBehavior.DROP_RESTRICT:
        problems.append("DROP ... CASCADE is not allowed")
    if stmt.missing_ok:
        problems.append("DROP ... IF EXISTS is not allowed")
    for obj in stmt.objects or ():
        names = [_string_value(name) for name in obj]
        if len(names) != 2 or names[0] != "market" or names[1] not in PARTITIONED_TABLES:
            problems.append(f"DROP only the twelve partitioned tables: {'.'.join(map(str, names))}")
    return problems


# --- SECURITY DEFINER functions (02 §9 and §3.9) ------------------------------------------


def _check_definer_functions(text: str, statements: Any, source: str) -> list[Violation]:
    """Each function created is one of the three of 02 §3.9, hardened, with only approved
    dynamic statements, and revoked from PUBLIC in the same migration."""
    problems: list[Violation] = []
    created: dict[str, int] = {}
    revoked: set[str] = set()
    for number, raw in enumerate(statements, start=1):
        stmt = raw.stmt
        if isinstance(stmt, pg.CreateFunctionStmt):
            # pglast gives the statement's place in characters of the text, not in bytes.
            start = raw.stmt_location or 0
            end = start + raw.stmt_len if raw.stmt_len else len(text)
            signature = _function_signature(stmt.funcname, stmt.parameters)
            created.setdefault(signature, number)
            statement = text[start:end]
            problems += [
                Violation(source, number, message)
                for message in _create_function(stmt, signature, statement)
            ]
        elif (
            isinstance(stmt, pg.GrantStmt)
            and not stmt.is_grant
            and not stmt.grant_option  # REVOKE GRANT OPTION FOR keeps EXECUTE
            and stmt.objtype == ObjectType.OBJECT_FUNCTION
            and stmt.privileges is None
            and any(g.roletype == RoleSpecType.ROLESPEC_PUBLIC for g in stmt.grantees or ())
        ):
            revoked |= {_function_signature(o.objname, o.objargs) for o in stmt.objects or ()}
    for signature, number in created.items():
        if signature not in revoked:
            problems.append(
                Violation(source, number, f"{signature}: REVOKE ALL FROM PUBLIC missing")
            )
    return problems


def _create_function(stmt: pg.CreateFunctionStmt, signature: str, statement: str) -> list[str]:
    if signature not in DEFINER_FUNCTIONS:
        return [f"function not allowed: {signature} (only those of 02 §3.9)"]
    problems = []
    if stmt.replace or stmt.is_procedure:
        problems.append(f"{signature}: CREATE OR REPLACE and procedures are not allowed")
    language = security = None
    settings: dict[str, tuple[str, ...]] = {}
    for option in stmt.options or ():
        if option.defname == "language":
            language = _string_value(option.arg)
        elif option.defname == "security":
            security = getattr(option.arg, "boolval", None)
        elif (
            option.defname == "set"
            and isinstance(option.arg, pg.VariableSetStmt)
            and option.arg.kind == VariableSetKind.VAR_SET_VALUE
        ):
            settings[option.arg.name.lower()] = tuple(
                str(_string_value(arg.val)) for arg in option.arg.args or ()
            )
        elif option.defname != "as":
            problems.append(f"{signature}: function option not allowed: {option.defname}")
    if language != "plpgsql":
        problems.append(f"{signature}: LANGUAGE plpgsql only")
    if security is not True:
        problems.append(f"{signature}: must be SECURITY DEFINER")
    if settings != DEFINER_SETTINGS:
        problems.append(f"{signature}: its SET must be exactly {DEFINER_SETTINGS}")
    body = next((option.arg for option in stmt.options or () if option.defname == "as"), None)
    source = _string_value(body[0]) if isinstance(body, tuple) and body else None
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest() if source is not None else None
    if digest is None or digest != _approved_bodies().get(signature):
        problems.append(f"{signature}: its body is not the approved one ({APPROVED_BODIES.name})")
    return problems + [f"{signature}: {message}" for message in _definer_body(statement)]


@functools.cache
def _approved_bodies() -> dict[str, str]:
    entries = json.loads(APPROVED_BODIES.read_text(encoding="utf-8"))
    return {entry["signature"]: entry["body_sha256"] for entry in entries}


def _definer_body(statement: str) -> list[str]:
    try:
        tree = json.loads(parse_plpgsql_json(statement))
    except (ParseError, ValueError) as exc:
        return [f"its PL/pgSQL body does not parse: {exc}"]
    problems: list[str] = []
    for kind, node in _plpgsql_statements(tree):
        if kind in DEFINER_FORBIDDEN:
            problems.append(f"{kind} is not allowed")
        elif kind == "PLpgSQL_stmt_dynexecute":
            problems += _dynamic_statement(node.get("query"))
        elif kind == "PLpgSQL_stmt_execsql":
            problems += _static_statement(node.get("sqlstmt"))
        elif kind == "PLpgSQL_stmt_perform":
            problems += _perform(node.get("expr"))
    return problems


def _perform(expr: Any) -> list[str]:
    """PERFORM only takes the advisory lock: pg_advisory_xact_lock(<integer constant>)."""
    query = _expression_text(expr)
    try:
        statements = parse_sql(query) if query else ()
    except ParseError:
        statements = ()
    select = statements[0].stmt if len(statements) == 1 else None
    targets = select.targetList if isinstance(select, pg.SelectStmt) else None
    call = targets[0].val if targets and len(targets) == 1 else None
    if (
        isinstance(select, pg.SelectStmt)
        and not (select.fromClause or select.withClause or select.whereClause)
        and isinstance(call, pg.FuncCall)
        and [_string_value(part) for part in call.funcname or ()] == ["pg_advisory_xact_lock"]
        and len(call.args or ()) == 1
        and isinstance(call.args[0], pg.A_Const)
        and isinstance(call.args[0].val, pg.Integer)
    ):
        return []
    return [f"PERFORM may only take the advisory lock: {query!r}"]


def _plpgsql_statements(node: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key.startswith("PLpgSQL_stmt_") and isinstance(value, dict):
                yield key, value
            yield from _plpgsql_statements(value)
    elif isinstance(node, list):
        for item in node:
            yield from _plpgsql_statements(item)


def _expression_text(expr: Any) -> str | None:
    inner = expr.get("PLpgSQL_expr") if isinstance(expr, dict) else None
    query = inner.get("query") if isinstance(inner, dict) else None
    return query if isinstance(query, str) else None


def _dynamic_statement(expr: Any) -> list[str]:
    """EXECUTE format('<approved template>', ...): nothing else builds a statement."""
    query = _expression_text(expr)
    try:
        statements = parse_sql(f"SELECT {query}") if query else ()
    except ParseError:
        statements = ()
    select = statements[0].stmt if len(statements) == 1 else None
    targets = select.targetList if isinstance(select, pg.SelectStmt) else None
    call = targets[0].val if targets and len(targets) == 1 else None
    if (
        not isinstance(call, pg.FuncCall)
        or [_string_value(part) for part in call.funcname or ()] != ["format"]
        or not call.args
        or not isinstance(call.args[0], pg.A_Const)
        or not isinstance(call.args[0].val, pg.String)
    ):
        return [f"EXECUTE must build its statement with format() and a template: {query!r}"]
    template = call.args[0].val.sval
    return [] if template in DYNAMIC_TEMPLATES else [f"EXECUTE template not approved: {template!r}"]


def _static_statement(expr: Any) -> list[str]:
    """Reads, and the function's own bookkeeping rows, inserted with VALUES."""
    query = _expression_text(expr)
    try:
        statements = parse_sql(query) if query else ()
    except ParseError:
        return [f"statement does not parse: {query!r}"]
    problems = []
    for raw in statements:
        stmt = raw.stmt
        ctes = stmt.withClause.ctes if getattr(stmt, "withClause", None) else ()
        if any(not isinstance(cte.ctequery, pg.SelectStmt) for cte in ctes or ()):
            problems.append(f"WITH may not modify data in a SECURITY DEFINER function: {query!r}")
            continue
        if isinstance(stmt, pg.SelectStmt) and stmt.intoClause is None:
            continue
        if (
            isinstance(stmt, pg.InsertStmt)
            and (stmt.relation.schemaname, stmt.relation.relname) in DEFINER_INSERTS
            and not (stmt.onConflictClause or stmt.withClause or stmt.returningClause)
        ):
            continue
        problems.append(f"statement not allowed in a SECURITY DEFINER function: {query!r}")
    return problems if statements else [f"empty statement: {query!r}"]


def _function_signature(names: Any, arguments: Any) -> str:
    qualified = ".".join(str(_string_value(name)) for name in names or ())
    types = []
    for argument in arguments or ():
        type_name = argument.argType if isinstance(argument, pg.FunctionParameter) else argument
        parts = [_string_value(name) for name in getattr(type_name, "names", None) or ()]
        types.append(str(parts[-1]) if parts else "?")
    return f"{qualified}({', '.join(types)})"


def _check_migration_function(node: ast.FunctionDef) -> list[str]:
    messages: list[str] = []
    arguments = node.args
    if (
        node.decorator_list
        or arguments.posonlyargs
        or arguments.args
        or arguments.vararg
        or arguments.kwonlyargs
        or arguments.kwarg
    ):
        messages.append(f"{node.name}() must take no arguments and have no decorators")

    body = node.body[1:] if node.body and _is_docstring(node.body[0]) else node.body
    if node.name == "upgrade":
        if (
            len(body) != 1
            or not isinstance(body[0], ast.Expr)
            or ast.dump(body[0].value) != SIBLING_SQL_CALL
        ):
            messages.append(
                'upgrade() may only call execute_sql_file(Path(__file__).with_suffix(".sql"))'
            )
    elif not (
        len(body) == 1
        and isinstance(body[0], ast.Raise)
        and isinstance(body[0].exc, ast.Call)
        and isinstance(body[0].exc.func, ast.Name)
        and body[0].exc.func.id == "NotImplementedError"
        and all(isinstance(arg, ast.Constant) for arg in body[0].exc.args)
        and not body[0].exc.keywords
    ):
        messages.append("downgrade() may only raise NotImplementedError")
    return messages


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_constant_setting(value: ast.expr) -> bool:
    if isinstance(value, ast.Constant):
        return value.value is None or isinstance(value.value, str)
    if isinstance(value, ast.Tuple | ast.List):
        return all(
            isinstance(item, ast.Constant) and isinstance(item.value, str) for item in value.elts
        )
    return False


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
