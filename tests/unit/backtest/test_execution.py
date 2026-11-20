"""The execution model of the F3 (02 §12.4.4): fills, costs and results in R, with the
examples A and B of §12.4.6. The costs are the values of the examples, not those of F3-E5."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kbot.backtest.execution import Costs, gross_r, net_r, stop_fill
from kbot.strategies.rules import LONG, SHORT, risk

EXAMPLE = Costs(fee=Decimal("0.0006"), slippage=Decimal("0.0005"))


def test_example_a_a_trade_with_costs() -> None:
    entry, stop = 3049.10, 3023.60
    r = risk(LONG, entry, stop)
    exit_ = stop_fill(LONG, 3053.00, 3054.20)  # the minute does not open below the stop
    assert exit_ == 3053.00
    entry_fill, exit_fill = EXAMPLE.entry(LONG, entry), EXAMPLE.exit(LONG, exit_)
    assert (round(entry_fill, 2), round(exit_fill, 2)) == (3050.62, 3051.47)
    assert gross_r(LONG, entry, exit_, r) == pytest.approx(0.15294, abs=1e-5)
    assert net_r(LONG, entry_fill, exit_fill, r, EXAMPLE.fee) == pytest.approx(-0.11029, abs=1e-5)


def test_example_b_a_gap_through_the_stop() -> None:
    fill = stop_fill(LONG, 100.00, 99.20)
    assert fill == 99.20  # at the opening, not at 100.00
    assert EXAMPLE.exit(LONG, fill) == pytest.approx(99.1504, abs=1e-9)


def test_a_short_sells_on_entry_and_buys_on_exit() -> None:
    assert EXAMPLE.entry(SHORT, 100.0) == pytest.approx(99.95)
    assert EXAMPLE.exit(SHORT, 100.0) == pytest.approx(100.05)
    assert stop_fill(SHORT, 100.0, 100.8) == 100.8  # opens above its stop: at the opening
    assert stop_fill(SHORT, 100.0, 99.5) == 100.0
    assert gross_r(SHORT, 100.0, 98.0, 1.0) == 2.0


def test_the_costs_are_fractions() -> None:
    with pytest.raises(ValueError, match="fractions"):
        Costs(Decimal("1"), Decimal("0"))
    with pytest.raises(ValueError, match="fractions"):
        Costs(Decimal("0"), Decimal("-0.0001"))
