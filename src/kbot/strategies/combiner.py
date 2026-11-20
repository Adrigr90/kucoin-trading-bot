"""The combiner (``02`` §12.4.3; F3-A8): one net position per instrument.

All the decisions of one instrument at one instant are combined together, after the
resolutions and liberations of that instant (F3E4-01):

* **occupied:** while a position occupies the instrument, in [decision, liberation), none
  is executed, of the same direction or the other: no adding, no turning around;
* **opposite:** decisions of both directions at the same instant: none is executed;
* **window end:** an execution minute at the end of the window or after it: none (F3E4-02);
* **same direction:** one, of the family that goes first in the fixed order trend, breakout
  and mean reversion, and within a family the one of the oldest signal candle. The order of
  families only breaks this tie.

The result does not depend on the order in which the decisions of an instant arrive.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from kbot.strategies.rules import Decision, execution_minute

FAMILY_ORDER = ("tendencia", "breakout", "reversion_media")


class Refusal(StrEnum):
    OCCUPIED = "ocupado"
    OPPOSITE = "opuestas"
    WINDOW_END = "fin_de_ventana"
    TAKEN = "posicion_tomada"


@dataclass(frozen=True)
class Verdict:
    chosen: Decision | None
    refused: tuple[tuple[Decision, Refusal], ...]


def rank(decision: Decision) -> tuple[int, datetime, int]:
    signal = decision.signal
    return (FAMILY_ORDER.index(signal.family), signal.open_time, -signal.direction)


def combine(
    decisions: Sequence[Decision], *, occupied: bool, window_end: datetime | None = None
) -> Verdict:
    """The decisions of one instrument at one instant, together."""
    if not decisions:
        return Verdict(None, ())
    if any(not d.decided for d in decisions):
        raise ValueError("the combiner only takes decisions, not expiries")
    if len({(d.signal.market, d.signal.symbol, d.at) for d in decisions}) != 1:
        raise ValueError("the combiner takes the decisions of one instrument at one instant")
    ordered = sorted(decisions, key=rank)
    refusal = None
    if occupied:
        refusal = Refusal.OCCUPIED
    elif len({d.signal.direction for d in ordered}) > 1:
        refusal = Refusal.OPPOSITE
    elif window_end is not None and execution_minute(ordered[0].at) >= window_end:
        refusal = Refusal.WINDOW_END
    if refusal is not None:
        return Verdict(None, tuple((d, refusal) for d in ordered))
    first, *rest = ordered
    return Verdict(first, tuple((d, Refusal.TAKEN) for d in rest))


def allocate(
    decisions: Iterable[Decision],
    execute: Callable[[Decision], datetime],
    *,
    window_end: datetime | None = None,
) -> list[tuple[Decision, Refusal | None]]:
    """The decisions of one instrument, instant by instant (F3E4-01). In each instant, the
    liberation of the position before applies first: the instrument is occupied in
    [decision, liberation). Then the decisions of the instant are combined together.
    ``execute`` executes a chosen decision and says when the instrument is free again.
    Each decision comes back with its refusal, or None if it was chosen."""
    by_instant: dict[datetime, list[Decision]] = defaultdict(list)
    for decision in decisions:
        by_instant[decision.at].append(decision)
    result: list[tuple[Decision, Refusal | None]] = []
    free_at: datetime | None = None
    for instant in sorted(by_instant):
        occupied = free_at is not None and instant < free_at
        verdict = combine(by_instant[instant], occupied=occupied, window_end=window_end)
        result.extend(verdict.refused)
        if verdict.chosen is not None:
            free_at = execute(verdict.chosen)
            result.append((verdict.chosen, None))
    return result
