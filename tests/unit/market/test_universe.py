"""Provisional universe (02 §3.8; D12 not approved; P3: 10 + 10 depth-50 candidates)."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from kbot.market.universe import L50_SIZE, UNIVERSE_SIZE, evaluate

NOW = datetime(2026, 11, 7, 0, 0, tzinfo=UTC)


def spot(symbol: str, *, quote: str = "USDT", status: str = "TradingEnabled") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "baseCurrency": symbol.split("-")[0],
        "quoteCurrency": quote,
        "tradingStatus": status,
    }


def futures(
    symbol: str, *, inverse: bool = False, settle: str = "USDT", status: str = "Open"
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "baseCurrency": symbol.removesuffix("USDTM"),
        "quoteCurrency": "USDT",
        "settlementCurrency": settle,
        "isInverse": inverse,
        "tradingStatus": status,
    }


def ticker(symbol: str, volume: str, bid: str = "1.0", ask: str = "1.1") -> dict[str, Any]:
    return {"symbol": symbol, "quoteVolume": volume, "bestBidPrice": bid, "bestAskPrice": ask}


def test_the_first_twenty_by_quote_volume_and_ten_candidates() -> None:
    symbols = [f"C{i:02d}-USDT" for i in range(30)]
    tickers = [ticker(s, str(1000 + i)) for i, s in enumerate(symbols)]
    random.Random(4).shuffle(tickers)
    entries = evaluate("SPOT", [spot(s) for s in symbols], tickers, evaluated_at=NOW)
    assert [e.symbol for e in entries] == list(reversed(symbols))
    assert [e.rank for e in entries] == list(range(1, 31))
    assert sum(e.in_universe for e in entries) == UNIVERSE_SIZE
    assert sum(e.l50_candidate for e in entries) == L50_SIZE
    assert all(e.in_universe for e in entries if e.l50_candidate)
    assert entries[0].quote_volume_24h == Decimal(1029)
    assert entries[0].criteria_version == "provisional-1+excluded:DAI,FDUSD,PYUSD,TUSD,USDC,USDE"


def test_ties_go_to_the_symbol_in_alphabetical_order() -> None:
    names = ["B-USDT", "A-USDT", "C-USDT"]
    entries = evaluate(
        "SPOT", [spot(s) for s in names], [ticker(s, "5") for s in names], evaluated_at=NOW
    )
    assert [e.symbol for e in entries] == ["A-USDT", "B-USDT", "C-USDT"]


@pytest.mark.parametrize(
    ("instrument", "tick"),
    [
        (spot("A-BTC", quote="BTC"), ticker("A-BTC", "9")),  # not quoted in USDT
        (spot("B-USDT", status="TradingDisabled"), ticker("B-USDT", "9")),  # not trading
        (spot("C-USDT"), ticker("C-USDT", "0")),  # no volume
        (spot("D-USDT"), ticker("D-USDT", "NaN")),
        (spot("E-USDT"), ticker("E-USDT", "9", bid="1.2", ask="1.1")),  # crossed book
        (spot("F-USDT"), ticker("F-USDT", "9", bid="")),  # no bid
        (spot("G-USDT"), {"symbol": "G-USDT", "quoteVolume": 9.5}),  # not text
    ],
)
def test_ineligible_spot_pairs_are_left_out(
    instrument: dict[str, Any], tick: dict[str, Any]
) -> None:
    good = spot("OK-USDT")
    entries = evaluate("SPOT", [instrument, good], [tick, ticker("OK-USDT", "1")], evaluated_at=NOW)
    assert [e.symbol for e in entries] == ["OK-USDT"]


@pytest.mark.parametrize(
    "instrument",
    [
        futures("XBTUSDM", inverse=True, settle="XBT"),  # inverse: outside the scope (D33)
        futures("AUSDTM", settle="USDC"),
        futures("BUSDTM", status="Paused"),
    ],
)
def test_only_linear_usdt_futures_that_trade(instrument: dict[str, Any]) -> None:
    good = futures("XBTUSDTM")
    entries = evaluate(
        "FUTURES",
        [instrument, good],
        [ticker(instrument["symbol"], "99"), ticker("XBTUSDTM", "1")],
        evaluated_at=NOW,
    )
    assert [e.symbol for e in entries] == ["XBTUSDTM"]


def test_excluded_bases_are_recorded_in_the_criteria_version() -> None:
    names = ["USDC-USDT", "BTC-USDT"]
    entries = evaluate(
        "SPOT",
        [spot(s) for s in names],
        [ticker("USDC-USDT", "50"), ticker("BTC-USDT", "40")],
        evaluated_at=NOW,
        excluded_bases=frozenset({"USDC"}),
    )
    assert [e.symbol for e in entries] == ["BTC-USDT"]
    assert entries[0].criteria_version == "provisional-1+excluded:USDC"


def test_unknown_markets_and_naive_times_are_refused() -> None:
    with pytest.raises(ValueError, match="market"):
        evaluate("MARGIN", [], [], evaluated_at=NOW)
    with pytest.raises(ValueError, match="time zone"):
        evaluate("SPOT", [], [], evaluated_at=datetime(2026, 11, 7))


def test_stablecoins_are_left_out_by_default() -> None:
    names = ["USDC-USDT", "FDUSD-USDT", "BTC-USDT"]
    entries = evaluate(
        "SPOT",
        [spot(s) for s in names],
        [ticker("USDC-USDT", "90"), ticker("FDUSD-USDT", "80"), ticker("BTC-USDT", "40")],
        evaluated_at=NOW,
    )
    assert [e.symbol for e in entries] == ["BTC-USDT"]
    nothing = evaluate(
        "SPOT",
        [spot(s) for s in names],
        [ticker("USDC-USDT", "90"), ticker("BTC-USDT", "40")],
        evaluated_at=NOW,
        excluded_bases=frozenset(),
    )
    assert [e.symbol for e in nothing] == ["USDC-USDT", "BTC-USDT"]
    assert nothing[0].criteria_version == "provisional-1"
