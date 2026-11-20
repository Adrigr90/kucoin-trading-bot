"""VWAP of the UTC day and relative volume: the cases of 02 §12.2, point 4, with the three
differences declared against TA-Lib 0.8.3, each checked against what TA-Lib gives."""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pytest
import talib

from kbot.indicators.series import compute
from kbot.indicators.volume import VWAP, RelativeVolume
from tests.unit.indicators.support import make_points

MINUTE = timedelta(minutes=1)


def rows_from(typical: list[float], volumes: list[float]) -> list[tuple[float, ...]]:
    return [(p, p, p, p, v) for p, v in zip(typical, volumes, strict=True)]


def test_vwap_of_three_candles_as_talib() -> None:
    rows = rows_from([10.0, 11.0, 12.0], [1.0, 2.0, 1.0])
    values = [v.outputs[0] for v in compute(VWAP(), make_points(rows), MINUTE)]
    assert values == pytest.approx([10.0, 32 / 3, 11.0], rel=1e-15)
    prices = np.array([10.0, 11.0, 12.0])
    reference = talib.VWAP(prices, prices, prices, np.array([1.0, 2.0, 1.0]))
    assert values == pytest.approx(list(reference), rel=1e-15)


def test_without_volume_yet_there_is_no_vwap() -> None:
    """Declared difference: TA-Lib gives 0 while the volume adds up to 0."""
    rows = rows_from([10.0, 10.0, 10.0, 11.0], [0.0, 0.0, 1.0, 2.0])
    points = make_points(rows)
    values = compute(VWAP(), points, MINUTE)
    assert [v.open_time for v in values] == [points[2].open_time, points[3].open_time]
    assert [v.outputs[0] for v in values] == pytest.approx([10.0, 32 / 3], rel=1e-15)
    prices = np.array([10.0, 10.0, 10.0, 11.0])
    reference = talib.VWAP(prices, prices, prices, np.array([0.0, 0.0, 1.0, 2.0]))
    assert list(reference[:2]) == [0.0, 0.0], "what TA-Lib gives instead"


def test_a_gap_leaves_the_rest_of_the_day_without_vwap_and_the_next_day_starts_again() -> None:
    """Declared difference: TA-Lib does not know gaps."""
    step = timedelta(hours=4)
    rows = rows_from([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0], [1.0] * 8)
    offsets = [0, 1, 3, 4, 5, 6, 7, 8]  # the third 4-hour candle of the first day is missing
    points = make_points(rows, step=step, offsets=offsets)
    values = compute(VWAP(), points, step)
    assert [v.open_time for v in values] == [
        points[0].open_time,
        points[1].open_time,
        points[5].open_time,  # 00:00 of the next day
        points[6].open_time,
        points[7].open_time,
    ]
    assert values[2].outputs[0] == 15.0 and values[2].inputs_from == points[5].open_time


def test_a_series_that_starts_mid_day_waits_for_the_next_day() -> None:
    step = timedelta(hours=6)
    points = make_points(rows_from([10.0] * 6, [1.0] * 6), step=step, offsets=[1, 2, 3, 4, 5, 6])
    values = compute(VWAP(), points, step)
    assert values[0].open_time == points[3].open_time  # offset 4: 00:00 of the next day


def test_relative_volume_as_talib() -> None:
    rows = rows_from([10.0] * 4, [10.0, 20.0, 30.0, 40.0])
    (value,) = compute(RelativeVolume(3), make_points(rows), MINUTE)
    assert value.outputs == (2.0,)
    reference = talib.RVOL(np.array([10.0, 20.0, 30.0, 40.0]), 3)
    assert float(reference[3]) == 2.0


def test_a_baseline_of_zero_gives_no_relative_volume() -> None:
    """Declared difference: TA-Lib gives an infinity, or NaN for 0/0."""
    volumes = [0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 0.0, 0.0]
    values = compute(RelativeVolume(3), make_points(rows_from([10.0] * 8, volumes)), MINUTE)
    assert [v.outputs[0] for v in values] == [0.0, 0.0, 0.0]  # at 4, 5 and 6
    reference = talib.RVOL(np.array(volumes), 3)
    assert math.isinf(float(reference[3])) and math.isnan(float(reference[7]))


def test_the_running_total_of_relative_volume_rounds_as_talibs() -> None:
    """F3E2-07 (review of 7b1c670), a declared limitation: with volumes 1e12, 1e-6, 1e-6,
    1e-6 and 1e-6, the running total of RVOL(3) loses the small volumes while the large one
    is in it, so the fifth candle gives 3, as TA-Lib 0.8.3 does with the same running total,
    though the mean of its window, recomputed, gives 1. Agreeing with TA-Lib is not
    mathematical exactness. It needs volumes some 10^16 times apart in one instrument."""
    volumes = [1e12, 1e-6, 1e-6, 1e-6, 1e-6]
    values = compute(RelativeVolume(3), make_points(rows_from([10.0] * 5, volumes)), MINUTE)
    ours = values[-1].outputs[0]
    reference = float(talib.RVOL(np.array(volumes), 3)[4])
    recomputed = volumes[4] / (sum(volumes[1:4]) / 3)
    assert (ours, reference) == (3.0, 3.0)
    assert recomputed == pytest.approx(1.0, rel=1e-12)
