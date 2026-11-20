"""One position (02 §12.4.2, §12.4.4 and §12.4.5), on minutes made by hand and with a ficha
whose exits each test sets: the gap of example B, the stop before the target, the target at
its level, absent minutes, the market exits, the trailing, the entry that does not happen,
the end of the window of example E (F3E4-02) and the two provenances (F3E4-03)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from kbot.backtest.execution import Costs
from kbot.backtest.position import EntryFailure, ExitReason, StopChange, simulate
from kbot.market.last_candles import STABILIZATION
from kbot.strategies.ficha import Ficha, Frames
from kbot.strategies.frames import ONE_MINUTE, build_frames, rest_only
from kbot.strategies.rules import LONG, SHORT, Check, Signal, Trail
from kbot.strategies.trend import TrendParams
from tests.unit.backtest.support import MINUTE, at, decision, flat
from tests.unit.strategies.support import contrasted

EXAMPLE = Costs(fee=Decimal("0.0006"), slippage=Decimal("0.0005"))
QUARTER = timedelta(minutes=15)


class Stub(Ficha):
    """A ficha without signals of its own: its exits are those each test sets, read on the
    candles of 15 m."""

    family = "tendencia"

    def __init__(
        self,
        *,
        target: float | None = None,
        limit: int | None = None,
        invalidations: tuple[datetime, ...] = (),
        trails: tuple[tuple[datetime, float], ...] = (),
    ) -> None:
        super().__init__(TrendParams(context="1h", signal="15m", execution="5m", trigger_candles=3))
        self._target, self._limit = target, limit
        self._invalidations, self._trails = set(invalidations), dict(trails)

    @property
    def invalidation_timeframe(self) -> str:
        return "15m"

    def invalidation(self, frames: Frames, signal: Signal, open_time: datetime) -> Check | None:
        point = frames["15m"].usable(open_time)
        if point is None or point.available_at is None:
            return None
        exits = open_time in self._invalidations
        return Check(point.available_at, exits, ((open_time, open_time + QUARTER),))

    def trailing(self, frames: Frames, signal: Signal, open_time: datetime) -> Trail | None:
        point = frames["15m"].usable(open_time)
        if open_time not in self._trails or point is None or point.available_at is None:
            return None
        return Trail(
            point.available_at, self._trails[open_time], ((open_time, open_time + QUARTER),)
        )

    def entry_target(self, signal: Signal, entry: float, stop: float) -> float | None:
        return self._target

    def time_limit(self, entry_minute: datetime) -> datetime | None:
        return entry_minute + timedelta(minutes=self._limit) if self._limit else None


def frames_of(minutes: list, end: datetime) -> Frames:
    kept = [m for m in minutes if m.candle.open_time < end]
    return build_frames(kept, downloaded_until=end, timeframes=("15m",))


def position(
    stub: Stub,
    *,
    changes: dict | None = None,
    skip: set | None = None,
    end: datetime | None = None,
    stop: float = 100.0,
    d: int = LONG,
    minutes: list | None = None,
):
    """A long decided at 10:20:15, entering at 10:21 at 101, with its stop at 100."""
    end = end or at(13)
    minutes = minutes or flat(at(10), 180, changes=changes, skip=skip)
    chosen = decision("tendencia", d, at(10, 20, 15), opened=at(10), stop=stop)
    return simulate(stub, frames_of(minutes, end), chosen, end, EXAMPLE)


def test_example_b_a_gap_through_the_stop() -> None:
    execution = position(Stub(), changes={at(12, 7): (99.20, 99.50, 98.90, 99.30)})
    trade = execution.trade
    assert trade is not None and trade.entry_minute == at(10, 21) and trade.entry_price == 101.0
    assert (trade.exit_reason, trade.exit_minute, trade.exit_price) == (
        ExitReason.STOP,
        at(12, 7),
        99.20,
    )
    assert trade.exit_fill == pytest.approx(99.1504, abs=1e-9)
    assert trade.exit_known_at == execution.liberation == at(12, 8, 15)


def test_the_stop_goes_before_the_target_in_the_same_minute() -> None:
    trade = position(Stub(target=102.0), changes={at(11): (101.0, 102.5, 99.9, 101.0)}).trade
    assert trade is not None
    assert (trade.exit_reason, trade.exit_price) == (ExitReason.STOP, 100.0)


def test_the_target_fills_at_its_level_never_better() -> None:
    trade = position(Stub(target=102.0), changes={at(11): (102.6, 102.8, 102.4, 102.7)}).trade
    assert trade is not None
    assert (trade.exit_reason, trade.exit_minute, trade.exit_price) == (
        ExitReason.TARGET,
        at(11),
        102.0,
    )


def test_an_absent_minute_fills_nothing() -> None:
    trade = position(Stub(), skip={at(11)}, changes={at(11, 1): (101.0, 101.0, 99.5, 100.5)}).trade
    assert trade is not None
    assert (trade.exit_reason, trade.exit_minute) == (ExitReason.STOP, at(11, 1))
    assert trade.exit_known_at == at(11, 2, 15)


def test_an_invalidation_exits_at_the_first_minute_present_after_its_instant() -> None:
    stub = Stub(invalidations=(at(10, 30),))  # [10:30, 10:45), available at 10:45:15
    trade = position(stub).trade
    assert trade is not None
    assert (trade.exit_reason, trade.exit_minute, trade.exit_price) == (
        ExitReason.INVALIDATION,
        at(10, 46),
        101.0,
    )
    later = position(stub, skip={at(10, 46)}).trade
    assert later is not None and later.exit_minute == at(10, 47)


def test_the_time_exit_from_its_limit() -> None:
    trade = position(Stub(limit=30)).trade  # entry at 10:21: from 10:51
    assert trade is not None
    assert (trade.exit_reason, trade.exit_minute) == (ExitReason.TIME, at(10, 51))
    later = position(Stub(limit=30), skip={at(10, 51)}).trade
    assert later is not None and later.exit_minute == at(10, 52)


def test_the_trailing_moves_the_stop_only_closer_and_for_the_minutes_after_it() -> None:
    stub = Stub(trails=((at(10, 15), 99.5), (at(10, 30), 100.5)))
    dip = (101.0, 101.0, 100.3, 101.0)
    trade = position(stub, changes={at(10, 45): dip, at(10, 46): dip}).trade
    assert trade is not None
    # 99.5 is farther and is not taken. The change is known when the minute of 10:45, which
    # holds its instant, is available: the position was still open then.
    assert trade.stop_changes == (StopChange(at(10, 45, 15), 100.5, at(10, 46, 15)),)
    # The minute of 10:45 holds 10:45:15, so it still has the stop of 100.
    assert (trade.exit_reason, trade.exit_minute, trade.exit_price) == (
        ExitReason.STOP,
        at(10, 46),
        100.5,
    )
    assert trade.initial_stop == 100.0 and trade.r == 1.0


def test_an_entry_needs_its_minute_and_its_checks() -> None:
    missing = position(Stub(), skip={at(10, 21)})
    assert (missing.trade, missing.failure) == (None, EntryFailure.NO_MINUTE)
    assert missing.liberation == at(10, 22, 15)  # CD-03: when the absence is known
    below = position(Stub(), changes={at(10, 21): (99.5, 99.6, 99.4, 99.5)})
    assert (below.trade, below.failure) == (None, EntryFailure.CHECKS)
    assert below.liberation == at(10, 22, 15)
    short = position(Stub(), d=SHORT, stop=100.0)  # a short with its stop below the price
    assert short.failure is EntryFailure.CHECKS


def test_example_e_the_liquidation_at_the_end_of_the_window() -> None:
    """F3E4-02: no exit at the end of the window or after. The position is liquidated at
    the close of the last minute present, known when the window resolves."""
    closing = {at(11, 59): (101.0, 101.2, 100.8, 101.1)}
    trade = position(Stub(), changes=closing, end=at(12)).trade
    assert trade is not None
    assert (trade.exit_reason, trade.exit_minute, trade.exit_price) == (
        ExitReason.LIQUIDATION,
        at(11, 59),
        101.1,
    )
    assert trade.exit_known_at == at(12, 0, 15)
    tail = {at(11, 31) + i * MINUTE for i in range(29)}  # 11:31 to 11:59 absent
    trade = position(Stub(), skip=tail, end=at(12)).trade
    assert trade is not None and trade.exit_minute == at(11, 30)
    assert trade.exit_known_at == at(12, 0, 15)  # not 11:31:15


def test_example_e_step_4_a_late_last_minute() -> None:
    """The residual of F3E4-02: 11:59 is absent (known at 12:00:15), and 11:58, the last
    present, is not available until 12:10. The liquidation is known at 12:10."""
    minutes = flat(at(10), 180, skip={at(11, 59)})
    late = [
        replace(m, available_at=at(12, 10)) if m.candle.open_time == at(11, 58) else m
        for m in minutes
    ]
    trade = position(Stub(), minutes=late, end=at(12)).trade
    assert trade is not None and trade.exit_minute == at(11, 58)
    assert trade.exit_known_at == at(12, 10)


def test_the_two_provenances() -> None:
    """F3E4-03: only REST from the minute of the entry on. The decision keeps 0 %; the result
    counts the minutes it used after it, and none after its exit."""
    minutes = [
        m if m.candle.open_time >= at(10, 21) else contrasted(m)
        for m in flat(at(10), 180, changes={at(10, 40): (101.0, 101.0, 99.5, 100.0)})
    ]
    chosen = decision(
        "tendencia",
        LONG,
        at(10, 20, 15),
        opened=at(10),
        stop=100.0,
        provenance=((at(10), at(10, 15)),),
    )
    frames = frames_of(minutes, at(13))
    trade = simulate(Stub(), frames, chosen, at(13), EXAMPLE).trade
    assert trade is not None and trade.exit_minute == at(10, 40)
    assert rest_only(trade.decision_provenance, frames[ONE_MINUTE]) == (15, 0)
    # [10:00, 10:15), the check of [10:15, 10:30) at 10:30:15 and the minutes 10:21 to 10:40;
    # the check of [10:30, 10:45), at 10:45:15, comes after the exit and does not count.
    assert trade.result_provenance == ((at(10), at(10, 41)),)
    assert rest_only(trade.result_provenance, frames[ONE_MINUTE]) == (41, 20)
    assert (trade.decision_rest_only, trade.result_rest_only) == ((15, 0), (41, 20))  # F3E4-05


def test_the_idealized_reference_is_apart() -> None:
    trade = position(Stub(), changes={at(10, 15): (100.7, 101.0, 100.5, 101.0)}).trade
    assert trade is not None and trade.ideal_open == 100.7  # the opening of t+1, 10:15
    assert trade.entry_price == 101.0  # the execution never uses it
    assert trade.signal_end == at(10, 15) and trade.signal_at == at(10, 15, 15)
    assert trade.entry_known_at == at(10, 21) + MINUTE + STABILIZATION
