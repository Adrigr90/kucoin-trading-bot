"""Reference data: instruments, risk tiers and funding rates (02 §3.2 and §3.8; D04).

Each payload is read with the strict contracts of ``kbot.kucoin.models``. Anything that
does not match is refused (None), never stored half-read: fail closed (02 §0 rule 1).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from kbot.kucoin.models import (
    FundingHistory,
    FundingRate,
    FuturesInstrument,
    RiskTier,
    SpotInstrument,
)
from kbot.market.timeutil import as_utc, from_milliseconds

TRADING = {"SPOT": "TradingEnabled", "FUTURES": "Open"}


@dataclass(frozen=True)
class InstrumentRow:
    market: str
    symbol: str
    base_currency: str
    quote_currency: str
    settle_currency: str | None
    tick_size: Decimal
    quantity_step: Decimal
    multiplier: Decimal
    min_quantity: Decimal | None
    min_funds: Decimal | None
    trading_status: str
    is_trading: bool
    raw: Mapping[str, Any]

    def key_fields(self) -> tuple[object, ...]:
        """What makes a new row worth storing: every field except the raw answer, whose
        volatile values (for example price limits) change all the time."""
        return (
            self.base_currency,
            self.quote_currency,
            self.settle_currency,
            self.tick_size,
            self.quantity_step,
            self.multiplier,
            self.min_quantity,
            self.min_funds,
            self.trading_status,
            self.is_trading,
        )


@dataclass(frozen=True)
class RiskTierRow:
    symbol: str
    level: int
    min_risk_limit: Decimal
    max_risk_limit: Decimal
    max_leverage: int
    initial_margin: Decimal
    maintain_margin: Decimal


@dataclass(frozen=True)
class FundingRow:
    symbol: str
    kind: str  # "liquidado" or "previsto"
    funding_time: datetime
    observed_at: datetime
    rate: Decimal
    granularity_ms: int | None
    rate_cap: Decimal | None
    rate_floor: Decimal | None


def instrument_row(market: str, raw: Mapping[str, Any]) -> InstrumentRow | None:
    """One V2 instrument, or None if it does not match its contract."""
    try:
        if market == "SPOT":
            spot = SpotInstrument.model_validate(raw)
            return InstrumentRow(
                market=market,
                symbol=spot.symbol,
                base_currency=spot.baseCurrency,
                quote_currency=spot.quoteCurrency,
                settle_currency=None,
                tick_size=spot.tickSize,
                quantity_step=spot.baseOrderStep,
                multiplier=Decimal(1),
                min_quantity=spot.minBaseOrderSize,
                min_funds=spot.minFunds,
                trading_status=spot.tradingStatus,
                is_trading=spot.tradingStatus == TRADING[market],
                raw=raw,
            )
        if market == "FUTURES":
            futures = FuturesInstrument.model_validate(raw)
            return InstrumentRow(
                market=market,
                symbol=futures.symbol,
                base_currency=futures.baseCurrency,
                quote_currency=futures.quoteCurrency,
                settle_currency=futures.settlementCurrency,
                tick_size=futures.tickSize,
                quantity_step=futures.lotSize,
                multiplier=futures.unitSize,
                min_quantity=None,
                min_funds=None,
                trading_status=futures.tradingStatus,
                is_trading=futures.tradingStatus == TRADING[market],
                raw=raw,
            )
    except ValidationError:
        return None
    raise ValueError(f"unknown market: {market!r}")


def risk_tier_rows(symbol: str, payload: Any) -> list[RiskTierRow] | None:
    """Classic risk tiers of one contract: all of them, contiguous, or None."""
    if not isinstance(payload, list) or not payload:
        return None
    try:
        tiers = [RiskTier.model_validate(item) for item in payload]
    except ValidationError:
        return None
    tiers.sort(key=lambda tier: tier.level)
    if {t.symbol for t in tiers} != {symbol} or [t.level for t in tiers] != list(
        range(1, len(tiers) + 1)
    ):
        return None
    for lower, upper in itertools.pairwise(tiers):
        if upper.minRiskLimit != lower.maxRiskLimit:
            return None
    return [
        RiskTierRow(
            symbol=t.symbol,
            level=t.level,
            min_risk_limit=t.minRiskLimit,
            max_risk_limit=t.maxRiskLimit,
            max_leverage=t.maxLeverage,
            initial_margin=t.initialMargin,
            maintain_margin=t.maintainMargin,
        )
        for t in tiers
    ]


def predicted_funding(payload: Any, observed_at: datetime) -> list[FundingRow] | None:
    """The V2 current funding rate (``nextFundingRate``): the predicted rate of the next
    settlement, kept for every observation."""
    observed_at = as_utc(observed_at, "observed_at")
    if not isinstance(payload, list):
        return None
    try:
        rates = [FundingRate.model_validate(item) for item in payload]
    except ValidationError:
        return None
    return [
        FundingRow(
            symbol=rate.symbol,
            kind="previsto",
            funding_time=from_milliseconds(rate.fundingTime),
            observed_at=observed_at,
            rate=rate.nextFundingRate,
            granularity_ms=rate.currentGranularity,
            rate_cap=rate.fundingRateCap,
            rate_floor=rate.fundingRateFloor,
        )
        for rate in rates
    ]


def settled_funding(payload: Any) -> list[FundingRow] | None:
    """The V2 funding history: settled rates, one row per settlement time."""
    try:
        history = FundingHistory.model_validate(payload)
    except ValidationError:
        return None
    rows = []
    for point in history.list:
        moment = from_milliseconds(point.ts)
        rows.append(
            FundingRow(
                symbol=history.symbol,
                kind="liquidado",
                funding_time=moment,
                observed_at=moment,
                rate=point.fundingRate,
                granularity_ms=None,
                rate_cap=None,
                rate_floor=None,
            )
        )
    return rows


def index_points(payload: Any, index_symbol: str) -> list[tuple[int, Decimal]] | None:
    """Per-second values of a Classic index page (``/api/v1/index/query``), or None."""
    if not isinstance(payload, dict) or not isinstance(payload.get("dataList"), list):
        return None
    points: list[tuple[int, Decimal]] = []
    for item in payload["dataList"]:
        if not isinstance(item, dict) or item.get("symbol") != index_symbol:
            return None
        moment, value = item.get("timePoint"), item.get("value")
        if isinstance(moment, bool) or not isinstance(moment, int):
            return None
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            return None
        points.append((moment, Decimal(value)))
    return points


def changed_instruments(
    rows: Iterable[InstrumentRow], latest: Mapping[tuple[str, str], tuple[object, ...]]
) -> list[InstrumentRow]:
    """Rows whose business fields differ from the latest stored ones (or are new)."""
    return [row for row in rows if latest.get((row.market, row.symbol)) != row.key_fields()]
