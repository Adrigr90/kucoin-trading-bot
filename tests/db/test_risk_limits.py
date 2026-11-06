"""Acceptance test A7: the live risk limits match the DM and no service can change them.

The write attempts run in real sessions of service roles, not by inspecting grants.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row

from kbot.db.roles import SERVICE_ROLES
from kbot.limits import RiskLimits, parse_dm_limits

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]

LIMIT_FIELDS = tuple(RiskLimits.model_fields)

WRITES = [
    "INSERT INTO config.risk_limits (version, initial_capital_usdt, max_risk_per_trade_pct,"
    " max_daily_loss_pct, max_futures_leverage, max_drawdown_pct, stop_loss_required,"
    " futures_margin_mode, averaging_down_forbidden, limits_hash)"
    " VALUES (2, 10000, 5, 2, 2, 10, true, 'isolated', true, repeat('0', 64))",
    "UPDATE config.risk_limits SET max_futures_leverage = 10",
    "DELETE FROM config.risk_limits",
    "TRUNCATE config.risk_limits",
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


def test_a7_readers_can_read(connect: Connect) -> None:
    for role in ("kbot_risk", "kbot_watchdog"):
        with connect(role) as conn:
            count = conn.execute("SELECT count(*) FROM config.risk_limits").fetchone()
            assert count == (1,)


@pytest.mark.parametrize("statement", WRITES)
@pytest.mark.parametrize("role", SERVICE_ROLES)
def test_a7_no_service_can_change_the_limits(connect: Connect, role: str, statement: str) -> None:
    with connect(role) as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(statement)


def test_a7_the_backup_role_cannot_write(connect: Connect) -> None:
    with connect("kbot_backup") as conn:
        assert conn.execute("SELECT count(*) FROM config.risk_limits").fetchone() == (1,)
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(WRITES[1])
