"""The figures of the report: the maximum drawdown without order (F3E5-02), the summaries
and the costs doubled (``02`` §12.5.4 and §12.5.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import permutations

import pytest

from kbot.backtest.execution import net_r
from kbot.backtest.fees import STRESS
from kbot.backtest.position import ExitReason, Trade
from kbot.backtest.report import max_drawdown, stressed_r, summary
from kbot.strategies.rules import LONG, SHORT
from tests.unit.backtest.history import fee

T0 = datetime(2025, 6, 2, 10, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)


def test_f3e5_02_the_maximum_drawdown_does_not_depend_on_the_order() -> None:
    """After -1 R, two trades of two instruments exit in the same minute with +2 R and -1 R:
    one step of +1 R, so the drawdown is 1 R in any order."""
    exits = [(T0, -1.0), (T1, 2.0), (T1, -1.0)]
    for order in permutations(exits):
        assert max_drawdown(order) == 1.0
        assert summary(list(order))["caida_maxima"] == 1.0
    # One at a time, the order -1, +2 would have given 2 R: the minute is one step.
    assert max_drawdown([(T0, -1.0), (T1, -1.0), (T1 + timedelta(minutes=1), 2.0)]) == 2.0


def test_the_curve_starts_at_zero() -> None:
    assert max_drawdown([]) == 0.0
    assert max_drawdown([(T0, -0.5)]) == 0.5  # a first loss falls from the initial 0
    assert max_drawdown([(T0, 1.0), (T1, 2.0)]) == 0.0


def test_the_summary_of_a_group() -> None:
    values = [(T0, 1.5), (T0, -1.0), (T1, 0.25), (T1, -0.5)]
    assert summary(values) == {
        "operaciones": 4,
        "positivas": 2,
        "media": 0.0625,
        "mediana": -0.125,
        "suma": 0.25,
        "peor": -1.0,
        "mejor": 1.5,
        "caida_maxima": 0.25,
    }
    empty = summary([])
    assert (empty["operaciones"], empty["media"], empty["suma"]) == (0, None, 0.0)
    # The sums are exactly rounded: the order of the trades changes no figure.
    tiny = [(T0, 1e16), (T0, 1.0), (T1, -1e16)]
    assert all(summary(list(o))["suma"] == 1.0 for o in permutations(tiny))


def trade(d: int, entry: float, exit_: float, stop: float) -> Trade:
    costs = fee("FUTURES", "XUSDTM", "0.0006").costs()
    r = d * (entry - stop)
    entry_fill, exit_fill = costs.entry(d, entry), costs.exit(d, exit_)
    return Trade(
        "tendencia",
        "FUTURES",
        "XUSDTM",
        "1h",
        d,
        T0,
        T0,
        T0,
        T0,
        T0,
        T0,
        entry,
        entry_fill,
        stop,
        None,
        r,
        (),
        ExitReason.TARGET,
        T1,
        exit_,
        exit_fill,
        T1,
        d * (exit_ - entry) / r,
        net_r(d, entry_fill, exit_fill, r, costs.fee),
        (),
        (),
        (0, 0),
        (0, 0),
        None,
    )


@pytest.mark.parametrize("d", [LONG, SHORT])
def test_the_net_r_with_the_costs_doubled(d: int) -> None:
    entry, exit_ = 100.0, 100.0 + d * 4.0
    t = trade(d, entry, exit_, entry - d * 2.0)
    assert t.gross_r == 2.0
    # By hand: fee 0.12 % per side and slippage 0.1 % per fill.
    e, x = entry * (1 + d * 0.001), exit_ * (1 - d * 0.001)
    expected = (d * (x - e) - 0.0012 * (e + x)) / 2.0
    assert stressed_r(t, fee("FUTURES", "XUSDTM", "0.0006")) == pytest.approx(expected, abs=1e-12)
    assert stressed_r(t, fee("FUTURES", "XUSDTM", "0.0006")) < t.net_r < t.gross_r
    # With the costs once, the same computation gives the net R of the trade.
    once = fee("FUTURES", "XUSDTM", "0.0006").costs()
    again = net_r(d, once.entry(d, entry), once.exit(d, exit_), t.r, once.fee)
    assert again == t.net_r
    assert STRESS == 2
