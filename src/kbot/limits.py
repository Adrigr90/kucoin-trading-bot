"""Risk limits of the Master Document (DM §6), their parser and their fingerprint.

The limits live in ``config.risk_limits`` and only ``kbot_owner`` can write them, through
migrations (spec 02 §6.1). The CI compares them with the literal text of the DM, and
``svc-risk`` will refuse entries when the stored fingerprint does not match (02 §4.2).
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from kbot.core.jcs import canonicalize


class DmParseError(ValueError):
    """The DM risk section does not contain exactly the expected statements."""


class RiskLimits(BaseModel):
    """Hard risk limits. Values are exact decimals; percentages are kept as in the DM."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    initial_capital_usdt: Decimal
    max_risk_per_trade_pct: Decimal
    max_daily_loss_pct: Decimal
    max_futures_leverage: Decimal
    max_drawdown_pct: Decimal
    stop_loss_required: bool
    futures_margin_mode: Literal["isolated"]
    averaging_down_forbidden: bool

    def fingerprint(self) -> str:
        """SHA-256 (hex) of the RFC 8785 canonical JSON of the limits."""
        return hashlib.sha256(canonicalize(self.model_dump())).hexdigest()


_NUMBER = r"([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]+)?)"

_VALUE_LINES = {
    "initial_capital_usdt": rf"- Capital ficticio: {_NUMBER} USDT\.",
    "max_risk_per_trade_pct": rf"- Riesgo máximo planificado por operación: {_NUMBER} %\.",
    "max_daily_loss_pct": rf"- Pérdida diaria máxima: {_NUMBER} %\.",
    "max_futures_leverage": rf"- Apalancamiento máximo Futures: {_NUMBER}x\.",
    "max_drawdown_pct": rf"- Drawdown máximo: {_NUMBER} %\.",
}

_RULE_LINES = {
    "stop_loss_required": r"- Stop-loss obligatorio en todas las operaciones\.",
    "isolated_margin": (
        r"El apalancamiento inicial deberá utilizar margen aislado cuando corresponda\."
    ),
    "averaging_down_forbidden": (
        r"Queda prohibido aumentar automáticamente posiciones perdedoras"
        r" para recuperar pérdidas\."
    ),
}


def parse_dm_limits(dm_text: str) -> RiskLimits:
    """Read the limits from the literal text of the DM risk section (DM §6)."""
    lines = [line.strip() for line in _risk_section(dm_text).splitlines()]

    values: dict[str, Decimal] = {}
    for field, pattern in _VALUE_LINES.items():
        matches = [m for line in lines if (m := re.fullmatch(pattern, line))]
        if len(matches) != 1:
            raise DmParseError(f"expected exactly one DM line for {field}, found {len(matches)}")
        values[field] = _spanish_number(matches[0].group(1))

    for rule, pattern in _RULE_LINES.items():
        count = sum(1 for line in lines if re.fullmatch(pattern, line))
        if count != 1:
            raise DmParseError(f"expected exactly one DM statement for {rule}, found {count}")

    return RiskLimits(
        **values,
        stop_loss_required=True,
        futures_margin_mode="isolated",
        averaging_down_forbidden=True,
    )


def _risk_section(dm_text: str) -> str:
    match = re.search(
        r"^## 6\. Gestión del riesgo\s*$(.*?)(?=^## \d+\.|\Z)", dm_text, re.MULTILINE | re.DOTALL
    )
    if match is None:
        raise DmParseError("DM section '## 6. Gestión del riesgo' not found")
    return match.group(1)


def _spanish_number(text: str) -> Decimal:
    """'10.000' -> 10000 and '0,5' -> 0.5 (dot for thousands, comma for decimals)."""
    return Decimal(text.replace(".", "").replace(",", "."))
