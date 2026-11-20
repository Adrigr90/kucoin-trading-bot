"""The combiner (02 §12.4.3; F3-A8): one net position per instrument, with the examples C
and F of §12.4.6 (F3E4-01) and the same record in any order of arrival."""

from __future__ import annotations

import itertools
import random

import pytest

from kbot.strategies.combiner import Refusal, allocate, combine
from kbot.strategies.rules import LONG, SHORT, Outcome, execution_minute
from tests.unit.backtest.support import at, decision


def statuses(result: list) -> dict[tuple[str, int, object], Refusal | None]:
    return {(d.signal.family, d.signal.direction, d.at): refusal for d, refusal in result}


def test_example_c() -> None:
    liberation = {at(11, 0, 15): at(12, 8, 15), at(12, 15, 15): at(13, 0, 15)}
    liberation[at(14, 0, 15)] = at(15, 0, 15)
    decisions = [
        decision("tendencia", LONG, at(10, 0, 15)),  # 1: opposite at the same instant
        decision("reversion_media", SHORT, at(10, 0, 15)),
        decision("breakout", LONG, at(11, 0, 15)),  # 2: free, it enters at 11:01
        decision("tendencia", LONG, at(11, 15, 15)),  # 3: occupied
        decision("tendencia", LONG, at(12, 0, 15)),  # 4: still occupied until 12:08:15
        decision("reversion_media", LONG, at(12, 15, 15)),  # 4: free again
        decision("tendencia", LONG, at(14, 0, 15)),  # 5: both long: the trend goes first
        decision("breakout", LONG, at(14, 0, 15)),
    ]
    found = statuses(allocate(decisions, lambda d: liberation[d.at]))
    assert found == {
        ("tendencia", LONG, at(10, 0, 15)): Refusal.OPPOSITE,
        ("reversion_media", SHORT, at(10, 0, 15)): Refusal.OPPOSITE,
        ("breakout", LONG, at(11, 0, 15)): None,
        ("tendencia", LONG, at(11, 15, 15)): Refusal.OCCUPIED,
        ("tendencia", LONG, at(12, 0, 15)): Refusal.OCCUPIED,
        ("reversion_media", LONG, at(12, 15, 15)): None,
        ("tendencia", LONG, at(14, 0, 15)): None,
        ("breakout", LONG, at(14, 0, 15)): Refusal.TAKEN,
    }
    assert execution_minute(at(11, 0, 15)) == at(11, 1)
    assert execution_minute(at(12, 15, 15)) == at(12, 16)


def test_example_f_a_liberation_and_a_decision_at_the_same_instant() -> None:
    """F3E4-01: the liberation applies first, so the decision finds the instrument free."""
    liberation = {
        at(11, 0, 15): at(12, 0, 15),  # out by its stop in the minute of 11:59
        at(12, 0, 15): at(13, 0, 15),
        at(10, 5, 16): at(10, 7, 15),  # a failed entry: the minute of 10:06 is absent
        at(10, 7, 15): at(10, 30, 15),
    }
    decisions = [
        decision("breakout", LONG, at(11, 0, 15)),
        decision("tendencia", LONG, at(12, 0, 15)),
        decision("tendencia", LONG, at(10, 5, 16)),
        decision("reversion_media", SHORT, at(10, 7, 15)),
    ]
    found = statuses(allocate(decisions, lambda d: liberation[d.at]))
    assert set(found.values()) == {None}  # all four are executed
    assert execution_minute(at(12, 0, 15)) == at(12, 1)
    assert execution_minute(at(10, 7, 15)) == at(10, 8)


def test_opposite_decisions_at_a_liberation_none_enters() -> None:
    liberation = {at(11, 0, 15): at(12, 0, 15)}
    decisions = [
        decision("breakout", LONG, at(11, 0, 15)),
        decision("tendencia", LONG, at(12, 0, 15)),
        decision("reversion_media", SHORT, at(12, 0, 15)),
    ]
    found = statuses(allocate(decisions, lambda d: liberation[d.at]))
    assert found[("tendencia", LONG, at(12, 0, 15))] is Refusal.OPPOSITE
    assert found[("reversion_media", SHORT, at(12, 0, 15))] is Refusal.OPPOSITE


def test_the_order_of_arrival_changes_nothing() -> None:
    liberation = {
        at(10, 0, 15): at(10, 40, 15),
        at(10, 40, 15): at(11, 0, 15),
    }
    decisions = [
        decision("tendencia", LONG, at(10, 0, 15)),
        decision("breakout", LONG, at(10, 0, 15)),
        decision("reversion_media", LONG, at(10, 0, 15)),
        decision("breakout", LONG, at(10, 20, 15)),
        decision("reversion_media", SHORT, at(10, 40, 15)),
        decision("breakout", SHORT, at(10, 40, 15)),
    ]
    reference = allocate(decisions, lambda d: liberation[d.at])
    for order in itertools.islice(itertools.permutations(decisions), 0, 720, 13):
        assert allocate(list(order), lambda d: liberation[d.at]) == reference
    shuffled = list(decisions)
    random.Random(3).shuffle(shuffled)
    assert allocate(shuffled, lambda d: liberation[d.at]) == reference
    chosen = [d for d, refusal in reference if refusal is None]
    assert [(d.signal.family, d.at) for d in chosen] == [
        ("tendencia", at(10, 0, 15)),
        ("breakout", at(10, 40, 15)),  # all short: the breakout before mean reversion
    ]


def test_the_same_family_goes_by_the_oldest_signal_candle() -> None:
    older = decision("tendencia", LONG, at(10, 0, 15), opened=at(9, 15))
    newer = decision("tendencia", LONG, at(10, 0, 15), opened=at(9, 30))
    verdict = combine([newer, older], occupied=False)
    assert verdict.chosen == older and verdict.refused == ((newer, Refusal.TAKEN),)


def test_no_execution_at_the_end_of_the_window_or_after() -> None:
    """F3E4-02: the decision of 23:59:15 would enter at 00:00, the end of the window."""
    late = decision("tendencia", LONG, at(23, 59, 15))
    end = execution_minute(late.at)
    assert combine([late], occupied=False, window_end=end).refused == ((late, Refusal.WINDOW_END),)
    early = decision("tendencia", LONG, at(23, 58, 15))
    assert combine([early], occupied=False, window_end=end).chosen == early


def test_occupied_goes_before_opposite() -> None:
    pair = [decision("tendencia", LONG, at(10, 0, 15)), decision("breakout", SHORT, at(10, 0, 15))]
    assert {r for _, r in combine(pair, occupied=True).refused} == {Refusal.OCCUPIED}


def test_the_combiner_takes_only_decisions_of_one_instant() -> None:
    expired = decision("tendencia", LONG, at(10, 0, 15))
    expired = type(expired)(expired.signal, Outcome.NO_TRIGGER, expired.at, None, None, None, ())
    with pytest.raises(ValueError, match="only takes decisions"):
        combine([expired], occupied=False)
    with pytest.raises(ValueError, match="one instant"):
        combine(
            [decision("tendencia", LONG, at(10, 0, 15)), decision("breakout", LONG, at(10, 5, 15))],
            occupied=False,
        )
