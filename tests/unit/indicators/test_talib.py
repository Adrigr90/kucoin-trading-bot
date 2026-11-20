"""Every indicator that TA-Lib has, against TA-Lib 0.8.3 (F3-A3; 02 §12.2, points 5 and
6): random walks and real days, at three price scales, value by value from the first, with
the tolerance of each output's units. A series with gaps is compared stretch by stretch,
because TA-Lib does not know gaps. These tests run on x86-64 and on aarch64 (CI)."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import timedelta

import numpy as np
import pytest
import talib

from kbot.indicators.price import ADX, ATR, DMI, EMA, MACD, RSI, SMA, Bollinger
from kbot.indicators.series import Indicator, compute
from kbot.indicators.volume import VWAP, RelativeVolume
from tests.unit.indicators.support import (
    MINUTE,
    PERCENT,
    PRICE_UNITS,
    RATIO,
    close_enough,
    fixture,
    make_points,
    price_scale,
    walk,
)

Rows = list[tuple[float, float, float, float, float]]
Reference = Callable[[np.ndarray, np.ndarray, np.ndarray, np.ndarray], tuple[np.ndarray, ...]]


def arrays(rows: Rows) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    table = np.array(rows, dtype=float)
    return table[:, 1], table[:, 2], table[:, 3], table[:, 4]  # high, low, close, volume


CASES: list[tuple[Callable[[], Indicator], Reference, tuple[str, ...]]] = [
    (lambda: SMA(20), lambda h, lo, c, v: (talib.SMA(c, 20),), (PRICE_UNITS,)),
    (lambda: SMA(5), lambda h, lo, c, v: (talib.SMA(c, 5),), (PRICE_UNITS,)),
    (lambda: EMA(20), lambda h, lo, c, v: (talib.EMA(c, 20),), (PRICE_UNITS,)),
    (lambda: EMA(2), lambda h, lo, c, v: (talib.EMA(c, 2),), (PRICE_UNITS,)),
    (lambda: RSI(14), lambda h, lo, c, v: (talib.RSI(c, 14),), (PERCENT,)),
    (lambda: RSI(2), lambda h, lo, c, v: (talib.RSI(c, 2),), (PERCENT,)),
    (
        lambda: MACD(12, 26, 9),
        lambda h, lo, c, v: talib.MACD(c, 12, 26, 9),
        (PRICE_UNITS,) * 3,
    ),
    (lambda: MACD(3, 7, 2), lambda h, lo, c, v: talib.MACD(c, 3, 7, 2), (PRICE_UNITS,) * 3),
    (
        lambda: Bollinger(20, 2.0),
        lambda h, lo, c, v: talib.BBANDS(c, 20, 2.0, 2.0, 0),
        (PRICE_UNITS,) * 3,
    ),
    (lambda: ATR(14), lambda h, lo, c, v: (talib.ATR(h, lo, c, 14),), (PRICE_UNITS,)),
    (lambda: ATR(1), lambda h, lo, c, v: (talib.ATR(h, lo, c, 1),), (PRICE_UNITS,)),
    (
        lambda: DMI(14),
        lambda h, lo, c, v: (
            talib.PLUS_DI(h, lo, c, 14),
            talib.MINUS_DI(h, lo, c, 14),
            talib.DX(h, lo, c, 14),
        ),
        (PERCENT,) * 3,
    ),
    (lambda: ADX(14), lambda h, lo, c, v: (talib.ADX(h, lo, c, 14),), (PERCENT,)),
    (lambda: ADX(5), lambda h, lo, c, v: (talib.ADX(h, lo, c, 5),), (PERCENT,)),
    (lambda: RelativeVolume(20), lambda h, lo, c, v: (talib.RVOL(v, 20),), (RATIO,)),
]
IDS = [
    "SMA20",
    "SMA5",
    "EMA20",
    "EMA2",
    "RSI14",
    "RSI2",
    "MACD",
    "MACD3-7-2",
    "BB",
    "ATR14",
    "ATR1",
    "DMI",
    "ADX14",
    "ADX5",
    "RVOL",
]


def compare(
    make: Callable[[], Indicator],
    reference: Reference,
    units: tuple[str, ...],
    rows: Rows,
    offsets: list[int] | None = None,
) -> int:
    """Run the indicator over the series and TA-Lib over each continuous stretch; every
    value must be TA-Lib's, from TA-Lib's first one. Returns how many values compared."""
    points = make_points(rows, offsets=offsets)
    ours = {v.open_time: v.outputs for v in compute(make(), points, MINUTE)}
    offsets = offsets if offsets is not None else list(range(len(rows)))
    stretches: list[list[int]] = []
    for i, offset in enumerate(offsets):
        if stretches and offset == offsets[stretches[-1][-1]] + 1:
            stretches[-1].append(i)
        else:
            stretches.append([i])
    compared = 0
    expected_times = []
    for stretch in stretches:
        part = [rows[i] for i in stretch]
        refs = reference(*arrays(part))
        scale = price_scale(part)
        for j, i in enumerate(stretch):
            if math.isnan(refs[0][j]):
                continue
            moment = points[i].open_time
            expected_times.append(moment)
            values = ours.get(moment)
            assert values is not None, f"no value at {moment}"
            for out, ref, unit in zip(values, refs, units, strict=True):
                assert close_enough(out, float(ref[j]), unit, scale), (j, out, float(ref[j]))
            compared += 1
    assert sorted(ours) == expected_times, "the same first value and no more"
    return compared


@pytest.mark.parametrize("scale", [1.0, 1e-8, 1e4], ids=["x1", "x1e-8", "x1e4"])
@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize(("make", "reference", "units"), CASES, ids=IDS)
def test_random_walks_match_talib(
    make: Callable[[], Indicator],
    reference: Reference,
    units: tuple[str, ...],
    seed: int,
    scale: float,
) -> None:
    assert compare(make, reference, units, walk(600, seed, scale)) > 400


@pytest.mark.parametrize(("make", "reference", "units"), CASES, ids=IDS)
def test_a_real_day_matches_talib(
    make: Callable[[], Indicator], reference: Reference, units: tuple[str, ...]
) -> None:
    """Spot BTC-USDT of 10 November 2026: 1440 minutes, without gaps."""
    rows, offsets = fixture("btc-usdt_2026-11-10.csv")
    assert offsets == list(range(1440))
    assert compare(make, reference, units, rows, offsets) > 1300


@pytest.mark.parametrize(("make", "reference", "units"), CASES, ids=IDS)
def test_a_real_day_with_gaps_matches_talib_stretch_by_stretch(
    make: Callable[[], Indicator], reference: Reference, units: tuple[str, ...]
) -> None:
    """Futures ZECUSDTM of 2 March 2025: 888 of 1440 minutes. Every stretch starts again,
    as after a gap, and is compared on its own (02 §12.2, point 6)."""
    rows, offsets = fixture("zecusdtm_2025-03-02.csv")
    assert len(rows) == 888 and offsets != list(range(888))
    assert compare(make, reference, units, rows, offsets) > 0


@pytest.mark.parametrize("scale", [1.0, 1e-8, 1e4], ids=["x1", "x1e-8", "x1e4"])
def test_vwap_matches_talib_day_by_day(scale: float) -> None:
    """TA-Lib accumulates from the start of the range: it gets each UTC day apart."""
    step = timedelta(minutes=5)
    rows = walk(3 * 288, 5, scale)
    points = make_points(rows, step=step)
    ours = {v.open_time: v.outputs[0] for v in compute(VWAP(), points, step)}
    for day in range(3):
        part = rows[day * 288 : (day + 1) * 288]
        high, low, close, volume = arrays(part)
        reference = talib.VWAP(high, low, close, volume)
        scale_s = price_scale(part)
        for j in range(288):
            moment = points[day * 288 + j].open_time
            assert close_enough(ours[moment], float(reference[j]), PRICE_UNITS, scale_s)
    assert len(ours) == 3 * 288


def test_a_result_that_vanishes_for_small_prices_fails_the_comparison() -> None:
    """F3E2-05: with prices of 1e-8 a MACD near 1e-10 must not accept a 0."""
    rows = walk(600, 1, 1e-8)
    _h, _l, close, _v = arrays(rows)
    line = talib.MACD(close, 12, 26, 9)[0]
    scale = price_scale(rows)
    references = [float(x) for x in line if not math.isnan(x) and abs(x) > 1e-12 * scale * 10]
    assert references, "values well above the absolute term"
    assert not any(close_enough(0.0, ref, PRICE_UNITS, scale) for ref in references)
    assert not close_enough(0.0, 1e-10, PRICE_UNITS, 1e-8), "the example of the review"
