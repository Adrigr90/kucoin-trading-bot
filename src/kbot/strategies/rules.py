"""What the three fichas share (``02`` §12.3.1), as pure functions on plain values.

* **Direction:** +1 is long and -1 short. Spot only goes long (§6.2.1); Futures both ways.
* **Context candle at D:** the one of the context timeframe whose interval is the last
  ended at D or before. It must be usable and available at D; an older one is never used.
* **Trigger:** the K execution candles that start when the signal candle ends are walked in
  order, and their instants never go back (F3E3-01): D_0 = D_s and D_i = max(D_s, D_{i-1},
  resolution of e_i). A candle resolves when it is available or when it is known that it
  will not be usable. At each D_i, an unusable candle makes the signal expire, so does a
  context that no longer holds, and a candle that fires is the decision.
* **Initial stop:** the close of the trigger candle minus ``d`` times the ficha's multiple
  of the ATR(14) of the signal candle.
* **Execution:** at the opening of the first minute that starts after the decision. There is
  no entry if the price is already past the stop, if the stop is not positive, or, with a
  target, if it is not positive or the price is already past it (F3E3-02). Levels are never
  clipped.
* **Exits by level:** on minute candles, from the entry one; a stop and a target touched in
  the same minute count as the stop. **Trailing:** the stop only comes closer to the price.
* **Thresholds** on indicator values are compared exactly (``Decimal`` of a float is its
  exact value); levels are computed in ``float``, like the indicators (§12.2, point 1).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from kbot.market.timeutil import MINUTE, floor_to
from kbot.strategies.regime import Direction, Volatility

LONG = 1
SHORT = -1

Interval = tuple[datetime, datetime]  # [start, end) of original minutes


def directions(market: str) -> tuple[int, ...]:
    if market == "SPOT":
        return (LONG,)
    if market == "FUTURES":
        return (LONG, SHORT)
    raise ValueError(f"unknown market {market!r}")


def at_least(value: float, threshold: Decimal) -> bool:
    return Decimal(value) >= threshold


def at_most(value: float, threshold: Decimal) -> bool:
    return Decimal(value) <= threshold


def context_open(instant: datetime, step: timedelta) -> datetime:
    """Opening of the last interval of ``step`` that ended at ``instant`` or before."""
    return floor_to(instant - step, step)


def beyond_extreme(d: int, close: float, high: float, low: float) -> bool:
    """The trigger of the trend and the breakout: a close beyond the extreme of the signal
    candle on the side of ``d``."""
    return close > high if d == LONG else close < low


class Outcome(StrEnum):
    DECISION = "decision"
    UNUSABLE = "caduca_vela_no_utilizable"
    CONTEXT = "caduca_contexto"
    NO_TRIGGER = "caduca_sin_disparo"


@dataclass(frozen=True)
class TriggerCandle:
    """An execution candle as the walk sees it: when it resolves, and its close if it is
    usable (None if it is not)."""

    open_time: datetime
    resolved_at: datetime
    close: float | None


@dataclass(frozen=True)
class Walk:
    outcome: Outcome
    at: datetime  # the instant of the decision, or of the expiry
    candle: TriggerCandle | None  # the candle that fired
    # Every candle whose close was looked at, in order, with its instant D_i, at which the
    # context held: the inputs of the outcome, the trigger candle included (F3E3-03).
    examined: tuple[tuple[TriggerCandle, datetime], ...] = ()


def walk(
    signal_at: datetime,
    candles: Sequence[TriggerCandle],
    fires: Callable[[float], bool],
    context_holds: Callable[[datetime], bool],
) -> Walk:
    """§12.3.1, «Disparo». ``at`` is D_{i-1} when the loop takes e_i, with D_0 = D_s, so
    ``max(at, resolution)`` is max(D_s, D_{i-1}, resolution of e_i): it never goes back."""
    at = signal_at
    examined: list[tuple[TriggerCandle, datetime]] = []
    for candle in candles:
        at = max(at, candle.resolved_at)
        if candle.close is None:
            return Walk(Outcome.UNUSABLE, at, None, tuple(examined))
        if not context_holds(at):
            return Walk(Outcome.CONTEXT, at, None, tuple(examined))
        examined.append((candle, at))
        if fires(candle.close):
            return Walk(Outcome.DECISION, at, candle, tuple(examined))
    return Walk(Outcome.NO_TRIGGER, at, None, tuple(examined))


def initial_stop(d: int, trigger_close: float, atr: float, multiple: Decimal) -> float:
    return trigger_close - d * float(multiple) * atr


def execution_minute(decision_at: datetime) -> datetime:
    """The opening of the first minute that starts after the decision (F3-E4; CD-17)."""
    return floor_to(decision_at, MINUTE) + MINUTE


def may_enter(d: int, entry: float, stop: float, target: float | None = None) -> bool:
    if stop <= 0.0 or d * (entry - stop) <= 0.0:
        return False
    return target is None or (target > 0.0 and d * (target - entry) > 0.0)


def risk(d: int, entry: float, stop: float) -> float:
    """The risk per unit, r: what 1 R is."""
    return d * (entry - stop)


class LevelExit(StrEnum):
    STOP = "stop"
    TARGET = "objetivo"


def level_exit(
    d: int, low: float, high: float, stop: float, target: float | None = None
) -> LevelExit | None:
    """Which level a minute candle touches, the stop first."""
    if (low <= stop) if d == LONG else (high >= stop):
        return LevelExit.STOP
    if target is not None and ((high >= target) if d == LONG else (low <= target)):
        return LevelExit.TARGET
    return None


def trail(d: int, stop: float, close: float, atr: float, multiple: Decimal) -> float:
    """The trailing stop after a close: it only comes closer to the price."""
    candidate = close - d * float(multiple) * atr
    return max(stop, candidate) if d == LONG else min(stop, candidate)


Labels = tuple[Direction | None, Volatility | None]


@dataclass(frozen=True)
class Signal:
    """A signal at the candle ``open_time`` of the signal timeframe (§1.3: its key is family,
    version, instrument, timeframe, candle and direction), in the instant ``at``, D_s."""

    family: str
    market: str
    symbol: str
    timeframe: str
    open_time: datetime
    direction: int
    at: datetime
    context_open: datetime  # the context candle at D_s
    levels: tuple[tuple[str, float], ...]  # fixed at the signal
    context_regime: Labels
    signal_regime: Labels
    provenance: tuple[Interval, ...]  # the original minutes of its inputs, merged

    def level(self, name: str) -> float:
        return dict(self.levels)[name]


@dataclass(frozen=True)
class Decision:
    """What the walk of the trigger gave for a signal: a decision, with its initial stop,
    or an expiry."""

    signal: Signal
    outcome: Outcome
    at: datetime
    trigger_open: datetime | None
    trigger_close: float | None
    stop: float | None
    # The signal's, every execution candle whose close was looked at and the context at
    # each of their instants (F3E3-03), merged: each original minute once.
    provenance: tuple[Interval, ...]

    @property
    def decided(self) -> bool:
        return self.outcome is Outcome.DECISION


@dataclass(frozen=True)
class Check:
    """A check on a closed candle after the entry: in the instant ``at``, whether to exit."""

    at: datetime
    exit: bool
