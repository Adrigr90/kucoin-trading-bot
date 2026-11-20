"""The mean reversion ficha (02 §12.3.6; F3-A6): its example 3, a long in Spot LINK-USDT,
and its variants, with the values the ficha gives."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kbot.strategies.mean_reversion import (
    MeanReversionParams,
    context_holds,
    fires,
    invalidated,
    setup_holds,
    time_limit,
)
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

DAY = datetime(2025, 9, 15, tzinfo=UTC)
P = MeanReversionParams()
SCALE = 21.0
LOWER, UPPER, TARGET, ATR, VWAP = 19.70, 20.50, 20.10, 0.18, 20.05


def at(hours: int, minutes: int = 0, seconds: int = 0) -> datetime:
    return DAY + timedelta(hours=hours, minutes=minutes, seconds=seconds)


def same(ours: float, expected: float) -> bool:
    return close_enough(ours, expected, PRICE_UNITS, SCALE)


def test_example_3() -> None:
    # 1. Signal at the 15 m candle [13:15, 13:30), D_s = 13:30:15, context [12:00, 13:00).
    assert context_holds(17.40, 19.95, 19.60, 20.30)
    assert setup_holds(LONG, 19.62, LOWER, UPPER, 27.50, VWAP, ATR)
    # 2. Trigger: 19.66 does not pass 19.70; 19.74 is between 19.70 and 20.10, at 13:40:15.
    candles = [
        TriggerCandle(at(13, 30 + 5 * i), at(13, 35 + 5 * i, 15), close)
        for i, close in enumerate([19.66, 19.74, 19.80])
    ]
    result = walk(
        at(13, 30, 15), candles, lambda c: fires(LONG, c, LOWER, UPPER, TARGET), lambda _: True
    )
    assert (result.outcome, result.at) == (Outcome.DECISION, at(13, 40, 15))
    stop = initial_stop(LONG, 19.74, ATR, P.stop_atr)
    assert same(stop, 19.38)
    # 3. Entry at 13:41, 19.75: r = 0.37 and T - P_e = 0.35 > 0.
    assert execution_minute(result.at) == at(13, 41)
    assert may_enter(LONG, 19.75, stop, TARGET)
    assert same(risk(LONG, 19.75, stop), 0.37)
    # 4. Neither [13:00, 14:00) nor [14:00, 15:00) invalidates.
    assert not invalidated(LONG, 18.20, 19.90, 19.55, 20.25)
    assert not invalidated(LONG, 18.90, 20.02, 19.58, 20.28)
    # 5. The minute of 15:07 reaches 20.11: the target. About +0.95 R, before costs.
    assert level_exit(LONG, 19.98, 20.11, stop, TARGET) is LevelExit.TARGET
    assert round((TARGET - 19.75) / risk(LONG, 19.75, stop), 2) == 0.95


def test_the_variants_of_example_3() -> None:
    assert invalidated(LONG, 25.30, 19.90, 19.55, 20.25)  # tendencia at 14:00:15
    assert time_limit(at(13, 41)) == at(17, 41)  # or the first minute present after
    assert setup_holds(LONG, 19.62, LOWER, UPPER, 30.00, VWAP, ATR)
    assert not setup_holds(LONG, 19.62, LOWER, UPPER, 30.01, VWAP, ATR)
    exactly = VWAP - 19.62  # VWAP - C is exactly one ATR, in float as the code computes it
    assert setup_holds(LONG, 19.62, LOWER, UPPER, 27.50, VWAP, exactly)
    assert not fires(LONG, 20.12, LOWER, UPPER, TARGET)  # at or past T: no trigger
    assert not fires(LONG, TARGET, LOWER, UPPER, TARGET)
    assert SHORT not in directions("SPOT")


def test_the_short_in_futures() -> None:
    assert setup_holds(SHORT, 20.58, LOWER, UPPER, 72.0, 20.05, 0.18)
    assert not setup_holds(SHORT, 20.58, LOWER, UPPER, 69.99, 20.05, 0.18)
    assert fires(SHORT, 20.40, LOWER, UPPER, TARGET)
    assert not fires(SHORT, 20.50, LOWER, UPPER, TARGET)  # still at the band
    assert invalidated(SHORT, 18.0, 20.31, 19.55, 20.30)  # out of the band against it
    assert not invalidated(SHORT, 18.0, 19.50, 19.55, 20.30)  # out of it in its favour


def test_the_context_needs_a_range_and_the_close_within_the_bands() -> None:
    assert context_holds(20.00, 19.60, 19.60, 20.30)  # on the band is within it
    assert not context_holds(20.01, 19.95, 19.60, 20.30)  # transicion
    assert not context_holds(17.40, 19.59, 19.60, 20.30)
    assert not context_holds(17.40, 20.31, 19.60, 20.30)
