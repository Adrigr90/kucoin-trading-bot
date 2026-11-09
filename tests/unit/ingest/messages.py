"""Builders of KuCoin public messages with the shapes recorded in E1 (tests only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def ns(moment: datetime) -> int:
    return (moment - EPOCH) // timedelta(microseconds=1) * 1000


def seconds(moment: datetime) -> int:
    return (moment - EPOCH) // timedelta(seconds=1)


def book(market: str, symbol: str, sequence: int, at: datetime, depth: str = "5") -> dict[str, Any]:
    return {
        "T": f"obu.{market}",
        "dp": depth,
        "t": "snapshot",
        "P": ns(at),
        "d": {
            "s": symbol,
            "C": sequence,
            "O": sequence,
            "M": ns(at),
            "b": [["100.0", "2"], ["99.9", "1"]],
            "a": [["100.2", "1"], ["100.3", "3"]],
        },
    }


def trade(market: str, symbol: str, at: datetime) -> dict[str, Any]:
    return {
        "T": f"trade.{market}",
        "P": ns(at),
        "d": {"s": symbol, "p": "100.1", "q": "1", "S": "buy", "M": ns(at), "ti": "1", "E": 1},
    }


def kline(market: str, symbol: str, minute: datetime, at: datetime) -> dict[str, Any]:
    opened = seconds(minute)
    return {
        "T": f"kline.{market}",
        "P": ns(at),
        "d": {
            "s": symbol,
            "i": "1min",
            "O": opened,
            "C": opened + 60,
            "o": "100",
            "h": "101",
            "l": "99",
            "c": "100.5",
            "v": "2",
            "a": "201",
            "S": False,
        },
    }


def mark(symbol: str, at: datetime, mp: str = "100", ip: str = "101") -> dict[str, Any]:
    ms = (at - EPOCH) // timedelta(milliseconds=1)
    return {
        "T": "mark-price",
        "P": ns(at),
        "d": {"s": symbol, "mp": mp, "ip": ip, "oi": "5", "ts": ms},
    }


def pong(core: Any, moment: datetime) -> None:
    """A pong on both connections at ``moment``: the proof of life without which nothing
    after the last message received is closed (``RecorderCore._proven``)."""
    for market in ("SPOT", "FUTURES"):
        core.on_alive(market, moment)


async def beat(recorder: Any, moment: datetime) -> None:
    """One step of the recorder, after a pong on both connections at that moment."""
    pong(recorder.core, moment)
    await recorder.step(moment)


def rest_row(minute: datetime) -> list[object]:
    return [seconds(minute), "100", "101", "99", "100.5", "2", "201"]


def feed_minute(core: Any, minute: datetime, spot: str, futures: str) -> None:
    """Books every 10 s, one trade and one candle per instrument, and 60 mark values."""
    for second in range(0, 60, 10):
        moment = minute + timedelta(seconds=second + 1)
        sequence = int(moment.timestamp())
        core.on_message("SPOT", book("SPOT", spot, sequence, moment), moment)
        core.on_message("SPOT", book("SPOT", spot, sequence, moment, "50"), moment)
        core.on_message("FUTURES", book("FUTURES", futures, sequence, moment), moment)
        core.on_message("FUTURES", book("FUTURES", futures, sequence, moment, "50"), moment)
    at = minute + timedelta(seconds=30)
    for market, symbol in (("SPOT", spot), ("FUTURES", futures)):
        core.on_message(market, trade(market, symbol, at), at)
        core.on_message(market, kline(market, symbol, minute, at), at)
    for second in range(60):
        moment = minute + timedelta(seconds=second)
        core.on_message("FUTURES", mark(futures, moment), moment)


def rest_candles_until(end_of_data: datetime) -> Any:
    """A fake REST kline answer with a candle for every minute before ``end_of_data``."""

    def rows(market: str, symbol: str, start: int, end: int) -> list[list[object]]:
        first = start - start % 60 + 60
        return [
            rest_row(datetime.fromtimestamp(s, UTC))
            for s in range(end - end % 60, first - 1, -60)
            if s < seconds(end_of_data)
        ]

    return rows
