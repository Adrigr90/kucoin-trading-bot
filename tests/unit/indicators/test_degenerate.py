"""±DI, DX and ADX where they degenerate, exactly as TA-Lib 0.8.3 (F3E2-04; 02 §12.2,
points 2 and 6), and the flat series of RSI, ATR and Bollinger."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import talib

from kbot.indicators.price import ADX, ATR, DMI, RSI, Bollinger
from kbot.indicators.series import compute
from tests.unit.indicators.support import make_points

MINUTE = timedelta(minutes=1)


def run(indicator: object, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> list:
    rows = [(c, h, lo, c, 1.0) for h, lo, c in zip(high, low, close, strict=True)]
    return [v.outputs for v in compute(indicator, make_points(rows), MINUTE)]  # type: ignore[arg-type]


def test_a_di_sum_under_the_band_leaves_dx_and_adx_at_zero() -> None:
    """The review's case: 40 candles, period 14, H = 10, 10.000000000000002, ..., L = 1 and
    C = 5. +DI is about 1.4e-15 and -DI 0, under the band of 1e-14: DX is not defined. DX
    gives 0 at candle 15 and ADX 0 at candle 28, as TA-Lib; the plain formula gave 100."""
    high = np.array([10.0] + [10.000000000000002] * 39)
    low, close = np.full(40, 1.0), np.full(40, 5.0)
    dmi = run(DMI(14), high, low, close)
    assert dmi[0][0] == float(talib.PLUS_DI(high, low, close, 14)[14])
    assert 1e-15 < dmi[0][0] < 2e-15 and dmi[0][1] == 0.0
    assert [d[2] for d in dmi] == [0.0] * 26 == list(talib.DX(high, low, close, 14)[14:])
    adx = run(ADX(14), high, low, close)
    assert [a[0] for a in adx] == [0.0] * 13 == list(talib.ADX(high, low, close, 14)[27:])


def test_a_di_sum_that_falls_under_the_band_later_keeps_dx_and_adx() -> None:
    """A rise starts the ADX; then a range without directional movement makes the DI
    decay until their sum is under 1e-14, at candle 505. From there DX repeats its last
    value and ADX stays, as TA-Lib, instead of falling towards 0."""
    m = 900
    high = np.concatenate([np.arange(40, dtype=float) + 11, np.full(m, 60.0)])
    low = np.concatenate([np.arange(40, dtype=float) + 9, np.full(m, 40.0)])
    close = (high + low) / 2
    dmi = run(DMI(14), high, low, close)
    sums = [d[0] + d[1] for d in dmi]
    first_under = next(i for i, s in enumerate(sums) if s < 1e-14) + 14  # 0-based candle
    assert first_under == 504, "candle 505"
    reference_dx = talib.DX(high, low, close, 14)
    reference_adx = talib.ADX(high, low, close, 14)
    assert [d[2] for d in dmi] == list(reference_dx[14:])
    adx = [a[0] for a in run(ADX(14), high, low, close)]
    assert adx == list(reference_adx[27:])
    assert adx[first_under - 27 :] == [100.0] * (len(high) - first_under)


def test_flat_series_give_what_talib_gives() -> None:
    """RSI 50 (TA-Lib 0.8.3; 0 in earlier versions), ATR, DX and ADX 0, and Bollinger
    bands on the mean."""
    flat = np.full(60, 5.0)
    assert {o[0] for o in run(RSI(14), flat, flat, flat)} == {50.0}
    assert {o[0] for o in run(ATR(14), flat, flat, flat)} == {0.0}
    assert {o[2] for o in run(DMI(14), flat, flat, flat)} == {0.0}
    assert {o[0] for o in run(ADX(14), flat, flat, flat)} == {0.0}
    assert {o for o in run(Bollinger(20), flat, flat, flat)} == {(5.0, 5.0, 5.0)}
    assert list(talib.RSI(flat, 14)[14:16]) == [50.0, 50.0]
