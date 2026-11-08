"""Contract tests for the REST payloads, version by version (07 F2-A4; H08, B4-B6, B14 a)."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from kbot.kucoin.models import (
    Candle,
    Contract,
    Envelope,
    FundingHistory,
    FundingRate,
    FuturesInstrument,
    FuturesInstrumentV1,
    IndexPage,
    InstrumentList,
    KlineData,
    MarkPrice,
    RiskTier,
    SpotInstrument,
    SpotInstrumentV1,
    TickerList,
    TradeList,
)
from kbot.kucoin.rest import ForbiddenRequest, check_request
from kbot.kucoin.routes import ROUTES

V1_EVIDENCE = Path(__file__).resolve().parents[1] / "fixtures" / "kucoin" / "evidencia_d04_v1"

Load = Callable[[str], Any]


def body(recorded: Load, name: str) -> Any:
    fixture = recorded(name)
    assert fixture["status"] == 200
    return Envelope.model_validate(fixture["body"]).data


def test_every_recording_went_through_the_whitelist(recorded: Load, fixture_names: list[str]):
    rest = [name for name in fixture_names if not name.startswith("ws_")]
    assert len(rest) >= 19
    for name in rest:
        fixture = recorded(name)
        route = check_request(httpx.Request("GET", fixture["request"]))
        assert route.name == fixture["route"]


def test_measured_weights_match_the_routes(recorded: Load, fixture_names: list[str]) -> None:
    """The weight of every route was read from gw-ratelimit-remaining (B14 a, H13)."""
    measured = {}
    for name in fixture_names:
        fixture = recorded(name)
        if fixture.get("weight") is not None:
            measured[fixture["route"]] = fixture["weight"]
            assert fixture["ratelimit"]["limit"] == 2000
    assert set(measured) == set(ROUTES)
    for route, weight in measured.items():
        assert ROUTES[route].weight == weight, route


# --- V2 ------------------------------------------------------------------------------


def test_v2_instruments(recorded: Load) -> None:
    spot = InstrumentList.model_validate(body(recorded, "v2_instrument_spot"))
    btc = SpotInstrument.model_validate(spot.list[0])
    assert (btc.symbol, btc.tickSize, btc.baseOrderStep) == (
        "BTC-USDT",
        Decimal("0.1"),
        Decimal("0.00000001"),
    )
    assert btc.tradingStatus == "TradingEnabled"

    futures = InstrumentList.model_validate(body(recorded, "v2_instrument_futures"))
    xbt = FuturesInstrument.model_validate(futures.list[0])
    assert (xbt.symbol, xbt.unitSize, xbt.lotSize, xbt.isInverse) == (
        "XBTUSDTM",
        Decimal("0.001"),
        Decimal("1"),
        False,
    )
    assert xbt.tradingStatus == "Open"
    assert all(
        isinstance(v, str) or v is None or isinstance(v, (bool, list))
        for v in futures.list[0].values()
    ), "V2 sends every decimal as text"


@pytest.mark.parametrize("name", ["v2_kline_spot", "v2_kline_futures", "v2_kline_futures_trade"])
def test_v2_candles(recorded: Load, name: str) -> None:
    data = KlineData.model_validate(body(recorded, name))
    times = [candle.open_time_s for candle in data.list]
    assert times == sorted(times, reverse=True), "newest first"
    assert all(a - b == 60 for a, b in itertools.pairwise(times))
    if data.tradeType == "FUTURES":
        assert data.klineType == "TRADE", "TRADE is the default candle type"


def test_v2_mark_and_index_candles_do_not_exist(recorded: Load) -> None:
    """B3/B4: klineType accepts TRADE; MARK and INDEX are refused with 148035."""
    manifest = recorded("manifest")
    refused = {
        case["params"]["klineType"]: case["code"]
        for case in manifest["rest"]
        if case["route"] == "uta.v2.kline" and case["status"] == 400
    }
    assert refused == {"MARK": "148035", "INDEX": "148035"}


@pytest.mark.parametrize("name", ["v2_trades_spot", "v2_trades_futures"])
def test_v2_trades(recorded: Load, name: str) -> None:
    trades = TradeList.model_validate(body(recorded, name))
    assert trades.list
    for trade in trades.list:
        assert trade.price > 0 and trade.size > 0
        assert trade.tradeId == str(trade.sequence)
        assert trade.ts > 10**18, "nanoseconds"


@pytest.mark.parametrize("name", ["v2_ticker_spot", "v2_ticker_futures"])
def test_v2_tickers(recorded: Load, name: str) -> None:
    tickers = TickerList.model_validate(body(recorded, name))
    (ticker,) = tickers.list
    assert ticker.bestBidPrice < ticker.bestAskPrice


def test_v2_funding(recorded: Load) -> None:
    (rate,) = [FundingRate.model_validate(item) for item in body(recorded, "v2_funding")]
    assert rate.symbol == "XBTUSDTM", "V2 uses the contract symbol (H08)"
    assert rate.fundingRateFloor < 0 < rate.fundingRateCap
    assert rate.currentGranularity == 8 * 3600 * 1000
    assert rate.fundingTime % rate.currentGranularity == 0


def test_v2_product_type_does_not_change_the_answer(recorded: Load) -> None:
    """B4: with a symbol, productType USDT-FUTURES and COIN-FUTURES give the same data."""
    plain = body(recorded, "v2_funding")
    assert body(recorded, "v2_funding_usdt-futures") == plain
    assert body(recorded, "v2_funding_coin-futures") == plain


def test_v2_funding_history(recorded: Load) -> None:
    history = FundingHistory.model_validate(body(recorded, "v2_funding_history"))
    assert history.symbol == "XBTUSDTM"
    times = [point.ts for point in history.list]
    assert times == sorted(times, reverse=True)
    assert all(t % (8 * 3600 * 1000) == 0 for t in times)


# --- V1, kept as evidence for D04 (approved: V2 plus Classic) --------------------------


def test_d04_v1_is_no_longer_whitelisted(recorded: Load) -> None:
    """D04: the V1 answers stay as evidence, but a V1 request is now refused."""
    assert not [name for name in ROUTES if name.startswith("uta.v1.")]
    v1_files = sorted(p.stem for p in V1_EVIDENCE.glob("*.json"))
    assert len(v1_files) == 12
    for name in v1_files:
        with pytest.raises(ForbiddenRequest, match="whitelist"):
            check_request(httpx.Request("GET", recorded(name)["request"]))


def test_v1_funding_uses_another_symbol_and_json_numbers(recorded: Load) -> None:
    v1 = body(recorded, "v1_funding")
    assert isinstance(v1, dict), "V1 returns an object, V2 a list"
    assert v1["symbol"] == ".XBTUSDTMFPI8H"
    assert isinstance(v1["nextFundingRate"], Decimal), "a JSON number, not text"
    (v2,) = body(recorded, "v2_funding")
    assert v1["nextFundingRate"] == Decimal(v2["nextFundingRate"])
    assert v1["fundingTime"] == v2["fundingTime"]


def test_v1_funding_symbol_is_the_contract_funding_symbol(recorded: Load) -> None:
    """B6: the V1 symbol is the fundingRateSymbol of the Classic contract."""
    contracts = [Contract.model_validate(c) for c in body(recorded, "classic_contracts_active")]
    (xbt,) = [c for c in contracts if c.symbol == "XBTUSDTM"]
    assert xbt.fundingRateSymbol == body(recorded, "v1_funding")["symbol"]


def test_v1_and_v2_funding_history_agree(recorded: Load) -> None:
    v1 = body(recorded, "v1_funding_history")
    v2 = FundingHistory.model_validate(body(recorded, "v2_funding_history"))
    assert v1["symbol"] == v2.symbol
    assert [(Decimal(p["fundingRate"]), p["ts"]) for p in v1["list"]] == [
        (p.fundingRate, p.ts) for p in v2.list
    ]
    assert all(isinstance(p["fundingRate"], Decimal) for p in v1["list"])


@pytest.mark.parametrize("market", ["spot", "futures"])
def test_v1_and_v2_candles_are_identical(recorded: Load, market: str) -> None:
    v1 = body(recorded, f"v1_kline_{market}_minute")
    v2 = body(recorded, f"v2_kline_{market}_minute")
    assert v1 == v2
    assert [Candle.model_validate(row) for row in v1["list"]]


def test_v1_futures_instrument_mixes_numbers_and_text(recorded: Load) -> None:
    (v1,) = body(recorded, "v1_instrument_futures")["list"]
    (v2,) = body(recorded, "v2_instrument_futures")["list"]
    assert v1["tradingStatus"] == "1" and v2["tradingStatus"] == "Open"
    assert isinstance(v1["indexPriceTickSize"], Decimal)
    assert isinstance(v1["maxLeverage"], int) and v2["maxLeverage"] == "125"
    assert v1["unitSize"] == v2["unitSize"] == "0.001"


def test_v1_instruments_follow_their_own_contract(recorded: Load) -> None:
    """A4-01: the V1 instruments are validated with typed V1 models."""
    spot = InstrumentList.model_validate(body(recorded, "v1_instrument_spot"))
    btc = SpotInstrumentV1.model_validate(spot.list[0])
    assert (btc.symbol, btc.tradingStatus, btc.tickSize) == ("BTC-USDT", "1", Decimal("0.1"))

    futures = InstrumentList.model_validate(body(recorded, "v1_instrument_futures"))
    xbt = FuturesInstrumentV1.model_validate(futures.list[0])
    assert (xbt.symbol, xbt.unitSize, xbt.maxLeverage, xbt.tradingStatus) == (
        "XBTUSDTM",
        Decimal("0.001"),
        125,
        "1",
    )
    v2 = FuturesInstrument.model_validate(
        InstrumentList.model_validate(body(recorded, "v2_instrument_futures")).list[0]
    )
    assert (xbt.tickSize, xbt.lotSize, xbt.unitSize) == (v2.tickSize, v2.lotSize, v2.unitSize)


@pytest.mark.parametrize(
    ("name", "model", "field", "wrong"),
    [
        ("v1_instrument_spot", SpotInstrumentV1, "tickSize", Decimal("0.1")),
        ("v1_instrument_spot", SpotInstrumentV1, "tradingStatus", "TradingEnabled"),
        ("v1_instrument_spot", SpotInstrumentV1, "feeCategory", "1"),
        ("v1_instrument_spot", SpotInstrumentV1, "minFunds", None),
        ("v1_instrument_futures", FuturesInstrumentV1, "maxLeverage", "125"),
        ("v1_instrument_futures", FuturesInstrumentV1, "orderPriceRange", "0.05"),
        ("v1_instrument_futures", FuturesInstrumentV1, "unitSize", Decimal("0.001")),
        ("v1_instrument_futures", FuturesInstrumentV1, "launchTime", True),
        ("v1_instrument_futures", FuturesInstrumentV1, "tradingStatus", 1),
        ("v2_instrument_spot", SpotInstrument, "tickSize", Decimal("0.1")),
        ("v2_instrument_futures", FuturesInstrument, "maxLeverage", 125),
    ],
)
def test_a_changed_field_type_breaks_the_contract(
    recorded: Load, name: str, model: type, field: str, wrong: object
) -> None:
    item = dict(InstrumentList.model_validate(body(recorded, name)).list[0])
    model.model_validate(item)  # type: ignore[attr-defined]
    item[field] = wrong
    with pytest.raises(ValidationError):
        model.model_validate(item)  # type: ignore[attr-defined]
    del item[field]
    with pytest.raises(ValidationError):
        model.model_validate(item)  # type: ignore[attr-defined]


@pytest.mark.parametrize("name", ["v1_trades_spot", "v1_trades_futures"])
def test_v1_trades_have_the_v2_shape(recorded: Load, name: str) -> None:
    assert TradeList.model_validate(body(recorded, name)).list


@pytest.mark.parametrize("name", ["v1_ticker_spot", "v1_ticker_futures"])
def test_v1_tickers_have_the_v2_fields(recorded: Load, name: str) -> None:
    assert TickerList.model_validate(body(recorded, name)).list


# --- Classic Futures ------------------------------------------------------------------


def test_classic_contracts(recorded: Load) -> None:
    fixture = recorded("classic_contracts_active")
    assert fixture["truncated"]["total"] > 100
    contracts = [Contract.model_validate(c) for c in body(recorded, "classic_contracts_active")]
    (xbt,) = [c for c in contracts if c.symbol == "XBTUSDTM"]
    assert (xbt.multiplier, xbt.lotSize, xbt.tickSize) == (
        Decimal("0.001"),
        Decimal(1),
        Decimal("0.1"),
    )
    assert xbt.indexSymbol == ".KXBTUSDT"
    (v2,) = body(recorded, "v2_instrument_futures")["list"]
    assert Decimal(v2["unitSize"]) == xbt.multiplier


def test_classic_risk_tiers_are_contiguous(recorded: Load) -> None:
    tiers = [RiskTier.model_validate(t) for t in body(recorded, "classic_risk_limit")]
    assert [t.level for t in tiers] == list(range(1, len(tiers) + 1))
    assert tiers[0].minRiskLimit == 0
    for lower, upper in itertools.pairwise(tiers):
        assert upper.minRiskLimit == lower.maxRiskLimit
        assert upper.maintainMargin > lower.maintainMargin
        assert upper.maxLeverage < lower.maxLeverage
    for tier in tiers:
        assert tier.maintainMargin < tier.initialMargin


def test_classic_mark_price(recorded: Load) -> None:
    mark = MarkPrice.model_validate(body(recorded, "classic_mark_price"))
    assert mark.symbol == "XBTUSDTM"
    assert mark.granularity == 1000
    assert mark.timePoint % 1000 == 0


def test_classic_index(recorded: Load) -> None:
    page = IndexPage.model_validate(body(recorded, "classic_index"))
    assert {point.symbol for point in page.dataList} == {".KXBTUSDT"}
    times = [point.timePoint for point in page.dataList]
    assert times == sorted(times, reverse=True)
    weights = [
        Decimal(part["weight"])
        for part in body(recorded, "classic_index")["dataList"][0]["decomposionList"]
    ]
    assert sum(weights) == pytest.approx(Decimal(1), abs=Decimal("0.001"))
