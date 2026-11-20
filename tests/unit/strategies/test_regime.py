"""The regime of 02 §12.3.2 (F3-A7): its frontiers, the ties, the precedence of the
expansion, the warm-up after a restart, and the same regime for the same data, in any
order of loading."""

from __future__ import annotations

import math
import random
from datetime import timedelta

import pytest

from kbot.history.timeframes import aggregate
from kbot.indicators.series import from_bar
from kbot.strategies.regime import (
    Direction,
    RegimeParams,
    Volatility,
    direction,
    regimes,
    volatility,
)
from tests.unit.indicators.support import MINUTE, make_points, walk
from tests.unit.strategies.support import START, walk_minutes

CALM = [1.0] * 100  # no measure strictly above another: never an extreme


def test_the_direction_at_its_frontiers() -> None:
    assert direction(25.0) is Direction.TENDENCIA
    assert direction(24.99) is Direction.TRANSICION
    assert direction(20.01) is Direction.TRANSICION
    assert direction(20.0) is Direction.RANGO
    # The comparison is exact: the floats next to the frontiers fall on their side.
    assert direction(math.nextafter(25.0, 0.0)) is Direction.TRANSICION
    assert direction(math.nextafter(20.0, 25.0)) is Direction.TRANSICION


def test_the_example_of_the_bollinger_width_with_a_window_of_five() -> None:
    params = RegimeParams(window=5)
    calm = [0.01] * 5
    assert volatility(calm, [0.050, 0.041, 0.038, 0.045, 0.036], params) is Volatility.COMPRESION
    # 0.038 ties with the third: only three are greater, and 300 < 400.
    assert volatility(calm, [0.050, 0.041, 0.038, 0.045, 0.038], params) is Volatility.NORMAL


def test_the_counts_at_their_frontiers_with_a_window_of_100() -> None:
    expanding = [1.0] * 80 + [2.0] * 20  # the last is strictly above 80 values
    almost = [1.0] * 79 + [2.0] * 21  # above 79 only: the others tie
    narrowing = [2.0] * 80 + [1.0] * 20  # the last is strictly below 80 values
    nearly = [2.0] * 79 + [1.0] * 21
    assert volatility(expanding, CALM) is Volatility.EXPANSION
    assert volatility(almost, CALM) is Volatility.NORMAL
    assert volatility(CALM, narrowing) is Volatility.COMPRESION
    assert volatility(CALM, nearly) is Volatility.NORMAL


def test_the_expansion_goes_first() -> None:
    expanding = [1.0] * 80 + [2.0] * 20
    narrowing = [2.0] * 80 + [1.0] * 20
    assert volatility(expanding, narrowing) is Volatility.EXPANSION


def test_ties_never_make_an_extreme() -> None:
    assert volatility(CALM, CALM) is Volatility.NORMAL


def test_the_windows_must_hold_exactly_w_values() -> None:
    with pytest.raises(ValueError, match="exactly 100"):
        volatility(CALM[:99], CALM)


def test_a_flat_series_is_a_range_with_normal_volatility() -> None:
    points = make_points([(10.0, 10.0, 10.0, 10.0, 1.0)] * 130)
    last = regimes(points, MINUTE)[points[-1].open_time]
    assert last.direction is Direction.RANGO  # ADX 0, as TA-Lib gives
    assert last.volatility is Volatility.NORMAL  # ATR and width 0: all ties


def test_the_warm_up_and_the_restart_after_a_gap() -> None:
    rows = walk(400, seed=3)
    offsets = list(range(200)) + [x + 1 for x in range(200, 400)]  # one candle missing
    points = make_points(rows, offsets=offsets)
    found = regimes(points, MINUTE)
    for run in (points[:200], points[200:]):
        opens = [p.open_time for p in run]
        trend = [o for o in opens if o in found and found[o].direction is not None]
        vol = [o for o in opens if o in found and found[o].volatility is not None]
        assert trend[0] == opens[27]  # the 28 candles of the ADX
        assert vol[0] == opens[118]  # Bollinger from the 20th, and 99 more
        assert trend == opens[27:] and vol == opens[118:]
        first = found[opens[118]]
        assert first.volatility_from == opens[0] and first.direction_from == opens[0]
        assert first.volatility_at == run[118].available_at
        assert first.direction_at == run[118].available_at


def test_the_same_data_give_the_same_regime_in_any_order_of_loading() -> None:
    minutes = walk_minutes(3 * 1440, seed=11)
    shuffled = list(minutes)
    random.Random(5).shuffle(shuffled)
    until = START + timedelta(days=3)
    step = timedelta(minutes=15)
    params = RegimeParams(window=20)

    def of(loaded: list) -> dict:
        points = [from_bar(b) for b in aggregate(loaded, "15m", downloaded_until=until)]
        return regimes(points, step, params)

    first = of(minutes)
    assert any(r.volatility is Volatility.COMPRESION for r in first.values())
    assert any(r.direction is Direction.TENDENCIA for r in first.values())
    assert of(shuffled) == first
    assert of(minutes + minutes[:50]) == first  # identical repeats change nothing
