"""Higher timeframes from the minutes: the cases of the data contract (02 §12.1, point 8)
that this part answers for (F3-A2): CD-01, CD-06 to CD-10 and CD-17; and exact sums
(F3E1-01)."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, Inexact, localcontext
from pathlib import Path

import pytest

from kbot.history.timeframes import (
    TIMEFRAMES,
    Bar,
    ConflictingCandles,
    MinuteCandle,
    Quality,
    aggregate,
    consecutive,
    historical_minute,
)
from kbot.market.last_candles import CandleEvidence, Ohlcv

DAY = datetime(2026, 11, 10, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "history"
# The largest and the smallest positive values of format 2, decimal128(38, 24), and zero.
LARGEST = Decimal("99999999999999.999999999999999999999999")
SMALLEST = Decimal("0.000000000000000000000001")
ZERO = Decimal("0.000000000000000000000000")


def minute(
    at: datetime, price: str = "100", volume: str = "2", market: str = "SPOT", symbol: str = "BTC"
) -> MinuteCandle:
    p = Decimal(price)
    candle = Ohlcv(at, p, p + 1, p - 1, p + Decimal("0.5"), Decimal(volume), Decimal(volume) * p)
    return historical_minute(market, symbol, candle)


def minutes(start: datetime, count: int, *, skip: tuple[int, ...] = ()) -> list[MinuteCandle]:
    return [
        minute(start + timedelta(minutes=i), str(100 + i)) for i in range(count) if i not in skip
    ]


def test_a_missing_minute_makes_an_incomplete_candle_that_no_indicator_uses() -> None:
    """CD-01: 4 of the 5 minutes of [10:00, 10:05). It is kept with its 4 for the reports,
    and it is never usable, however late."""
    start = DAY + timedelta(hours=10)
    (bar,) = aggregate(minutes(start, 5, skip=(2,)), "5m", downloaded_until=DAY + timedelta(days=1))
    assert (bar.minutes_present, bar.minutes, bar.complete) == (4, 5, False)
    assert bar.available_at is None
    assert not bar.usable_at(DAY + timedelta(days=30))


def test_a_complete_candle_is_made_from_its_minutes() -> None:
    start = DAY + timedelta(hours=10)
    (bar,) = aggregate(minutes(start, 5), "5m", downloaded_until=DAY + timedelta(days=1))
    assert (bar.open_time, bar.open, bar.close) == (start, Decimal(100), Decimal("104.5"))
    assert (bar.high, bar.low) == (Decimal(105), Decimal(99))
    assert bar.volume == Decimal(10)
    assert bar.turnover == sum(Decimal(2) * (100 + i) for i in range(5))  # exact decimals
    assert bar.complete and bar.minutes_present == 5


def test_a_candle_counts_its_minutes_only_rest() -> None:
    """The share only REST counts original minutes (02 §12.1, point 5; §12.2, point 1):
    a 5-minute candle with two contrasted minutes holds three only REST."""
    start = DAY + timedelta(hours=10)
    five = minutes(start, 5)
    contrasted = [replace(m, evidence=CandleEvidence.CRUZADA_CONSISTENTE) for m in five[:2]]
    (bar,) = aggregate(contrasted + five[2:], "5m", downloaded_until=DAY + timedelta(days=1))
    assert (bar.minutes, bar.rest_only_minutes) == (5, 3)
    (history,) = aggregate(five, "5m", downloaded_until=DAY + timedelta(days=1))
    assert history.rest_only_minutes == 5, "the history: all only REST"


def day_of(start: datetime, volumes: list[Decimal], turnovers: list[Decimal]) -> list[Bar]:
    one = Decimal(1)
    candles = [
        historical_minute(
            "SPOT", "ZEC-USDT", Ohlcv(start + timedelta(minutes=i), one, one, one, one, v, t)
        )
        for i, (v, t) in enumerate(zip(volumes, turnovers, strict=True))
    ]
    return aggregate(candles, "1d", downloaded_until=start + timedelta(days=1))


def by_integers(values: list[Decimal]) -> Decimal:
    """The sum of values of 24 decimals with integers alone, with no decimal context."""
    total = 0
    for value in values:
        sign, digits, exponent = value.as_tuple()
        assert exponent == -24
        coefficient = int("".join(map(str, digits)))
        total += -coefficient if sign else coefficient
    return Decimal(f"{total}E-24")


@pytest.mark.parametrize("precision", [3, 28, 200])
def test_volume_and_turnover_are_exact_whatever_the_ambient_context(precision: int) -> None:
    """F3E1-01, with the real case: the daily candle of Spot ZEC-USDT of 7 November 2025,
    read from the history. With the ambient context (28 digits) its turnover came out as
    287386854.5583713212601414940."""
    data = json.loads((FIXTURES / "zec_usdt_2025-11-07.json").read_text(encoding="utf-8"))
    volumes = [Decimal(v) for v in data["volumen"]]
    turnovers = [Decimal(t) for t in data["turnover"]]
    start = datetime(2025, 11, 7, tzinfo=UTC)
    with localcontext(Context(prec=precision)):
        (bar,) = day_of(start, volumes, turnovers)
    assert bar.complete
    assert str(bar.turnover) == "287386854.558371321260141494040000"
    assert str(bar.volume) == "440345.663926703542127382560000"
    assert (bar.volume, bar.turnover) == (by_integers(volumes), by_integers(turnovers))


@pytest.mark.parametrize(
    "values",
    [
        [LARGEST] * 1440,
        [SMALLEST] * 1440,
        [LARGEST, SMALLEST] * 720,
        [ZERO] * 1439 + [SMALLEST],
    ],
    ids=["largest", "smallest", "both", "zeros"],
)
def test_the_sums_of_a_day_at_the_limits_of_format_2_are_exact(values: list[Decimal]) -> None:
    """F3E1-01: 1440 values at the limits of format 2, 14 integer digits and 24 decimals
    (02 §3.9), give their exact sum with the ambient context of 28 digits."""
    (bar,) = day_of(DAY, values, values)
    assert bar.volume == bar.turnover == by_integers(values)


def test_a_sum_that_does_not_fit_raises_instead_of_rounding() -> None:
    """Beyond the 80 digits of the accounting context: ``Inexact``, never a rounded sum."""
    values = [Decimal("1E+60"), SMALLEST]
    with pytest.raises(Inexact):
        day_of(DAY, values, values)


def test_the_same_minutes_in_another_order_and_repeated_give_the_same_candles() -> None:
    """CD-06."""
    start = DAY + timedelta(hours=9)
    ordered = minutes(start, 240, skip=(17, 100, 101))
    shuffled = ordered + ordered[:50]  # identical repeats
    random.Random(4).shuffle(shuffled)
    until = DAY + timedelta(days=1)
    for timeframe in TIMEFRAMES:
        assert aggregate(shuffled, timeframe, downloaded_until=until) == aggregate(
            ordered, timeframe, downloaded_until=until
        ), timeframe


def test_two_different_candles_with_the_same_key_stop_the_process() -> None:
    """CD-07: none is chosen."""
    first = minute(DAY, "100")
    other = minute(DAY, "101")
    with pytest.raises(ConflictingCandles, match="two different candles"):
        aggregate([first, other], "5m", downloaded_until=DAY + timedelta(days=1))


def test_two_candles_separated_by_an_empty_interval_are_not_consecutive() -> None:
    """CD-08: two complete hours with an hour without minutes between them."""
    bars = aggregate(
        minutes(DAY, 60) + minutes(DAY + timedelta(hours=2), 60),
        "1h",
        downloaded_until=DAY + timedelta(days=1),
    )
    assert [b.open_time for b in bars] == [DAY, DAY + timedelta(hours=2)]
    assert all(b.complete for b in bars)
    assert not consecutive(bars[0], bars[1])
    nxt = aggregate(
        minutes(DAY + timedelta(hours=1), 60), "1h", downloaded_until=DAY + timedelta(days=1)
    )
    assert consecutive(bars[0], nxt[0])


def test_utc_limits_of_the_day_and_of_the_four_hour_candles() -> None:
    """CD-09: the minute of 23:59 belongs to the day it starts in; the 4 h candles open at
    00, 04, 08, 12, 16 and 20 UTC."""
    last = DAY + timedelta(hours=23, minutes=59)
    (daily,) = aggregate([minute(last)], "1d", downloaded_until=DAY + timedelta(days=2))
    assert daily.open_time == DAY
    whole_day = minutes(DAY, 1440)
    four_hours = aggregate(whole_day, "4h", downloaded_until=DAY + timedelta(days=1))
    assert [b.open_time.hour for b in four_hours] == [0, 4, 8, 12, 16, 20]
    assert all(b.complete for b in four_hours)
    (day,) = aggregate(whole_day, "1d", downloaded_until=DAY + timedelta(days=1))
    assert day.complete and day.minutes == 1440


def test_an_interval_not_ended_within_what_was_downloaded_gives_no_candle() -> None:
    """CD-10: downloaded up to 10:03; the interval [10:00, 10:05) has not ended."""
    start = DAY + timedelta(hours=10)
    assert aggregate(minutes(start, 3), "5m", downloaded_until=start + timedelta(minutes=3)) == []
    (bar,) = aggregate(minutes(start, 5), "5m", downloaded_until=start + timedelta(minutes=5))
    assert bar.complete


def test_a_complete_candle_is_available_when_its_last_minute_is() -> None:
    """CD-17: [10:00, 10:05) with a margin of 15 s; its last minute is received and
    validated at 10:05:16. At 10:05:10 it does not exist for anybody; at 10:05:16 it does,
    without waiting for the close of the day."""
    start = DAY + timedelta(hours=10)
    live = [
        replace(m, evidence=CandleEvidence.CRUZADA_CONSISTENTE, available_at=m.available_at)
        for m in minutes(start, 5)
    ]
    live[-1] = replace(live[-1], available_at=start + timedelta(minutes=5, seconds=16))
    (bar,) = aggregate(live, "5m", downloaded_until=DAY + timedelta(days=1))
    assert not bar.usable_at(start + timedelta(minutes=5, seconds=10))
    assert bar.usable_at(start + timedelta(minutes=5, seconds=16))
    assert bar.available_at == start + timedelta(minutes=5, seconds=16)


def test_a_historical_minute_is_available_at_its_end_plus_the_margin() -> None:
    """The rule declared for the history (02 §12.1, point 4): end of the minute plus 15 s;
    its dimensions are only REST and its evidence vela_rest_suficiente."""
    m = minute(DAY)
    assert m.available_at == DAY + timedelta(minutes=1, seconds=15)
    assert (m.price, m.volume, m.time) == (Quality.SOLO_REST,) * 3
    assert m.evidence is CandleEvidence.REST_SUFICIENTE


def test_a_candle_takes_the_worst_quality_of_its_minutes() -> None:
    """Point 5: per dimension, the worst of its minutes (contrasted < only REST <
    discrepant); its evidence is vela_rest_suficiente if any of them is."""
    base = [
        replace(
            m,
            evidence=CandleEvidence.CRUZADA_CONSISTENTE,
            price=Quality.CONTRASTADA,
            volume=Quality.CONTRASTADA,
            time=Quality.CONTRASTADA,
        )
        for m in minutes(DAY, 5)
    ]
    until = DAY + timedelta(days=1)
    (clean,) = aggregate(base, "5m", downloaded_until=until)
    assert (clean.price, clean.volume_quality, clean.time) == (Quality.CONTRASTADA,) * 3
    assert clean.evidence is CandleEvidence.CRUZADA_CONSISTENTE
    mixed = list(base)
    mixed[1] = replace(
        mixed[1], volume=Quality.DISCREPANTE, evidence=CandleEvidence.CRUZADA_DISCREPANTE
    )
    mixed[3] = replace(mixed[3], price=Quality.SOLO_REST, evidence=CandleEvidence.REST_SUFICIENTE)
    (bar,) = aggregate(mixed, "5m", downloaded_until=until)
    assert (bar.price, bar.volume_quality, bar.time) == (
        Quality.SOLO_REST,
        Quality.DISCREPANTE,
        Quality.CONTRASTADA,
    )
    assert bar.evidence is CandleEvidence.REST_SUFICIENTE


def test_instruments_are_never_mixed() -> None:
    btc = [minute(DAY + timedelta(minutes=i), symbol="BTC") for i in range(5)]
    eth = [minute(DAY + timedelta(minutes=i), "50", symbol="ETH") for i in range(3)]
    bars = aggregate(btc + eth, "5m", downloaded_until=DAY + timedelta(days=1))
    assert [(b.symbol, b.minutes_present) for b in bars] == [("BTC", 5), ("ETH", 3)]
    assert isinstance(bars[0], Bar)


def test_a_minute_that_does_not_open_on_the_minute_is_refused() -> None:
    odd = minute(DAY + timedelta(seconds=30))
    with pytest.raises(ValueError, match="open on the minute"):
        aggregate([odd], "5m", downloaded_until=DAY + timedelta(days=1))
