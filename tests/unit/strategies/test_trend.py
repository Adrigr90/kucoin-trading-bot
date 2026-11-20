"""The trend ficha (02 §12.3.4; F3-A6): its example 1, a long in Futures ETHUSDTM, and its
variants, with the values the ficha gives. Levels are compared with the price tolerance of
§12.2, point 5."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
    trail,
    walk,
)
from kbot.strategies.trend import TrendParams, context_holds, invalidated, setup_holds
from tests.unit.indicators.support import PRICE_UNITS, close_enough

DAY = datetime(2025, 3, 10, tzinfo=UTC)
P = TrendParams()
SCALE = 3100.0


def at(hours: int, minutes: int = 0, seconds: int = 0) -> datetime:
    return DAY + timedelta(hours=hours, minutes=minutes, seconds=seconds)


def same(ours: float, expected: float) -> bool:
    return close_enough(ours, expected, PRICE_UNITS, SCALE)


# The 1 h candles t-4 to t, [10:00, 11:00) to [14:00, 15:00): lows and their EMA(20).
LOWS = [3030.00, 3025.50, 3019.00, 3027.00, 3033.00]
EMA20 = [3018.20, 3019.40, 3021.75, 3023.10, 3024.80]  # only [12:00, 13:00) touches it
HIGH, LOW, CLOSE, EMA50, ATR = 3046.00, 3033.00, 3041.20, 3002.60, 12.50


def candles(closes: list[float | None]) -> list[TriggerCandle]:
    """The 15 m candles from 15:00, each available at its end plus 15 s."""
    return [
        TriggerCandle(at(15, 15 * i), at(15, 15 * i + 15, 15), close)
        for i, close in enumerate(closes)
    ]


def fires(close: float) -> bool:
    return close > HIGH


def test_example_1() -> None:
    # 1. Signal at the 1 h candle [14:00, 15:00), D_s = 15:00:15, context [08:00, 12:00).
    assert context_holds(LONG, 3052.40, 2987.10, 27.80, 26.50, 13.20)
    assert setup_holds(LONG, CLOSE, EMA50, LOWS, EMA20, -0.85, 1.30)
    # 2. Trigger: 3043.80 does not pass 3046.00; 3048.60 does, decided at 15:30:15.
    result = walk(at(15, 0, 15), candles([3043.80, 3048.60, 3050.0, 3051.0]), fires, lambda _: True)
    assert (result.outcome, result.at) == (Outcome.DECISION, at(15, 30, 15))
    assert result.candle is not None and result.candle.close == 3048.60
    stop = initial_stop(LONG, 3048.60, ATR, P.stop_atr)
    assert same(stop, 3023.60)
    # 3. Entry at the opening of 15:31, 3049.10: r = 25.50.
    assert execution_minute(result.at) == at(15, 31)
    assert may_enter(LONG, 3049.10, stop)
    assert same(risk(LONG, 3049.10, stop), 25.50)
    # 4. Trailing at the 1 h candles that end after the entry.
    stop = trail(LONG, stop, 3061.00, 12.60, P.trail_atr)  # 3023.20: no change
    assert same(stop, 3023.60)
    stop = trail(LONG, stop, 3092.00, 13.00, P.trail_atr)
    assert same(stop, 3053.00)
    stop = trail(LONG, stop, 3085.40, 13.20, P.trail_atr)  # 3045.80: no change
    assert same(stop, 3053.00)
    assert not invalidated(LONG, 3060.00, 3001.00, 26.10)  # the 4 h [12:00, 16:00)
    # 5. The minute of 18:12 touches the stop: about +0.15 R at its level, before costs.
    assert level_exit(LONG, 3051.50, 3058.00, stop) is LevelExit.STOP
    assert round((stop - 3049.10) / 25.50, 2) == 0.15


def test_the_variants_of_example_1() -> None:
    assert invalidated(LONG, 3060.00, 3001.00, 19.60)  # direction rango at 20:00:15
    # The four 15 m closes do not pass 3046.00; the last ties with it.
    result = walk(
        at(15, 0, 15), candles([3043.80, 3045.90, 3040.10, 3046.00]), fires, lambda _: True
    )
    assert (result.outcome, result.at) == (Outcome.NO_TRIGGER, at(16, 0, 15))
    # [15:15, 15:30) incomplete: the signal expires, though the next candle would fire.
    result = walk(at(15, 0, 15), candles([3043.80, None, 3050.00, 3051.00]), fires, lambda _: True)
    assert result.outcome is Outcome.UNUSABLE
    # The minute of 15:31 opens at 3020.00, past the stop of 3023.60: no entry.
    assert not may_enter(LONG, 3020.00, initial_stop(LONG, 3048.60, ATR, P.stop_atr))
    # The frontier of the ADX of 4 h.
    assert context_holds(LONG, 3052.40, 2987.10, 25.00, 26.50, 13.20)
    assert not context_holds(LONG, 3052.40, 2987.10, 24.99, 26.50, 13.20)


def test_the_same_conditions_short_give_no_signal_in_spot() -> None:
    highs = [2960.00, 2965.50, 2972.00, 2964.00, 2958.00]
    ema20 = [2971.80, 2970.60, 2968.25, 2966.90, 2965.20]  # [12:00, 13:00) touches it
    assert context_holds(SHORT, 2947.60, 3012.90, 27.80, 13.20, 26.50)
    assert setup_holds(SHORT, 2949.80, 2997.40, highs, ema20, 0.85, -1.30)
    assert SHORT in directions("FUTURES")
    assert SHORT not in directions("SPOT")


def test_the_pullback_needs_a_touch_and_the_macd_a_crossing() -> None:
    no_touch = [low + 20.0 for low in LOWS]
    assert not setup_holds(LONG, CLOSE, EMA50, no_touch, EMA20, -0.85, 1.30)
    assert not setup_holds(LONG, CLOSE, EMA50, LOWS, EMA20, 0.10, 1.30)  # already above 0
    assert not setup_holds(LONG, CLOSE, EMA50, LOWS, EMA20, -0.85, 0.0)  # not above 0 yet
    assert setup_holds(LONG, CLOSE, EMA50, LOWS, EMA20, 0.0, 1.30)  # from exactly 0
    assert not setup_holds(LONG, 3002.60, EMA50, LOWS, EMA20, -0.85, 1.30)  # at EMA(50)


def test_the_invalidation_of_a_short() -> None:
    assert invalidated(SHORT, 2990.0, 2990.0, 30.0)  # EMA(20) no longer below
    assert not invalidated(SHORT, 2980.0, 2990.0, 22.0)  # transicion: it goes on
    assert invalidated(SHORT, 2980.0, 2990.0, 20.0)  # rango


def test_the_parameters_are_those_of_the_ficha() -> None:
    assert (P.context, P.signal, P.execution, P.trigger_candles) == ("4h", "1h", "15m", 4)
    assert (P.stop_atr, P.trail_atr) == (Decimal("2"), Decimal("3"))
