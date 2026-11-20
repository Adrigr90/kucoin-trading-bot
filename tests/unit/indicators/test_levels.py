"""Supports and resistances from pivots: the cases of 02 §12.2, point 4."""

from __future__ import annotations

from datetime import timedelta

from kbot.indicators.levels import Resistance, Support
from kbot.indicators.series import compute
from tests.unit.indicators.support import make_points

MINUTE = timedelta(minutes=1)


def highs(values: list[float], closes: list[float] | None = None) -> list[tuple[float, ...]]:
    closes = closes if closes is not None else [min(1.0, h) for h in values]
    return [(c, h, 0.0, c, 1.0) for h, c in zip(values, closes, strict=True)]


def lows(values: list[float], closes: list[float] | None = None) -> list[tuple[float, ...]]:
    closes = closes if closes is not None else [max(9.0, low) for low in values]
    return [(c, 20.0, low, c, 1.0) for low, c in zip(values, closes, strict=True)]


def levels(indicator: Resistance | Support, rows: list[tuple[float, ...]]) -> list[float | None]:
    points = make_points(rows)
    found = {v.open_time: v.outputs[0] for v in compute(indicator, points, MINUTE)}
    return [found.get(p.open_time) for p in points]


def test_a_pivot_is_known_when_its_k_th_candle_after_closes() -> None:
    """Highs 1, 2, 5, 2, 1: the candle of 5 is a high pivot, known at the fifth candle,
    and the resistance is 5 from there on."""
    assert levels(Resistance(2, 100), highs([1, 2, 5, 2, 1, 1, 1])) == [
        None,
        None,
        None,
        None,
        5,
        5,
        5,
    ]


def test_a_close_above_breaks_the_resistance_and_an_older_one_comes_back() -> None:
    """A lower pivot after 5 is confirmed and broken: the resistance is 5 again."""
    values = [1, 2, 5, 2, 1, 3, 1, 1, 4, 1]
    closes = [1, 1, 1, 1, 1, 1, 1, 1, 4, 1]  # the close of 4 breaks the pivot of 3, not 5
    found = levels(Resistance(2, 100), highs(values, closes))
    assert found[4:8] == [5, 5, 5, 3], "the pivot of 3 is known at the eighth candle"
    assert found[8:] == [5, 5]


def test_two_equal_highs_make_no_pivot() -> None:
    assert levels(Resistance(2, 100), highs([1, 2, 5, 5, 2, 1, 1])) == [None] * 7


def test_a_pivot_expires_m_candles_after_its_own() -> None:
    """With m = 6, the pivot of the third candle counts while t - j < 6: up to the eighth
    candle, and not at the ninth, though nobody went above it."""
    found = levels(Resistance(2, 6), highs([1, 2, 5, 2, 1, 1, 1, 1, 1, 1]))
    assert found[4:8] == [5, 5, 5, 5] and found[8:] == [None, None]


def test_supports_are_the_same_with_lows_and_closes_below() -> None:
    values = [9, 8, 5, 8, 9, 9, 4, 9]
    closes = [9, 9, 9, 9, 9, 9, 4, 9]  # the close of 4 breaks the support of 5
    assert levels(Support(2, 100), lows(values, closes)) == [
        None,
        None,
        None,
        None,
        5,
        5,
        None,
        None,
    ]


def test_the_inputs_are_the_last_m_plus_k_candles() -> None:
    points = make_points(highs([1, 2, 5, 2, 1] + [1] * 20))
    values = compute(Resistance(2, 30), points, MINUTE)
    assert values[-1].minutes == 25 and values[-1].inputs_from == points[0].open_time
    short = compute(Resistance(2, 6), make_points(highs([1, 2, 5, 2, 1, 1, 1, 1])), MINUTE)
    assert short[-1].minutes == 8
