"""Provisional universe of instruments (02 §3.3 and §3.8; D12 not approved).

Rule: USDT pairs that are trading (in Futures, linear contracts settled in USDT, 02 §0),
ordered by their 24-hour volume in the quote currency; the first 20 of each market form
the universe and the first 10 are the depth-50 candidates (P3). Stablecoins as base
currency are left out (owner decision of 7 November 2026). Ties go to the symbol in
alphabetical order, so the result does not depend on the order of the answer. A pair
without a usable book (no bid or ask, or a crossed one) is left out: its spread is unknown.
Every evaluation is stored in ``universe_snapshots`` with the version of the criteria.

Inputs are the V2 instrument and ticker lists, which KuCoin returns for every symbol of a
market in one request each (weights 4 and 15, measured on 7 November 2026).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from kbot.market.mark_candles import positive_price
from kbot.market.timeutil import as_utc

UNIVERSE_SIZE = 20
L50_SIZE = 10
TRADING = {"SPOT": "TradingEnabled", "FUTURES": "Open"}
CRITERIA_VERSION = "provisional-1"
# Owner decision of 7 November 2026: stablecoins are not traded against USDT.
STABLECOINS = frozenset({"USDC", "USDE", "FDUSD", "DAI", "TUSD", "PYUSD"})


@dataclass(frozen=True)
class UniverseEntry:
    evaluated_at: datetime
    market: str
    symbol: str
    rank: int
    in_universe: bool
    l50_candidate: bool
    quote_volume_24h: Decimal
    best_bid: Decimal
    best_ask: Decimal
    criteria_version: str


def eligible(market: str, instrument: Mapping[str, Any], excluded_bases: frozenset[str]) -> bool:
    if instrument.get("quoteCurrency") != "USDT":
        return False
    if instrument.get("tradingStatus") != TRADING[market]:
        return False
    if instrument.get("baseCurrency") in excluded_bases:
        return False
    if market == "FUTURES":
        return (
            instrument.get("isInverse") is False and instrument.get("settlementCurrency") == "USDT"
        )
    return True


def evaluate(
    market: str,
    instruments: Iterable[Mapping[str, Any]],
    tickers: Iterable[Mapping[str, Any]],
    *,
    evaluated_at: datetime,
    excluded_bases: frozenset[str] = STABLECOINS,
) -> list[UniverseEntry]:
    """Rank the eligible instruments of one market. Returns every ranked instrument; only the
    first ``UNIVERSE_SIZE`` are in the universe."""
    if market not in TRADING:
        raise ValueError(f"unknown market: {market!r}")
    evaluated_at = as_utc(evaluated_at, "evaluated_at")
    by_symbol = {i.get("symbol"): i for i in instruments}
    ranked: list[tuple[Decimal, str, Decimal, Decimal]] = []
    for ticker in tickers:
        symbol = ticker.get("symbol")
        instrument = by_symbol.get(symbol)
        if not isinstance(symbol, str) or instrument is None:
            continue
        if not eligible(market, instrument, excluded_bases):
            continue
        volume = positive_price(ticker.get("quoteVolume"))  # no volume, no liquidity
        bid = positive_price(ticker.get("bestBidPrice"))
        ask = positive_price(ticker.get("bestAskPrice"))
        if volume is None or bid is None or ask is None or bid >= ask:
            continue
        ranked.append((volume, symbol, bid, ask))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    version = CRITERIA_VERSION
    if excluded_bases:
        version += "+excluded:" + ",".join(sorted(excluded_bases))
    return [
        UniverseEntry(
            evaluated_at=evaluated_at,
            market=market,
            symbol=symbol,
            rank=position,
            in_universe=position <= UNIVERSE_SIZE,
            l50_candidate=position <= L50_SIZE,
            quote_volume_24h=volume,
            best_bid=bid,
            best_ask=ask,
            criteria_version=version,
        )
        for position, (volume, symbol, bid, ask) in enumerate(ranked, start=1)
    ]
