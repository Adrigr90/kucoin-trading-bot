"""The breakout ficha (02 §12.3.5; F3-A6): its example 2, a short in Futures SOLUSDTM, and
its variants, with the values the ficha gives."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kbot.strategies.breakout import (
    BreakoutParams,
    context_holds,
    invalidated,
    setup_holds,
    target,
)
from kbot.strategies.regime import Volatility, volatility
from kbot.strategies.rules import (
    LONG,
    SHORT,
    LevelExit,
    Outcome,
    TriggerCandle,
    directions,
    execution_minute,
    initial_stop,
    level_exit,
    may_enter,
    risk,
    walk,
)
from tests.unit.indicators.support import PRICE_UNITS, close_enough

DAY = datetime(2025, 6, 2, tzinfo=UTC)
P = BreakoutParams()
SCALE = 150.0
N, C = Volatility.NORMAL, Volatility.COMPRESION
LABELS = [N, N, C, N, N]  # t-5 to t-1: [06:00, 07:00), t-3, is in compression
MIN = P.relative_volume_min


def at(hours: int, minutes: int = 0, seconds: int = 0) -> datetime:
    return DAY + timedelta(hours=hours, minutes=minutes, seconds=seconds)


def same(ours: float, expected: float) -> bool:
    return close_enough(ours, expected, PRICE_UNITS, SCALE)


def test_the_compression_of_example_2() -> None:
    """[06:00, 07:00): its width is below 86 of the 100 of its window, and its relative ATR
    is above only 41 values."""
    width = [0.05] * 86 + [0.01] * 13 + [0.02]
    relative_atr = [0.001] * 41 + [0.009] * 58 + [0.005]
    assert volatility(relative_atr, width) is Volatility.COMPRESION


def test_example_2() -> None:
    # 1. Signal at [09:00, 10:00), D_s = 10:00:15, context [04:00, 08:00).
    assert context_holds(SHORT, 142.30, 145.80)
    assert setup_holds(SHORT, LABELS, 139.20, 140.00, 2.10, MIN)
    # 2. Trigger: 139.40 does not go below 138.90; 138.70 does, at 10:30:15.
    candles = [
        TriggerCandle(at(10, 15 * i), at(10, 15 * i + 15, 15), close)
        for i, close in enumerate([139.40, 138.70, 138.00, 137.50])
    ]
    result = walk(at(10, 0, 15), candles, lambda c: c < 138.90, lambda _: True)
    assert (result.outcome, result.at) == (Outcome.DECISION, at(10, 30, 15))
    stop = initial_stop(SHORT, 138.70, 0.80, P.stop_atr)
    assert same(stop, 140.30)
    # 3. Entry at 10:31, 138.65: r = 1.65 and T = 135.35.
    assert execution_minute(result.at) == at(10, 31)
    goal = target(SHORT, 138.65, stop, P.target_r)
    assert same(risk(SHORT, 138.65, stop), 1.65) and same(goal, 135.35)
    assert may_enter(SHORT, 138.65, stop, goal)
    # 4. [10:00, 11:00) closes at 138.20: it goes on. [11:00, 12:00) closes at 140.10: back.
    assert not invalidated(SHORT, 138.20, 140.00)
    assert invalidated(SHORT, 140.10, 140.00)
    assert level_exit(SHORT, 138.10, 140.25, stop, goal) is None  # the stop is not touched
    # 5. Out at the opening of 12:01, 140.05: about -0.85 R, before costs.
    assert round(SHORT * (140.05 - 138.65) / 1.65, 2) == -0.85


def test_the_variants_of_example_2() -> None:
    stop = initial_stop(SHORT, 138.70, 0.80, P.stop_atr)
    goal = target(SHORT, 138.65, stop, P.target_r)
    assert level_exit(SHORT, 135.35, 137.00, stop, goal) is LevelExit.TARGET  # +2 R
    assert setup_holds(SHORT, LABELS, 139.20, 140.00, 1.50, MIN)
    assert not setup_holds(SHORT, LABELS, 139.20, 140.00, 1.49, MIN)
    assert not setup_holds(SHORT, LABELS, 140.00, 140.00, 2.10, MIN)  # strictly below
    assert not setup_holds(SHORT, [N, N, N, None, N], 139.20, 140.00, 2.10, MIN)
    assert SHORT not in directions("SPOT")
    assert setup_holds(LONG, LABELS, 140.80, 140.00, 2.10, MIN)  # upwards, allowed in Spot
    assert context_holds(LONG, 146.00, 145.80)


def test_the_invalidation_at_the_level_itself() -> None:
    assert invalidated(LONG, 140.00, 140.00)  # back to the broken resistance
    assert not invalidated(LONG, 140.01, 140.00)
    assert invalidated(SHORT, 140.00, 140.00)


def test_a_target_that_is_not_positive_blocks_the_entry() -> None:
    """F3E3-02, in the breakout: the target is fixed when entering."""
    stop = initial_stop(SHORT, 1.0, 1.0, P.stop_atr)
    goal = target(SHORT, 1.0, stop, P.target_r)
    assert goal == -3.0 and not may_enter(SHORT, 1.0, stop, goal)
