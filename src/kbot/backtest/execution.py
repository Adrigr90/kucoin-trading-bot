"""The execution model of the F3 (``02`` §12.4.4), simple and declared.

* **Entry**, at market at the opening of the execution minute.
* **Stop**, a stop at market: at its level, or at the opening of the minute if it already
  opens past it (a gap, §7).
* **Target**, a limit order: at its level, never better.
* **Exits by close and by time, and the liquidation at the end of a window**, at market.
* **Costs:** a fee ``f`` per side on the notional, and a slippage ``s`` on every fill: a
  purchase at P(1 + s) and a sale at P(1 - s). Their values are set by F3-E5; there is no
  funding in the F3.
* **Result** of a trade, per unit and in multiples of r: gross, d (P_x - P_e) / r with the
  prices without costs; net, [d (P'_x - P'_e) - f (P'_e + P'_x)] / r with the slipped ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kbot.strategies.rules import LONG


@dataclass(frozen=True)
class Costs:
    fee: Decimal  # per side, on the notional
    slippage: Decimal  # on every fill

    def __post_init__(self) -> None:
        if not (Decimal(0) <= self.fee < 1 and Decimal(0) <= self.slippage < 1):
            raise ValueError("fee and slippage are fractions between 0 and 1")

    def entry(self, d: int, price: float) -> float:
        """The price of the entry with slippage: a long buys, a short sells."""
        return price * (1.0 + d * float(self.slippage))

    def exit(self, d: int, price: float) -> float:
        """The price of the exit with slippage: a long sells, a short buys."""
        return price * (1.0 - d * float(self.slippage))


def stop_fill(d: int, stop: float, opening: float) -> float:
    """A stop at market fills at its level, or at the opening if the minute opens past it."""
    return min(stop, opening) if d == LONG else max(stop, opening)


def gross_r(d: int, entry: float, exit_: float, r: float) -> float:
    return d * (exit_ - entry) / r


def net_r(d: int, entry: float, exit_: float, r: float, fee: Decimal) -> float:
    """With the prices already slipped."""
    return (d * (exit_ - entry) - float(fee) * (entry + exit_)) / r
