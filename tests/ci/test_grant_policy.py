"""Grant policy derived from the role table of spec 02 §9 (reviewer finding 1)."""

from __future__ import annotations

import pytest

from tools.grant_policy import (
    ALLOWED_GRANTS,
    SCHEMA,
    TABLE,
    Grant,
    check_policy,
    section_9_conflict,
)


def test_the_exact_matrix_respects_section_9() -> None:
    assert check_policy() == []


@pytest.mark.parametrize(
    "grant",
    [
        Grant("kbot_ingest", "usage", SCHEMA, "trading"),
        Grant("kbot_ingest", "insert", TABLE, "trading.signals"),
        Grant("kbot_ingest", "select", TABLE, "news.items"),
        Grant("kbot_ingest", "select", TABLE, "config.risk_limits"),
        Grant("kbot_news", "select", TABLE, "trading.intents"),
        Grant("kbot_strategy", "insert", TABLE, "trading.approved_orders"),
        Grant("kbot_broker", "insert", TABLE, "trading.risk_decisions"),
        Grant("kbot_web", "insert", TABLE, "trading.risk_reservations"),
        Grant("kbot_web", "insert", TABLE, "config.risk_limits"),
        Grant("kbot_risk", "insert", TABLE, "config.risk_limits"),
        Grant("kbot_risk", "insert", TABLE, "trading.paper_fills"),
        Grant("kbot_risk", "insert", TABLE, "ledger.postings"),
        Grant("kbot_telegram", "insert", TABLE, "trading.signals"),
        Grant("kbot_backup", "select", TABLE, "market.candles"),
        Grant("kbot_owner", "usage", SCHEMA, "config"),
    ],
)
def test_section_9_forbids(grant: Grant) -> None:
    assert section_9_conflict(grant), grant


@pytest.mark.parametrize(
    "grant",
    [
        Grant("kbot_ingest", "insert", TABLE, "market.candles"),
        Grant("kbot_news", "insert", TABLE, "ai.calls"),
        Grant("kbot_strategy", "insert", TABLE, "trading.signals"),
        Grant("kbot_risk", "insert", TABLE, "trading.approved_orders"),
        Grant("kbot_watchdog", "insert", TABLE, "ops.halt_flags"),
        Grant("kbot_telegram", "insert", TABLE, "ops.admin_requests"),
    ],
)
def test_section_9_allows_what_the_table_grants(grant: Grant) -> None:
    assert section_9_conflict(grant) is None, grant


@pytest.mark.parametrize(
    "bad",
    [
        Grant("kbot_ingest", "usage", SCHEMA, "trading"),
        Grant("kbot_risk", "update", TABLE, "config.risk_limits"),
        Grant("kbot_risk", "usage", SCHEMA, "config.risk_limits"),
        Grant("kbot_risk", "select", TABLE, "evil.table"),
    ],
)
def test_a_bad_entry_in_the_matrix_is_reported(bad: Grant) -> None:
    assert check_policy(ALLOWED_GRANTS | {bad})
