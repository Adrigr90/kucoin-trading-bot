"""Own mark and index candles (02 §3.7 and §3.8; owner decision of 7 November 2026)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from kbot.market.mark_candles import (
    Evidence,
    MarkCandleBuilder,
    index_candle_from_points,
    minute_of,
)

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
T0_MS = 1_794_052_800_000
NONE = timedelta(0)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def mark(second: int, mp: str, ip: str, symbol: str = "XBTUSDTM") -> dict[str, object]:
    return {
        "T": "mark-price",
        "P": (T0_MS + second * 1000) * 1_000_000 + 5,
        "d": {"s": symbol, "mp": mp, "ip": ip, "oi": "100", "ts": T0_MS + second * 1000},
    }


def builder() -> MarkCandleBuilder:
    built = MarkCandleBuilder("XBTUSDTM", started_at=at(0))
    built.connection_up(at(0))
    return built


def test_the_reference_minute_is_right() -> None:
    assert minute_of(T0_MS) == T0
    assert minute_of(T0_MS + 59_999) == T0
    assert minute_of(T0_MS + 60_000) == at(60)


def test_a_healthy_minute_gives_a_mark_and_an_index_candle() -> None:
    built = builder()
    values = ["100.5", "101.25", "99.75", "100.0"]
    for second, value in zip((0, 20, 40, 59), values, strict=True):
        built.on_message(mark(second, value, f"{Decimal(value) + 1}"))
    candles, gaps = built.close_until(at(60), NONE)
    assert gaps == []
    mark_candle, index_candle = candles
    assert (mark_candle.open, mark_candle.high, mark_candle.low, mark_candle.close) == (
        Decimal("100.5"),
        Decimal("101.25"),
        Decimal("99.75"),
        Decimal("100.0"),
    )
    assert mark_candle.samples == 4 and mark_candle.evidence is Evidence.FLOW
    assert index_candle.series == "index" and index_candle.high == Decimal("102.25")


def test_the_order_of_arrival_does_not_matter() -> None:
    built = builder()
    built.on_message(mark(30, "105", "105"))
    built.on_message(mark(10, "101", "101"))
    built.on_message(mark(50, "102", "102"))
    candles, _ = built.close_until(at(60), NONE)
    assert (candles[0].open, candles[0].close) == (Decimal(101), Decimal(102))


def test_a_disconnection_turns_both_minutes_into_gaps() -> None:
    built = builder()
    built.on_message(mark(5, "100", "100"))
    built.connection_down(at(30))
    built.connection_up(at(32))
    built.on_message(mark(40, "100", "100"))
    candles, gaps = built.close_until(at(120), NONE)
    assert [(g.series, g.minute) for g in gaps] == [
        ("mark", T0),
        ("index", T0),
        ("mark", at(60)),
        ("index", at(60)),
    ]
    assert candles == [], "the second minute had no values: nothing is invented"


def test_a_minute_without_values_is_a_gap_even_when_healthy() -> None:
    built = builder()
    _, gaps = built.close_until(at(60), NONE)
    assert [g.series for g in gaps] == ["mark", "index"]


def test_late_values_and_duplicates_never_change_a_candle() -> None:
    built = builder()
    built.on_message(mark(1, "100", "100"))
    built.on_message(mark(1, "999", "999"))  # same ts: duplicate, the first value stays
    candles, _ = built.close_until(at(60), NONE)
    assert candles[0].high == Decimal(100)
    built.on_message(mark(2, "1", "1"))  # its minute is already closed
    candles, _ = built.close_until(at(120), NONE)
    assert candles == []


def test_other_symbols_are_ignored_and_bad_messages_fail() -> None:
    built = builder()
    built.on_message(mark(1, "5", "5", symbol="ETHUSDTM"))
    with pytest.raises(ValueError, match="mark-price"):
        built.on_message({"d": {"s": "XBTUSDTM", "mp": 100.0, "ip": "1", "ts": T0_MS}})
    _, gaps = built.close_until(at(60), NONE)
    assert len(gaps) == 2


def test_minutes_close_after_the_lateness_margin() -> None:
    built = builder()
    built.on_message(mark(1, "100", "100"))
    assert built.close_until(at(61), timedelta(seconds=2)) == ([], [])
    candles, _ = built.close_until(at(62), timedelta(seconds=2))
    assert len(candles) == 2


def test_an_index_minute_is_rebuilt_only_with_every_second() -> None:
    points = [(T0_MS + 1000 * s, Decimal(100 + s % 7)) for s in range(60)]
    candle = index_candle_from_points("XBTUSDTM", T0, points)
    assert candle is not None and candle.evidence is Evidence.CLASSIC
    assert (candle.open, candle.high, candle.low, candle.close, candle.samples) == (
        Decimal(100),
        Decimal(106),
        Decimal(100),
        Decimal(103),
        60,
    )
    assert index_candle_from_points("XBTUSDTM", T0, points[:-1]) is None
    shifted = [(ts + 500, value) for ts, value in points]
    assert index_candle_from_points("XBTUSDTM", T0, shifted) is None
    outside = [*points, (T0_MS + 60_000, Decimal(1))]
    assert index_candle_from_points("XBTUSDTM", T0, outside) == candle
