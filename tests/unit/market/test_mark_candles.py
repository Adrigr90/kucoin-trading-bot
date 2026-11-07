"""Own mark and index candles (02 §3.7 and §3.8; owner decision of 7 November 2026).

Review of E2 (problems 6 and 7): a candle needs all 60 seconds of the minute with valid
prices on a healthy connection; anything else is a gap or waits for Classic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kbot.market.mark_candles import (
    Evidence,
    FlowState,
    MarkCandleBuilder,
    index_candle_from_points,
    minute_of,
)

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
T0_MS = 1_794_052_800_000
NONE = timedelta(0)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def mark(
    ms: int, mp: object = "100", ip: object = "101", symbol: str = "XBTUSDTM"
) -> dict[str, object]:
    return {
        "T": "mark-price",
        "P": (T0_MS + ms) * 1_000_000,
        "d": {"s": symbol, "mp": mp, "ip": ip, "oi": "100", "ts": T0_MS + ms},
    }


def builder() -> MarkCandleBuilder:
    built = MarkCandleBuilder("XBTUSDTM", started_at=at(0))
    built.connection_up(at(0))
    return built


def feed_seconds(built: MarkCandleBuilder, seconds: range | list[int], base: int = 0) -> None:
    for second in seconds:
        value = str(100 + (second * 7) % 11)
        built.on_message(mark(base + second * 1000, value, str(Decimal(value) + 1)))


def test_the_reference_minute_is_right() -> None:
    assert minute_of(T0_MS) == T0
    assert minute_of(T0_MS + 59_999) == T0
    assert minute_of(T0_MS + 60_000) == at(60)


def test_sixty_continuous_seconds_give_a_mark_and_an_index_candle() -> None:
    built = builder()
    feed_seconds(built, range(60))
    (result,) = built.close_until(at(60), NONE)
    assert result.flow_state is FlowState.S1 and result.samples == 60
    assert result.mark is not None and result.index is not None
    values = [Decimal(100 + (s * 7) % 11) for s in range(60)]
    assert (result.mark.open, result.mark.high, result.mark.low, result.mark.close) == (
        values[0],
        max(values),
        min(values),
        values[-1],
    )
    assert result.mark.evidence is Evidence.FLOW and result.mark.samples == 60
    assert result.index.high == max(values) + 1
    assert not result.index_needs_rebuild


@pytest.mark.parametrize(
    "seconds",
    [
        list(range(59)),  # the last second is missing
        [s for s in range(60) if s != 30],  # one second missing in the middle
        [0, 1, 2],  # the example of the review: three values, no disconnection
        list(range(0, 60, 2)),  # half the frequency
    ],
)
def test_without_every_second_there_is_no_candle(seconds: list[int]) -> None:
    built = builder()
    feed_seconds(built, seconds)
    (result,) = built.close_until(at(60), NONE)
    assert result.flow_state is FlowState.S2
    assert not result.disconnected, "the socket never closed, and still no candle"
    assert result.mark is None, "mark: a gap, never a candle from part of the minute"
    assert result.index_needs_rebuild, "index: rebuilt from Classic or a gap"


def test_a_disconnection_turns_the_minute_into_a_gap_even_with_sixty_values() -> None:
    built = builder()
    feed_seconds(built, range(60))
    built.connection_down(at(30))
    built.connection_up(at(30.5))
    (result,) = built.close_until(at(60), NONE)
    assert result.disconnected and result.flow_state is FlowState.S2
    assert result.mark is None and result.index_needs_rebuild


def test_a_minute_without_values_is_s3() -> None:
    built = builder()
    (result,) = built.close_until(at(60), NONE)
    assert result.flow_state is FlowState.S3 and result.samples == 0
    assert result.mark is None and result.index_needs_rebuild


@pytest.mark.parametrize(
    ("mp", "ip"),
    [
        ("NaN", "100"),
        ("Infinity", "100"),
        ("-Infinity", "100"),
        ("sNaN", "100"),
        ("0", "100"),
        ("-1", "100"),
        ("abc", "100"),
        ("", "100"),
        (100.5, "100"),
        (None, "100"),
        ("100", "NaN"),
        ("100", "0"),
    ],
)
def test_invalid_prices_never_reach_a_candle(mp: object, ip: object) -> None:
    built = builder()
    feed_seconds(built, [s for s in range(60) if s != 7])
    built.on_message(mark(7000, mp, ip))
    (result,) = built.close_until(at(60), NONE)
    assert result.anomalies == 1 and result.samples == 59
    assert result.mark is None and result.flow_state is FlowState.S2


@pytest.mark.parametrize("ts", [T0_MS + 7500, "1794052807000", 1.7e12, True])
def test_timestamps_off_the_whole_second_are_anomalies(ts: object) -> None:
    built = builder()
    feed_seconds(built, range(60))
    built.on_message({"d": {"s": "XBTUSDTM", "mp": "100", "ip": "100", "ts": ts}})
    (result,) = built.close_until(at(60), NONE)
    assert result.anomalies == 1 and result.mark is None


def test_the_order_of_arrival_does_not_matter_and_repeats_keep_the_first_value() -> None:
    built = builder()
    feed_seconds(built, list(reversed(range(60))))
    built.on_message(mark(0, "999", "999"))  # same ts: the first value stays
    (result,) = built.close_until(at(60), NONE)
    assert result.mark is not None
    assert result.mark.open == Decimal(100) and result.mark.high < Decimal(999)


def test_late_values_and_other_symbols_are_ignored() -> None:
    built = builder()
    feed_seconds(built, range(60))
    built.on_message(mark(3000, "7", "7", symbol="ETHUSDTM"))
    first = built.close_until(at(60), NONE)
    assert first[0].mark is not None
    built.on_message(mark(5000, "1", "1"))  # its minute is closed
    (second,) = built.close_until(at(120), NONE)
    assert second.samples == 0 and second.anomalies == 0


def test_minutes_close_after_the_lateness_margin() -> None:
    built = builder()
    feed_seconds(built, range(60))
    assert built.close_until(at(61), timedelta(seconds=2)) == []
    assert len(built.close_until(at(62), timedelta(seconds=2))) == 1


def test_times_must_carry_a_zone_and_are_normalized_to_utc() -> None:
    with pytest.raises(ValueError, match="time zone"):
        MarkCandleBuilder("XBTUSDTM", started_at=datetime(2026, 11, 7, 12, 0))
    madrid = timezone(timedelta(hours=1))
    built = MarkCandleBuilder("XBTUSDTM", started_at=datetime(2026, 11, 7, 13, 0, tzinfo=madrid))
    built.connection_up(datetime(2026, 11, 7, 13, 0, tzinfo=madrid))
    feed_seconds(built, range(60))
    (result,) = built.close_until(at(60), NONE)
    assert result.minute == T0 and result.mark is not None
    with pytest.raises(ValueError, match="time zone"):
        built.close_until(datetime(2026, 11, 7, 12, 5), NONE)


def classic(seconds: range | list[int], value: object = None) -> list[tuple[int, object]]:
    return [
        (T0_MS + 1000 * s, value if value is not None else Decimal(100 + s % 7)) for s in seconds
    ]


def test_an_index_minute_is_rebuilt_from_classic_with_every_second() -> None:
    candle = index_candle_from_points("XBTUSDTM", T0, classic(range(60)))
    assert candle is not None and candle.evidence is Evidence.CLASSIC
    assert (candle.open, candle.high, candle.low, candle.close, candle.samples) == (
        Decimal(100),
        Decimal(106),
        Decimal(100),
        Decimal(103),
        60,
    )
    outside = [*classic(range(60)), (T0_MS + 60_000, Decimal(1)), (T0_MS - 1000, Decimal(1))]
    assert index_candle_from_points("XBTUSDTM", T0, outside) == candle


@pytest.mark.parametrize(
    "points",
    [
        classic(range(59)),
        classic([s for s in range(60) if s != 20]),
        [(ts + 500, v) for ts, v in classic(range(60))],
        [*classic(range(60)), (T0_MS + 20_500, Decimal(100))],
        [*classic(range(60)), (T0_MS + 20_000, Decimal(100))],
        classic(range(60), Decimal("NaN")),
        classic(range(60), Decimal(0)),
        classic(range(60), Decimal(-5)),
        classic(range(60), "100"),
        classic(range(60), 100.0),
    ],
)
def test_an_incomplete_or_invalid_classic_minute_stays_a_gap(
    points: list[tuple[int, object]],
) -> None:
    assert index_candle_from_points("XBTUSDTM", T0, points) is None
