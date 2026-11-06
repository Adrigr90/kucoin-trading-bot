"""DM risk limits: parser and fingerprint (spec 02 §6.1, §4.2)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from kbot.limits import DmParseError, RiskLimits, parse_dm_limits

DM_LIMITS = RiskLimits(
    initial_capital_usdt=Decimal("10000"),
    max_risk_per_trade_pct=Decimal("0.5"),
    max_daily_loss_pct=Decimal("2"),
    max_futures_leverage=Decimal("2"),
    max_drawdown_pct=Decimal("10"),
    stop_loss_required=True,
    futures_margin_mode="isolated",
    averaging_down_forbidden=True,
)


@pytest.fixture(scope="module")
def dm_text(repo_root: Path) -> str:
    return (repo_root / "docs" / "especificacion" / "01_Documento_Maestro.md").read_text(
        encoding="utf-8"
    )


def test_the_dm_limits_are_read_literally(dm_text: str) -> None:
    assert parse_dm_limits(dm_text) == DM_LIMITS


def test_fingerprint_is_sha256_of_the_canonical_json() -> None:
    assert DM_LIMITS.fingerprint() == (
        "a9c038cb25b193017be417fa0915ae6076f3d94c26b2fac7c0a8d3697ff57ec1"
    )


def test_any_value_change_changes_the_fingerprint() -> None:
    changed = DM_LIMITS.model_copy(update={"max_futures_leverage": Decimal("3")})
    assert changed.fingerprint() != DM_LIMITS.fingerprint()


@pytest.mark.parametrize(
    ("old", "new", "field", "value"),
    [
        (
            "Apalancamiento máximo Futures: 2x.",
            "Apalancamiento máximo Futures: 3x.",
            "max_futures_leverage",
            Decimal("3"),
        ),
        (
            "Capital ficticio: 10.000 USDT.",
            "Capital ficticio: 12.500,5 USDT.",
            "initial_capital_usdt",
            Decimal("12500.5"),
        ),
    ],
)
def test_a_changed_dm_gives_other_limits(
    dm_text: str, old: str, new: str, field: str, value: Decimal
) -> None:
    limits = parse_dm_limits(dm_text.replace(old, new))
    assert getattr(limits, field) == value
    assert limits != DM_LIMITS


@pytest.mark.parametrize(
    "line",
    [
        "- Drawdown máximo: 10 %.",
        "- Stop-loss obligatorio en todas las operaciones.",
        "El apalancamiento inicial deberá utilizar margen aislado cuando corresponda.",
        "Queda prohibido aumentar automáticamente posiciones perdedoras para recuperar pérdidas.",
    ],
)
def test_a_missing_or_duplicated_dm_statement_is_an_error(dm_text: str, line: str) -> None:
    assert line in dm_text
    with pytest.raises(DmParseError):
        parse_dm_limits(dm_text.replace(line, ""))
    with pytest.raises(DmParseError):
        parse_dm_limits(dm_text.replace(line, f"{line}\n{line}"))


def test_limits_reject_floats_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        DM_LIMITS.model_validate({**DM_LIMITS.model_dump(), "max_drawdown_pct": 10.0})
    with pytest.raises(ValidationError):
        DM_LIMITS.model_validate({**DM_LIMITS.model_dump(), "extra": True})
