"""Acceptance test A7: the live risk limits match the DM and no service can change them.

The change attempts run in real sessions of every login role, not by inspecting grants:
writing rows, altering or dropping the table, and becoming its owner are all refused.

What protects the limits is the privileges: only kbot_owner owns the table, it cannot log
in, and the migrations can only INSERT into it (tools/sql_guard.py). The stored hash adds
detection, and it is only meaningful against a reference outside the database: the DM text
in the CI, and in phase 4 the fingerprint that svc-risk checks at start-up (02 §4.2).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row

from kbot.db.roles import LOGIN_ROLES
from kbot.limits import RiskLimits, parse_dm_limits

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]

LIMIT_FIELDS = tuple(RiskLimits.model_fields)

CHANGES = [
    "INSERT INTO config.risk_limits (version, initial_capital_usdt, max_risk_per_trade_pct,"
    " max_daily_loss_pct, max_futures_leverage, max_drawdown_pct, stop_loss_required,"
    " futures_margin_mode, averaging_down_forbidden, limits_hash)"
    " VALUES (2, 10000, 5, 2, 2, 10, true, 'isolated', true, repeat('0', 64))",
    "UPDATE config.risk_limits SET max_futures_leverage = 10",
    "DELETE FROM config.risk_limits",
    "TRUNCATE config.risk_limits",
    "ALTER TABLE config.risk_limits ADD COLUMN note text",
    "DROP TABLE config.risk_limits",
    "SET ROLE kbot_owner",
]


def test_a7_live_limits_match_the_dm_and_their_hash(
    admin: psycopg.Connection, repo_root: Path
) -> None:
    with admin.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            "SELECT * FROM config.risk_limits ORDER BY version DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    live = RiskLimits(**{field: row[field] for field in LIMIT_FIELDS})
    dm_text = (repo_root / "docs" / "especificacion" / "01_Documento_Maestro.md").read_text(
        encoding="utf-8"
    )
    assert live == parse_dm_limits(dm_text)
    assert row["limits_hash"] == live.fingerprint()


@pytest.mark.parametrize("role", ["kbot_risk", "kbot_watchdog", "kbot_backup"])
def test_a7_readers_can_read(connect: Connect, role: str) -> None:
    with connect(role) as conn:
        assert conn.execute("SELECT count(*) FROM config.risk_limits").fetchone() == (1,)


@pytest.mark.parametrize("statement", CHANGES)
@pytest.mark.parametrize("role", LOGIN_ROLES)
def test_a7_no_login_role_can_change_the_limits(
    connect: Connect, role: str, statement: str
) -> None:
    with connect(role) as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(statement)


def test_a7_the_limits_are_unchanged_after_the_attempts(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "SELECT version, max_futures_leverage FROM config.risk_limits ORDER BY version"
    ).fetchall()
    assert [(version, str(leverage)) for version, leverage in rows] == [(1, "2")]
