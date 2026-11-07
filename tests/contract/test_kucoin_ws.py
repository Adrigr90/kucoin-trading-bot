"""Contract tests for the public WebSocket messages (07 A4; B1, B2, B8, B31)."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from kbot.kucoin.models import (
    Ack,
    BookMessage,
    Envelope,
    KlineData,
    KlineMessage,
    MarkPriceMessage,
    Pong,
    TickerMessage,
    TradeMessage,
    Welcome,
)

Load = Callable[[str], Any]
MARKETS = ["spot", "futures"]


def samples(recorded: Load, market: str, channel: str) -> list[dict[str, Any]]:
    items = recorded(f"ws_{market}")["samples"][channel]
    assert items, f"no {channel} sample for {market}"
    return [item["message"] for item in items]


@pytest.mark.parametrize("market", MARKETS)
@pytest.mark.parametrize("channel", ["obu_5", "obu_50"])
def test_book_snapshots(recorded: Load, market: str, channel: str) -> None:
    for message in samples(recorded, market, channel):
        book = BookMessage.model_validate(message)
        assert f"obu.{market.upper()}" == book.T
        assert book.dp == channel.removeprefix("obu_")
        assert book.d.O == book.d.C, "a snapshot starts and ends at the same sequence"
        assert len(book.d.a) == len(book.d.b) == int(book.dp)
        assert book.P > 10**18 and book.d.M > 10**18, "nanoseconds"


@pytest.mark.parametrize("market", MARKETS)
def test_book_sequences_increase(recorded: Load, market: str) -> None:
    books = [BookMessage.model_validate(m) for m in samples(recorded, market, "obu_5")]
    sequences = [book.d.C for book in books]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)


@pytest.mark.parametrize("market", MARKETS)
def test_trades(recorded: Load, market: str) -> None:
    for message in samples(recorded, market, "trade"):
        trade = TradeMessage.model_validate(message)
        assert trade.d.p > 0 and trade.d.q > 0
        assert trade.d.M > 10**18


@pytest.mark.parametrize("market", MARKETS)
def test_tickers(recorded: Load, market: str) -> None:
    for message in samples(recorded, market, "ticker"):
        ticker = TickerMessage.model_validate(message)
        assert ticker.d.b < ticker.d.a


@pytest.mark.parametrize("market", MARKETS)
def test_candles(recorded: Load, market: str) -> None:
    for message in samples(recorded, market, "kline"):
        KlineMessage.model_validate(message)


def test_mark_price_every_second(recorded: Load) -> None:
    """B2 (first evidence only; the 24 h measurement is in E5)."""
    marks = [MarkPriceMessage.model_validate(m) for m in samples(recorded, "futures", "mark_price")]
    assert {mark.d.s for mark in marks} == {"XBTUSDTM"}
    times = [mark.d.ts for mark in marks]
    assert [b - a for a, b in itertools.pairwise(times)] == [1000] * (len(times) - 1)


@pytest.mark.parametrize("market", MARKETS)
def test_control_messages_and_ping_format(recorded: Load, market: str) -> None:
    """B31: ``{"id", "type": "ping"}`` is answered with a pong (24 h test pending, E5)."""
    control = samples(recorded, market, "control")
    welcome = Welcome.model_validate(control[0])
    assert welcome.pingInterval == 18000
    acks = [Ack.model_validate(m) for m in control if "result" in m]
    pongs = [Pong.model_validate(m) for m in control if m.get("type") == "pong"]
    assert len(acks) == (6 if market == "futures" else 5)
    assert len(pongs) >= 2 and all(p.id.startswith("p") for p in pongs)
    states = recorded(f"ws_{market}")["samples"]["states"]
    assert [state[1] for state in states] == ["connected"], "no disconnection while recording"


@pytest.mark.parametrize("market", MARKETS)
def test_rest_candle_columns_match_the_labelled_websocket_candle(
    recorded: Load, market: str
) -> None:
    """B8 (labels): REST rows are [open time, open, high, low, close, volume, turnover]."""
    evidence = recorded(f"ws_{market}")["minute"]
    (last,) = evidence["kline_last"]
    ws = KlineMessage.model_validate(last["message"]).d
    rest = KlineData.model_validate(
        Envelope.model_validate(recorded(f"v2_kline_{market}_minute")["body"]).data
    )
    (row,) = [c for c in rest.list if c.open_time_s == evidence["minute_open_s"]]
    columns = (row.open_time_s, row.open, row.high, row.low, row.close, row.volume, row.turnover)
    assert columns == (ws.O, ws.o, ws.h, ws.l, ws.c, ws.v, ws.a)


@pytest.mark.parametrize("market", MARKETS)
def test_candle_volume_is_twice_the_websocket_trades(recorded: Load, market: str) -> None:
    """B8 (units), observation [I]: in the recorded minute, the candle volume and turnover
    are exactly twice the sums over the trades of the WebSocket. Futures sizes are in
    contracts (multiplier 0.001 for XBTUSDTM). Still open: a 60-minute comparison in E5."""
    evidence = recorded(f"ws_{market}")["minute"]
    ws = KlineMessage.model_validate(evidence["kline_last"][0]["message"]).d
    trades = evidence["trades_in_minute"]
    assert len(trades) == evidence["trade_count"] > 0
    quantity = sum((Decimal(q) for _, _, q, _ in trades), Decimal(0))
    turnover = sum((Decimal(p) * Decimal(q) for _, p, q, _ in trades), Decimal(0))
    multiplier = Decimal("0.001") if market == "futures" else Decimal(1)
    assert ws.v == 2 * quantity
    assert ws.a == 2 * turnover * multiplier
