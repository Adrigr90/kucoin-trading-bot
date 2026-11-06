"""Compare the risk limits written by the migrations with the literal DM text (spec 02 §6.1).

This check needs no database: it reads the INSERT statements into ``config.risk_limits``
with the PostgreSQL 18 grammar. The CI also compares the live table (tests/db).

Usage: ``python -m tools.check_limits`` from the repository root.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from pglast import ast as pg
from pglast import parse_sql

from kbot.limits import RiskLimits, parse_dm_limits

REPO = Path(__file__).resolve().parents[1]
DM_FILE = REPO / "docs" / "especificacion" / "01_Documento_Maestro.md"
MIGRATIONS_DIR = REPO / "migrations" / "versions"


class LimitsSqlError(ValueError):
    """The migrations do not contain a readable risk limits row."""


def migration_limits(sql_texts: list[str]) -> tuple[int, RiskLimits, str]:
    """Return (version, limits, stored hash) of the highest version inserted."""
    rows: list[dict[str, object]] = []
    for text in sql_texts:
        for raw in parse_sql(text):
            stmt = raw.stmt
            if not isinstance(stmt, pg.InsertStmt):
                continue
            relation = stmt.relation
            if (relation.schemaname, relation.relname) != ("config", "risk_limits"):
                continue
            columns = [target.name for target in stmt.cols or ()]
            for values in stmt.selectStmt.valuesLists:
                if len(values) != len(columns):
                    raise LimitsSqlError("column and value counts differ")
                rows.append(dict(zip(columns, map(_constant, values), strict=True)))
    if not rows:
        raise LimitsSqlError("no INSERT into config.risk_limits found")

    latest = max(rows, key=lambda row: _int(row["version"]))
    stored_hash = latest.pop("limits_hash")
    version = _int(latest.pop("version"))
    if not isinstance(stored_hash, str):
        raise LimitsSqlError("limits_hash must be text")
    limits = RiskLimits(
        initial_capital_usdt=_decimal(latest.pop("initial_capital_usdt")),
        max_risk_per_trade_pct=_decimal(latest.pop("max_risk_per_trade_pct")),
        max_daily_loss_pct=_decimal(latest.pop("max_daily_loss_pct")),
        max_futures_leverage=_decimal(latest.pop("max_futures_leverage")),
        max_drawdown_pct=_decimal(latest.pop("max_drawdown_pct")),
        stop_loss_required=latest.pop("stop_loss_required"),
        futures_margin_mode=latest.pop("futures_margin_mode"),
        averaging_down_forbidden=latest.pop("averaging_down_forbidden"),
    )
    if latest:
        raise LimitsSqlError(f"unexpected columns: {sorted(latest)}")
    return version, limits, stored_hash


def compare(dm_text: str, sql_texts: list[str]) -> list[str]:
    """Return every difference between the DM, the migration values and the stored hash."""
    expected = parse_dm_limits(dm_text)
    _, actual, stored_hash = migration_limits(sql_texts)
    problems = [
        f"{field}: migration has {getattr(actual, field)!r}, DM says {value!r}"
        for field, value in expected
        if getattr(actual, field) != value
    ]
    if stored_hash != actual.fingerprint():
        problems.append("limits_hash does not match the migration values")
    if stored_hash != expected.fingerprint():
        problems.append("limits_hash does not match the DM values")
    return problems


def main() -> int:
    sql_texts = [p.read_text(encoding="utf-8") for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    problems = compare(DM_FILE.read_text(encoding="utf-8"), sql_texts)
    for problem in problems:
        print(f"MISMATCH {problem}")
    if problems:
        return 1
    print("check_limits: the migration limits match the DM and their hash")
    return 0


def _constant(node: pg.Node) -> object:
    if not isinstance(node, pg.A_Const) or node.isnull:
        raise LimitsSqlError("risk limit values must be non-null constants")
    value = node.val
    if isinstance(value, pg.Boolean):
        return value.boolval
    if isinstance(value, pg.Integer):
        return value.ival
    if isinstance(value, pg.Float):
        return Decimal(value.fval)
    if isinstance(value, pg.String):
        return value.sval
    raise LimitsSqlError(f"unsupported constant: {type(value).__name__}")


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise LimitsSqlError(f"expected a number, got {value!r}")
    return Decimal(value)


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LimitsSqlError(f"expected an integer, got {value!r}")
    return value


if __name__ == "__main__":
    sys.exit(main())
