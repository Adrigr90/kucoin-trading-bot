"""What the three fichas share (02 §12.3.1): directions, the context candle, the walk of the
trigger (F3E3-01), the execution minute, the entry checks (F3E3-02), the level exits and
the trailing stop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from kbot.strategies.breakout import target
from kbot.strategies.rules import (
    LONG,
    SHORT,
    LevelExit,
    Outcome,
    TriggerCandle,
    context_open,
    directions,
    execution_minute,
    initial_stop,
    level_exit,
    may_enter,
    trail,
    walk,
)

DAY = datetime(2026, 3, 10, tzinfo=UTC)
H4, H1, M15 = timedelta(hours=4), timedelta(hours=1), timedelta(minutes=15)


def at(hours: int, minutes: int = 0, seconds: int = 0) -> datetime:
    return DAY + timedelta(hours=hours, minutes=minutes, seconds=seconds)


def test_spot_only_goes_long() -> None:
    assert directions("SPOT") == (LONG,)
    assert directions("FUTURES") == (LONG, SHORT)
    with pytest.raises(ValueError, match="unknown market"):
        directions("MARGIN")


def test_the_context_candle_is_the_last_ended() -> None:
    assert context_open(at(15, 0, 15), H4) == at(8)  # [12:00, 16:00) has not ended
    assert context_open(at(16, 0, 15), H4) == at(12)
    assert context_open(at(16), H4) == at(12)  # ended at 16:00, that is, at D
    assert context_open(at(13, 30, 15), H1) == at(12)
    assert context_open(at(15, 30, 15), H4) == at(8)


def trigger(opened: datetime, resolved: datetime, close: float | None) -> TriggerCandle:
    return TriggerCandle(opened, resolved, close)


def above(level: float):
    return lambda close: close > level


def test_the_instants_of_the_trigger_never_go_back() -> None:
    """F3E3-01, the reviewer's case: the first candle is not available until 15:50:00 and
    the second is at 15:30:15. No decision comes before 15:50:00."""
    asked: list[datetime] = []

    def context(instant: datetime) -> bool:
        asked.append(instant)
        return True

    first = trigger(at(15), at(15, 50), 99.0)
    second = trigger(at(15, 15), at(15, 30, 15), 101.0)
    result = walk(at(15, 0, 15), [first, second], above(100.0), context)
    assert result.outcome is Outcome.DECISION
    assert result.at == at(15, 50) and result.candle == second
    assert execution_minute(result.at) == at(15, 51)  # never 15:31
    assert asked == [at(15, 50), at(15, 50)]  # the context, at the effective instants
    # F3E3-03: both candles were looked at, each at its instant.
    assert result.examined == ((first, at(15, 50)), (second, at(15, 50)))


def test_the_first_late_candle_that_fires_decides_at_its_instant() -> None:
    first = trigger(at(15), at(15, 50), 101.0)
    second = trigger(at(15, 15), at(15, 30, 15), 101.0)
    result = walk(at(15, 0, 15), [first, second], above(100.0), lambda _: True)
    assert (result.outcome, result.at, result.candle) == (Outcome.DECISION, at(15, 50), first)


def test_a_late_unusable_first_candle_makes_the_signal_expire() -> None:
    first = trigger(at(15), at(15, 50), None)
    second = trigger(at(15, 15), at(15, 30, 15), 101.0)
    result = walk(at(15, 0, 15), [first, second], above(100.0), lambda _: True)
    assert (result.outcome, result.at, result.candle) == (Outcome.UNUSABLE, at(15, 50), None)
    assert result.examined == ()  # no close was looked at


def test_the_walk_keeps_the_candles_it_looked_at() -> None:
    """F3E3-03: the inputs of each outcome are the candles whose close was looked at, the
    one that fires included; never those after it."""
    candles = [trigger(at(15, 15 * i), at(15, 15 * i + 15, 15), 99.0 + i) for i in range(4)]
    decided = walk(at(15, 0, 15), candles, above(100.5), lambda _: True)
    assert decided.candle == candles[2]
    assert decided.examined == tuple((c, c.resolved_at) for c in candles[:3])
    expired = walk(at(15, 0, 15), candles, above(105.0), lambda _: True)
    assert expired.outcome is Outcome.NO_TRIGGER
    assert expired.examined == tuple((c, c.resolved_at) for c in candles)
    cut = walk(at(15, 0, 15), candles, above(105.0), lambda instant: instant < at(15, 45))
    assert cut.outcome is Outcome.CONTEXT
    assert cut.examined == tuple((c, c.resolved_at) for c in candles[:2])


def test_with_the_usual_availabilities_each_candle_decides_at_its_own() -> None:
    candles = [trigger(at(15, 15 * i), at(15, 15 * i + 15, 15), 99.0 + i) for i in range(4)]
    result = walk(at(15, 0, 15), candles, above(99.5), lambda _: True)
    assert (result.outcome, result.at) == (Outcome.DECISION, at(15, 30, 15))
    assert result.candle == candles[1]


def test_a_context_that_no_longer_holds_makes_the_signal_expire() -> None:
    candles = [trigger(at(15, 45), at(16, 0, 15), 101.0)]
    result = walk(at(15, 0, 15), candles, above(100.0), lambda instant: instant < at(16))
    assert (result.outcome, result.at) == (Outcome.CONTEXT, at(16, 0, 15))


def test_without_a_trigger_the_signal_expires_after_the_k_candles() -> None:
    closes = [99.0, 99.5, 98.0, 100.0]  # the last ties with the level: not beyond it
    candles = [trigger(at(15, 15 * i), at(15, 15 * i + 15, 15), c) for i, c in enumerate(closes)]
    result = walk(at(15, 0, 15), candles, above(100.0), lambda _: True)
    assert (result.outcome, result.at) == (Outcome.NO_TRIGGER, at(16, 0, 15))


def test_the_execution_minute_starts_after_the_decision() -> None:
    assert execution_minute(at(15, 30, 15)) == at(15, 31)
    assert execution_minute(at(15, 50)) == at(15, 51)  # a minute that starts at D is not after


def test_no_entry_with_a_stop_or_a_target_that_is_not_positive() -> None:
    """F3E3-02: trigger close 1, ATR 1, m = 2 and entry 1."""
    two = Decimal("2")
    long_stop = initial_stop(LONG, 1.0, 1.0, two)
    assert long_stop == -1.0
    assert not may_enter(LONG, 1.0, long_stop)  # though r = 2 > 0
    short_stop = initial_stop(SHORT, 1.0, 1.0, two)
    short_target = target(SHORT, 1.0, short_stop, two)
    assert (short_stop, short_target) == (3.0, -3.0)
    assert not may_enter(SHORT, 1.0, short_stop, short_target)
    assert not may_enter(LONG, 1.0, 0.0)  # a level of exactly 0
    assert not may_enter(SHORT, 1.0, 3.0, 0.0)
    assert may_enter(LONG, 1.0, 0.5) and may_enter(SHORT, 1.0, 3.0, 0.5)  # positive levels


def test_no_entry_with_the_price_beyond_the_stop_or_the_target() -> None:
    assert not may_enter(LONG, 3020.0, 3023.6)  # §6.2.5
    assert not may_enter(SHORT, 141.0, 140.3)
    assert not may_enter(LONG, 20.2, 19.38, 20.1)  # the target is already passed
    assert may_enter(LONG, 19.75, 19.38, 20.1)


def test_the_stop_goes_first_when_both_levels_are_touched_in_a_minute() -> None:
    assert level_exit(LONG, 99.0, 106.0, 99.5, 105.0) is LevelExit.STOP
    assert level_exit(LONG, 99.6, 106.0, 99.5, 105.0) is LevelExit.TARGET
    assert level_exit(LONG, 99.6, 104.9, 99.5, 105.0) is None
    assert level_exit(SHORT, 94.0, 101.0, 100.5, 95.0) is LevelExit.STOP
    assert level_exit(SHORT, 95.0, 100.4, 100.5, 95.0) is LevelExit.TARGET
    assert level_exit(LONG, 99.5, 100.0, 99.5) is LevelExit.STOP  # touching is enough


def test_the_trailing_stop_only_comes_closer() -> None:
    three = Decimal("3")
    assert trail(LONG, 100.0, 110.0, 2.0, three) == 104.0
    assert trail(LONG, 104.0, 107.0, 2.0, three) == 104.0
    assert trail(SHORT, 100.0, 90.0, 2.0, three) == 96.0
    assert trail(SHORT, 96.0, 93.0, 2.0, three) == 96.0
