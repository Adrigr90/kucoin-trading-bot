"""The engine (02 §12.4.5 and §12.4.7): F3-A9 and the poisoning of the whole engine, on four
days of minutes of a random walk with the three fichas of short periods, alone and combined.

* Every trade keeps end + margin <= D_s <= D < execution, and executes in the first minute
  after its decision.
* An absent execution minute makes the signal expire (CD-03).
* A late candle delays its use, and nothing comes before its new availability.
* Running again, or with the fichas in another order, gives the same record.
* Changing everything after an instant X changes nothing known by X (F3-A4).
* The end of the window (F3E4-02) and the two shares only REST (F3E4-03)."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from kbot.backtest.engine import CHOSEN, COMBINED, EXECUTED, RepeatedSignal, Run, Window, run
from kbot.backtest.execution import Costs
from kbot.backtest.position import ExitReason
from kbot.market.last_candles import STABILIZATION
from kbot.strategies.ficha import Frames
from kbot.strategies.frames import ONE_MINUTE, build_frames, rest_only
from kbot.strategies.rules import LONG, Signal, execution_minute
from kbot.strategies.trend import Trend
from tests.unit.backtest.support import Fixed, at, decision, flat
from tests.unit.strategies.support import (
    MINUTE,
    MINUTES,
    START,
    UNTIL,
    contrasted,
    poisoned,
    short_fichas,
)

EXAMPLE = Costs(fee=Decimal("0.0006"), slippage=Decimal("0.0005"))
WINDOW = Window(START + timedelta(days=1), UNTIL)


@pytest.fixture(scope="module")
def combined() -> Run:
    return run(short_fichas(), MINUTES, WINDOW, EXAMPLE)


@pytest.fixture(scope="module", params=range(3), ids=[f.family for f in short_fichas()])
def alone(request: pytest.FixtureRequest) -> Run:
    return run([short_fichas()[request.param]], MINUTES, WINDOW, EXAMPLE)


def test_the_four_instants_of_every_trade(combined: Run, alone: Run) -> None:
    for result in (combined, alone):
        assert result.trades
        for trade in result.trades:
            assert trade.signal_end + STABILIZATION <= trade.signal_at
            assert trade.signal_at <= trade.decision_at < trade.entry_minute
            assert trade.entry_minute == execution_minute(trade.decision_at)
            assert WINDOW.holds(trade.decision_at) and trade.entry_minute < WINDOW.end
            assert trade.entry_minute <= trade.exit_minute < WINDOW.end
            assert trade.exit_known_at > trade.exit_minute


def test_the_combined_run_holds_one_position_at_a_time(combined: Run) -> None:
    assert combined.mode == COMBINED
    trades = combined.trades
    assert len({t.family for t in trades}) > 1
    for before, after in itertools.pairwise(trades):
        assert before.exit_known_at <= after.decision_at  # [decision, liberation)
    assert {r.status for r in combined.decisions} >= {CHOSEN, "ocupado"}
    assert {r.entry for r in combined.decisions if r.status == CHOSEN} >= {EXECUTED}


def test_running_again_or_in_another_order_gives_the_same_record(combined: Run) -> None:
    assert run(short_fichas(), MINUTES, WINDOW, EXAMPLE) == combined
    assert run(list(reversed(short_fichas())), MINUTES, WINDOW, EXAMPLE) == combined


def test_an_absent_execution_minute_makes_the_signal_expire(combined: Run) -> None:
    trade = combined.trades[0]
    without = [m for m in MINUTES if m.candle.open_time != trade.entry_minute]
    again = run(short_fichas(), without, WINDOW, EXAMPLE)
    record = next(
        r
        for r in again.decisions
        if (r.decision.signal.family, r.decision.signal.open_time, r.decision.at)
        == (trade.family, trade.signal_open, trade.decision_at)
    )
    assert (record.status, record.entry) == (CHOSEN, "caduca_sin_minuto")
    assert record.entry_known_at == trade.entry_minute + MINUTE + STABILIZATION
    assert all(t.decision_at != trade.decision_at for t in again.trades)


def test_a_late_candle_delays_its_use(combined: Run) -> None:
    """F3-A9: the last minute of the signal candle of each trade comes 10 minutes late. Its
    signal, its decision and its execution never come before the new availability, and at
    least some of them still come."""
    delay = timedelta(minutes=10)
    still = 0
    for trade in combined.trades[:4]:
        last = trade.signal_end - MINUTE
        late = [
            replace(m, available_at=m.available_at + delay) if m.candle.open_time == last else m
            for m in MINUTES
        ]
        again = run(short_fichas(), late, WINDOW, EXAMPLE)
        new_availability = trade.signal_at + delay
        key = (trade.family, trade.signal_open, trade.direction)
        signals = [s for s in again.signals if (s.family, s.open_time, s.direction) == key]
        for signal in signals:
            assert signal.at >= new_availability
        for record in again.decisions:
            if record.decision.signal in signals:
                assert record.decision.at >= new_availability
        for moved in again.trades:
            if (moved.family, moved.signal_open, moved.direction) == key:
                assert moved.decision_at >= new_availability < moved.entry_minute
                still += 1
    assert still > 0


def known(result: Run, cut: datetime) -> dict[str, list[object]]:
    """Everything of a run whose knowledge instant is by ``cut``."""
    entries, stops, exits = [], [], []
    for t in result.trades:
        if t.entry_known_at <= cut:
            entry = (t.family, t.signal_open, t.direction, t.decision_at, t.entry_minute)
            entries.append(
                (*entry, t.entry_price, t.initial_stop, t.target, t.r, t.decision_rest_only)
            )
        stops.extend((t.signal_open, c) for c in t.stop_changes if c.known_at <= cut)
        if t.exit_known_at <= cut:
            exit_ = (t.signal_open, t.exit_reason, t.exit_minute, t.exit_price, t.net_r)
            exits.append((*exit_, t.result_rest_only))
    decisions = [(r.decision, r.status) for r in result.decisions if r.decision.at <= cut]
    entered = [
        (r.decision, r.entry)
        for r in result.decisions
        if r.entry_known_at is not None and r.entry_known_at <= cut
    ]
    signals: list[object] = [s for s in result.signals if s.at <= cut]
    return {
        "signals": signals,
        "decisions": decisions,
        "entered": entered,
        "entries": entries,
        "stops": stops,
        "exits": exits,
    }


def test_poisoning_after_an_instant_changes_nothing_known_by_it(combined: Run) -> None:
    """F3-A4 of the whole engine, at several cuts: the decision of a trade, the knowledge of
    its entry, a moment in the middle and the knowledge of its exit."""
    cuts = []
    for trade in combined.trades[:3]:
        middle = trade.entry_minute + (trade.exit_minute - trade.entry_minute) / 2
        cuts += [trade.decision_at, trade.entry_known_at, middle, trade.exit_known_at]
    for index, cut in enumerate(cuts):
        again = run(short_fichas(), poisoned(MINUTES, cut, seed=index), WINDOW, EXAMPLE)
        assert known(again, cut) == known(combined, cut), cut


def test_a_decision_whose_execution_falls_at_the_end_of_the_window(combined: Run) -> None:
    """F3E4-02: with the window ending at the execution minute of a trade, its decision does
    not enter, and nothing of the following minutes enters the run."""
    trade = combined.trades[1]
    shorter = Window(WINDOW.start, trade.entry_minute)
    again = run(short_fichas(), MINUTES, shorter, EXAMPLE)
    record = next(r for r in again.decisions if r.decision.at == trade.decision_at)
    assert record.status == "fin_de_ventana"
    assert all(t.exit_minute < shorter.end for t in again.trades)


def test_the_liquidation_is_known_when_the_window_resolves(combined: Run) -> None:
    trade = next(t for t in combined.trades if t.exit_minute - t.entry_minute > 30 * MINUTE)
    end = trade.entry_minute + 20 * MINUTE
    window = Window(WINDOW.start, end)
    cut = next(
        t
        for t in run(short_fichas(), MINUTES, window, EXAMPLE).trades
        if t.signal_open == trade.signal_open
    )
    assert cut.exit_reason is ExitReason.LIQUIDATION
    assert cut.exit_minute == end - MINUTE and cut.exit_known_at == end + STABILIZATION
    # The last minute present comes late (step 4 of example E): so does the liquidation.
    late = [
        replace(m, available_at=end + 10 * MINUTE) if m.candle.open_time == end - MINUTE else m
        for m in MINUTES
    ]
    delayed = run(short_fichas(), late, window, EXAMPLE)
    moved = next(t for t in delayed.trades if t.signal_open == trade.signal_open)
    assert moved.exit_known_at == end + 10 * MINUTE
    # Poisoning after a cut 5 minutes after the end changes nothing known by then.
    again = run(short_fichas(), poisoned(late, end + 5 * MINUTE, seed=1), window, EXAMPLE)
    assert known(again, end + 5 * MINUTE) == known(delayed, end + 5 * MINUTE)


def test_the_two_shares_only_rest(combined: Run) -> None:
    """F3E4-03: only REST after the decision of a trade. The decision keeps 0 %; the result
    counts the minutes it used after the decision, and none after its exit."""
    trade = combined.trades[0]
    decided = trade.decision_at
    mixed = [m if m.available_at > decided else contrasted(m) for m in MINUTES]
    again = run(short_fichas(), mixed, WINDOW, EXAMPLE)
    same = next(t for t in again.trades if t.decision_at == decided)
    minutes = build_frames(
        [m for m in mixed if m.candle.open_time < WINDOW.end],
        downloaded_until=WINDOW.end,
        timeframes=(),
    )[ONE_MINUTE]
    assert rest_only(same.decision_provenance, minutes)[1] == 0
    later = sum(
        1
        for start, end in same.result_provenance
        for opened in minutes.span(start, end)
        if minutes.points[opened].available_at is not None
        and minutes.points[opened].available_at > decided
    )
    total, rest = rest_only(same.result_provenance, minutes)
    assert rest == later > 0 and total > rest
    assert same.result_provenance[-1][1] == same.exit_minute + MINUTE  # nothing after the exit


class Repeating(Trend):
    """A trend that gives its first signal twice."""

    def signals(self, frames: Frames) -> list[Signal]:
        found = super().signals(frames)
        return [found[0], *found]


def test_a_repeated_signal_stops_the_run() -> None:
    trend = short_fichas()[0]
    with pytest.raises(ValueError, match="one ficha per family"):
        run([trend, trend], MINUTES, WINDOW, EXAMPLE)
    with pytest.raises(RepeatedSignal, match="two signals with the key"):
        run([Repeating(trend.params)], MINUTES, WINDOW, EXAMPLE)


def fixed_run(minutes: list, chosen) -> Run:
    return run([Fixed(chosen)], minutes, Window(at(10), at(13)), EXAMPLE)


def test_the_record_keeps_when_a_failed_entry_is_known() -> None:
    """F3E4-04: the same decision at 10:20:15 and the same refusal of its entry, whose minute
    opens past the stop. In one run that minute is available at 10:22:15; in the other, at
    12:00. The records differ, and at 11:00 only the first knows the refusal."""
    chosen = decision("tendencia", LONG, at(10, 20, 15), opened=at(10), stop=100.0)
    below = flat(at(10), 180, changes={at(10, 21): (99.5, 99.6, 99.4, 99.5)})
    late = [
        replace(m, available_at=at(12)) if m.candle.open_time == at(10, 21) else m for m in below
    ]
    soon, later = fixed_run(below, chosen), fixed_run(late, chosen)
    (first,), (second,) = soon.decisions, later.decisions
    assert (first.status, first.entry, first.entry_known_at) == (CHOSEN, "no_entra", at(10, 22, 15))
    assert (second.status, second.entry, second.entry_known_at) == (CHOSEN, "no_entra", at(12))
    assert soon != later
    assert known(soon, at(11))["entered"] == [(chosen, "no_entra")]
    assert known(later, at(11))["entered"] == []
    assert known(soon, at(11))["decisions"] == known(later, at(11))["decisions"]  # chosen at D


def test_the_record_keeps_the_two_shares_only_rest(combined: Run) -> None:
    """F3E4-05, read straight from the run: 100 % with every minute only REST, 0 % with every
    minute contrasted, and the mixed case of §12.4.7, (15, 0) and (41, 20)."""
    for trade in combined.trades:  # the history: every minute only REST
        assert trade.decision_rest_only[0] == trade.decision_rest_only[1] > 0
        assert trade.result_rest_only[0] == trade.result_rest_only[1] > 0
    confirmed = run(short_fichas(), [contrasted(m) for m in MINUTES], WINDOW, EXAMPLE)
    assert confirmed != combined and confirmed.trades
    for trade in confirmed.trades:
        assert trade.decision_rest_only[1] == trade.result_rest_only[1] == 0
    chosen = decision(
        "tendencia",
        LONG,
        at(10, 20, 15),
        opened=at(10),
        stop=100.0,
        provenance=((at(10), at(10, 15)),),
    )
    mixed = [
        m if m.candle.open_time >= at(10, 21) else contrasted(m)
        for m in flat(at(10), 180, changes={at(10, 40): (101.0, 101.0, 99.5, 100.0)})
    ]
    (trade,) = fixed_run(mixed, chosen).trades
    assert (trade.decision_rest_only, trade.result_rest_only) == ((15, 0), (41, 20))
