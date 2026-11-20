"""How every indicator is run (02 §12.2, points 1 and 6; F3-A4 and F3-A5): which candles
are usable, resets and warming up again, inputs, provenance and the share only REST,
availability, no look-ahead, and the uses of volume (CD-02, CD-04, CD-05, CD-08, CD-12 and
CD-18)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from kbot.history.timeframes import Quality, aggregate, historical_minute
from kbot.indicators.levels import Resistance, Support
from kbot.indicators.price import ADX, ATR, DMI, EMA, MACD, RSI, SMA, Bollinger
from kbot.indicators.series import (
    Indicator,
    Kind,
    VolumeUse,
    VolumeUseForbidden,
    compute,
    from_bar,
    from_minute,
    require_volume_use,
)
from kbot.indicators.volume import VWAP, RelativeVolume
from kbot.market.last_candles import STABILIZATION, Ohlcv
from tests.unit.indicators.support import START, make_points, walk

MINUTE = timedelta(minutes=1)
ALL: list[Indicator] = [
    SMA(5),
    EMA(5),
    RSI(5),
    MACD(3, 7, 3),
    Bollinger(5),
    ATR(5),
    DMI(5),
    ADX(5),
    VWAP(),
    RelativeVolume(5),
    Resistance(2, 20),
    Support(2, 20),
]


def times(values: list) -> list:
    return [v.open_time for v in values]


@pytest.mark.parametrize("indicator", ALL, ids=lambda i: i.name)
def test_poisoning_what_comes_after_t_changes_nothing_up_to_t(indicator: Indicator) -> None:
    """F3-A4: every candle after t is changed; no value up to t changes."""
    points = make_points(walk(200, 3))
    cut = 120
    poisoned = points[:cut] + make_points(walk(200, 99), start=points[cut].open_time)[:80]
    clean = [v for v in compute(indicator, points, MINUTE) if v.open_time < points[cut].open_time]
    dirty = [v for v in compute(indicator, poisoned, MINUTE) if v.open_time < points[cut].open_time]
    assert clean == dirty and clean, "the same values, availability and provenance"


@pytest.mark.parametrize("indicator", ALL, ids=lambda i: i.name)
def test_the_first_value_comes_after_the_warm_up(indicator: Indicator) -> None:
    points = make_points(walk(60, 4))
    values = compute(indicator, points, MINUTE)
    if indicator.kind is Kind.WINDOW and isinstance(indicator, Resistance | Support):
        assert values[0].open_time >= points[indicator.warmup - 1].open_time  # needs a pivot
    else:
        assert values[0].open_time == points[indicator.warmup - 1].open_time


def test_a_gap_resets_and_the_ema_warms_up_again_from_its_simple_mean() -> None:
    """CD-02 and CD-08: a missing minute breaks the series. The EMA has no value until 5
    usable candles after the gap, and that value is their simple mean."""
    rows = walk(20, 5)
    offsets = list(range(10)) + list(range(11, 21))  # minute 10 missing
    points = make_points(rows, offsets=offsets)
    values = compute(EMA(5), points, MINUTE)
    after = [v for v in values if v.open_time > points[9].open_time]
    assert after[0].open_time == points[14].open_time, "5 candles after the gap"
    assert after[0].outputs[0] == pytest.approx(sum(r[3] for r in rows[10:15]) / 5, rel=1e-15)
    assert after[0].inputs_from == points[10].open_time, "the inputs start again"


@pytest.mark.parametrize(
    ("dimension", "price_value", "volume_value"),
    [("time", False, False), ("price", False, False), ("volume_quality", True, False)],
    ids=["T", "P", "V"],
)
def test_a_discrepant_dimension_matters_only_to_who_needs_it(
    dimension: str, price_value: bool, volume_value: bool
) -> None:
    """F3E2-01: a T not valid stops and resets every indicator; a P not valid stops the
    relative volume; a V not valid alone leaves an SMA with its value and no VWAP."""
    points = make_points(walk(40, 6))
    bad = 30
    points[bad] = replace(points[bad], **{dimension: Quality.DISCREPANTE})
    moment = points[bad].open_time
    for indicator, expected in (
        (SMA(5), price_value),
        (RelativeVolume(5), volume_value),
        (VWAP(), volume_value),
    ):
        values = compute(indicator, points, MINUTE)
        assert (moment in times(values)) is expected, (indicator.name, dimension)
        if not expected and indicator.kind is not Kind.SESSION:
            after = [v for v in values if v.open_time > moment]
            assert after and after[0].open_time == points[bad + indicator.warmup].open_time


def test_an_incomplete_candle_is_not_usable() -> None:
    points = make_points(walk(30, 7))
    points[20] = replace(points[20], complete=False, available_at=None)
    values = compute(SMA(3), points, MINUTE)
    assert points[20].open_time not in times(values)
    assert points[21].open_time not in times(values) and points[22].open_time not in times(values)
    assert points[23].open_time in times(values), "warmed up again after it"


def test_the_share_only_rest_counts_the_inputs_of_each_kind() -> None:
    """F3E2-02, the review's case: SMA(2) with the first candle only REST and the next two
    contrasted gives 0 % at the third, never 33.33 %. An EMA keeps its seed only REST in
    its provenance until it resets (CD-18)."""
    points = make_points(walk(3, 8))
    contrasted = [replace(p, rest_only_minutes=0) for p in points[1:]]
    series = [points[0], *contrasted]
    sma = compute(SMA(2), series, MINUTE)
    assert [v.rest_only_share for v in sma] == [0.5, 0.0]
    ema = compute(EMA(2), series, MINUTE)
    assert ema[-1].rest_only_minutes == 1 and ema[-1].minutes == 3
    assert ema[-1].rest_only_share == pytest.approx(1 / 3)


def test_history_is_all_rest_only() -> None:
    """CD-04: in the history every candle is only REST, so every result is 100 %."""
    for indicator in ALL:
        values = compute(indicator, make_points(walk(60, 9)), MINUTE)
        assert {v.rest_only_share for v in values} == {1.0}, indicator.name


def test_the_original_minutes_count_for_higher_timeframes() -> None:
    """A 5-minute candle counts as its 5 minutes, each once."""
    step = timedelta(minutes=5)
    values = compute(SMA(3), make_points(walk(10, 10), step=step), step)
    assert {v.minutes for v in values} == {15}


def test_a_value_is_available_when_its_latest_input_is() -> None:
    """F3E2-03, the review's case: SMA(2), the 10:00 candle available at 10:05 and the
    10:01 one at 10:02:15. The value at 10:01 does not exist before 10:05."""
    ten = START + timedelta(hours=10)
    points = make_points(walk(2, 11), start=ten)
    points[0] = replace(points[0], available_at=ten + timedelta(minutes=5))
    points[1] = replace(points[1], available_at=ten + timedelta(minutes=2, seconds=15))
    (value,) = compute(SMA(2), points, MINUTE)
    assert value.available_at == ten + timedelta(minutes=5)
    late = make_points(walk(10, 12), start=ten)
    late[2] = replace(late[2], available_at=ten + timedelta(hours=1))
    for v in compute(EMA(3), late, MINUTE):
        assert v.available_at >= ten + timedelta(hours=1), "a recursive keeps its inputs"


def test_points_must_be_in_order() -> None:
    points = make_points(walk(5, 13))
    with pytest.raises(ValueError, match="out of order"):
        compute(SMA(2), [points[1], points[0]], MINUTE)


def test_a_use_of_volume_that_b8_forbids_is_refused() -> None:
    """CD-05: an absolute volume threshold, or comparing volumes across instruments."""
    for use in (VolumeUse.ABSOLUTE_THRESHOLD, VolumeUse.CROSS_INSTRUMENT):
        with pytest.raises(VolumeUseForbidden, match="B8"):
            require_volume_use(use)

    class Threshold(SMA):
        volume_use = VolumeUse.ABSOLUTE_THRESHOLD

    with pytest.raises(VolumeUseForbidden):
        compute(Threshold(3), make_points(walk(10, 14)), MINUTE)
    require_volume_use(VolumeUse.SCALE_INVARIANT)


def test_a_new_multiplier_breaks_a_volume_window_like_a_gap() -> None:
    """CD-12: in Futures a change of the contract multiplier breaks the windows of volume,
    and only them."""
    points = [replace(p, multiplier=0.01) for p in make_points(walk(30, 15))]
    points[15:] = [replace(p, multiplier=0.1) for p in points[15:]]
    moment = points[15].open_time
    rvol = compute(RelativeVolume(5), points, MINUTE)
    assert moment not in times(rvol) and points[20].open_time in times(rvol)
    assert rvol[-1].inputs_from == points[24].open_time, "its last w + 1 candles"
    assert moment in times(compute(SMA(5), points, MINUTE)), "prices do not care"


def test_minutes_and_bars_of_the_history_become_points() -> None:
    """A minute of the history and the 5-minute candle of its five minutes: prices and
    volume as the floats of their exact decimals, all of it only REST, available at the
    end of its last minute plus the margin, and the 5 minutes of the candle."""
    price = Decimal("73759.123456789012345678")
    minutes = [
        historical_minute(
            "SPOT",
            "BTC-USDT",
            Ohlcv(START + i * MINUTE, price, price + 1, price - 1, price, Decimal("0.5"), price),
        )
        for i in range(5)
    ]
    point = from_minute(minutes[0])
    assert (point.close, point.high, point.volume) == (float(price), float(price + 1), 0.5)
    assert (point.minutes, point.rest_only_minutes, point.complete) == (1, 1, True)
    assert point.available_at == START + MINUTE + STABILIZATION
    (bar,) = aggregate(minutes, "5m", downloaded_until=START + timedelta(days=1))
    five = from_bar(bar, multiplier=0.01)
    assert (five.minutes, five.rest_only_minutes, five.volume) == (5, 5, 2.5)
    assert five.available_at == START + 5 * MINUTE + STABILIZATION and five.multiplier == 0.01
    assert five.price is Quality.SOLO_REST and five.valid(frozenset())


def test_vwap_has_no_meaning_in_a_daily_timeframe() -> None:
    with pytest.raises(ValueError, match="less than 1 d"):
        compute(VWAP(), make_points(walk(5, 16), step=timedelta(days=1)), timedelta(days=1))
