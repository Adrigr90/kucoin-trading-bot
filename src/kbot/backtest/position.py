"""One position, from its decision to its exit (``02`` §12.4.2, §12.4.4 and §12.4.5).

* **Entry** at the opening of the execution minute, the first that starts after the
  decision. If it is absent, the signal expires (CD-03); if the checks of §12.3.1 fail, there
  is no entry. Either way the instrument is free when that minute resolves.
* **After the entry**, minute by minute over the minutes present:
  1. the checks of the closed candles whose instant is before the minute, in order of
     instant: a trailing proposal moves the stop only closer, and an invalidation that says
     exit leaves a market exit pending;
  2. a pending market exit, or the time limit, fills at the opening;
  3. otherwise the stop and the target of the minute, the stop first.
* **No fill without an observed price** (§7): absent minutes are skipped.
* **Window end** (F3E4-02): nothing at its end or after. A position still open is liquidated
  at the close of the last minute present of the window, labelled.
* **Knowledge instants**, the latest of the data each event uses (§12.4.5):
  - the entry, when its minute is available;
  - an exit, when every minute from the entry to its own is resolved, because it also says
    that none before fired: with the availabilities in order, its own minute's;
  - a change of the stop, its candle's, and not before the minutes up to the one that
    holds its instant are resolved, because it needs the position still open;
  - a liquidation, when every minute of the window from the entry on is resolved.

  The liberation is the knowledge instant of the exit.
* **Two provenances** (F3E4-03): the decision's, and the result's. The result's adds the
  minutes from the entry to the exit, all looked at, and what every check before the exit
  used. Nothing after the exit counts, and every minute counts once. The trade keeps both
  intervals and both counts, total and only REST (F3E4-05).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from kbot.backtest.execution import Costs, gross_r, net_r, stop_fill
from kbot.market.last_candles import STABILIZATION
from kbot.market.timeutil import MINUTE, floor_to
from kbot.strategies.ficha import Ficha, Frames
from kbot.strategies.frames import ONE_MINUTE, Frame, merged, rest_only, step_of
from kbot.strategies.rules import (
    Check,
    Decision,
    Interval,
    LevelExit,
    Trail,
    closer,
    execution_minute,
    level_exit,
    may_enter,
    risk,
)


class ExitReason(StrEnum):
    STOP = "stop"
    TARGET = "objetivo"
    INVALIDATION = "invalidacion"
    TIME = "tiempo"
    LIQUIDATION = "liquidacion_fin_de_ventana"


class EntryFailure(StrEnum):
    NO_MINUTE = "caduca_sin_minuto"  # CD-03
    CHECKS = "no_entra"  # §12.3.1, «Ejecución»


@dataclass(frozen=True)
class StopChange:
    at: datetime  # it holds for the minutes that start after this instant
    level: float
    # It also needs the position still open then: every minute from the entry to the one
    # that holds ``at`` resolved. With the availabilities in order, that minute's.
    known_at: datetime


@dataclass(frozen=True)
class Trade:
    family: str
    market: str
    symbol: str
    timeframe: str
    direction: int
    signal_open: datetime
    signal_end: datetime  # instant 1
    signal_at: datetime  # instant 2, D_s
    decision_at: datetime  # instant 3, D
    entry_minute: datetime  # instant 4, the execution
    entry_known_at: datetime
    entry_price: float
    entry_fill: float
    initial_stop: float
    target: float | None
    r: float
    stop_changes: tuple[StopChange, ...]
    exit_reason: ExitReason
    exit_minute: datetime
    exit_price: float
    exit_fill: float
    exit_known_at: datetime
    gross_r: float
    net_r: float
    decision_provenance: tuple[Interval, ...]
    result_provenance: tuple[Interval, ...]
    # The two shares only REST (F3E4-05): (original minutes, of them only REST), of the
    # decision and of the result, counted on the minutes of the run.
    decision_rest_only: tuple[int, int]
    result_rest_only: tuple[int, int]
    ideal_open: float | None  # «idealizada»: the opening of t+1; never used for anything


@dataclass(frozen=True)
class Execution:
    """What an executed decision gave: a trade, or an entry that did not happen, and the
    instant the instrument is free again."""

    decision: Decision
    trade: Trade | None
    failure: EntryFailure | None
    liberation: datetime


def resolution(minutes: Frame, minute: datetime) -> datetime:
    """When a minute resolves: when it is available, or, absent, when that is known (the
    rule of the history: at its end plus the margin)."""
    point = minutes.points.get(minute)
    if point is not None and point.available_at is not None:
        return point.available_at
    return minute + MINUTE + STABILIZATION


def resolved(minutes: Frame, start: datetime, end: datetime) -> datetime:
    """When every minute of [start, end) has resolved: the latest of their resolutions."""
    present = minutes.span(start, end)
    latest = max(resolution(minutes, m) for m in present) if present else start
    if len(present) < (end - start) // MINUTE:
        absent = end - MINUTE
        while absent in minutes.points:
            absent -= MINUTE
        latest = max(latest, absent + MINUTE + STABILIZATION)
    return latest


def simulate(
    ficha: Ficha, frames: Frames, decision: Decision, window_end: datetime, costs: Costs
) -> Execution:
    signal, d = decision.signal, decision.signal.direction
    minutes = frames[ONE_MINUTE]
    entry_minute = execution_minute(decision.at)
    if entry_minute >= window_end or decision.stop is None:
        raise ValueError("only a decision whose execution falls inside the window is simulated")
    point = minutes.points.get(entry_minute)
    if point is None:
        return Execution(decision, None, EntryFailure.NO_MINUTE, resolution(minutes, entry_minute))
    entry, stop = point.open, decision.stop
    target = ficha.entry_target(signal, entry, stop)
    if not may_enter(d, entry, stop, target):
        return Execution(decision, None, EntryFailure.CHECKS, resolution(minutes, entry_minute))
    r = risk(d, entry, stop)
    checks = _checks(ficha, frames, decision, entry_minute, window_end)
    limit = ficha.time_limit(entry_minute)
    opens = minutes.span(entry_minute, window_end)
    changes: list[StopChange] = []
    used: list[Interval] = []
    pending: Check | None = None
    exit_: tuple[ExitReason, datetime, float] | None = None
    index = 0
    for minute in opens:
        while index < len(checks) and checks[index].at < minute:
            item = checks[index]
            index += 1
            if pending is not None:
                continue  # the exit is already decided: nothing else is looked at
            used.extend(item.provenance)
            if isinstance(item, Trail):
                moved = closer(d, stop, item.level)
                if moved != stop:
                    stop = moved
                    still_open = resolved(minutes, entry_minute, floor_to(item.at, MINUTE) + MINUTE)
                    changes.append(StopChange(item.at, moved, max(item.at, still_open)))
            elif item.exit:
                pending = item
        candle = minutes.points[minute]
        if pending is not None:
            exit_ = (ExitReason.INVALIDATION, minute, candle.open)
        elif limit is not None and minute >= limit:
            exit_ = (ExitReason.TIME, minute, candle.open)
        else:
            touched = level_exit(d, candle.low, candle.high, stop, target)
            if touched is LevelExit.STOP:
                exit_ = (ExitReason.STOP, minute, stop_fill(d, stop, candle.open))
            elif touched is LevelExit.TARGET and target is not None:
                exit_ = (ExitReason.TARGET, minute, target)
        if exit_ is not None:
            break
    if exit_ is None:  # the window ends with the position open
        last = opens[-1]
        exit_ = (ExitReason.LIQUIDATION, last, minutes.points[last].close)
        known = resolved(minutes, entry_minute, window_end)
    else:
        known = resolved(minutes, entry_minute, exit_[1] + MINUTE)
    reason, exit_minute, exit_price = exit_
    entry_fill, exit_fill = costs.entry(d, entry), costs.exit(d, exit_price)
    signal_step = step_of(signal.timeframe)
    ideal = minutes.points.get(signal.open_time + signal_step)
    result_provenance = merged((*decision.provenance, (entry_minute, exit_minute + MINUTE), *used))
    trade = Trade(
        family=signal.family,
        market=signal.market,
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        direction=d,
        signal_open=signal.open_time,
        signal_end=signal.open_time + signal_step,
        signal_at=signal.at,
        decision_at=decision.at,
        entry_minute=entry_minute,
        entry_known_at=resolution(minutes, entry_minute),
        entry_price=entry,
        entry_fill=entry_fill,
        initial_stop=decision.stop,
        target=target,
        r=r,
        stop_changes=tuple(changes),
        exit_reason=reason,
        exit_minute=exit_minute,
        exit_price=exit_price,
        exit_fill=exit_fill,
        exit_known_at=known,
        gross_r=gross_r(d, entry, exit_price, r),
        net_r=net_r(d, entry_fill, exit_fill, r, costs.fee),
        decision_provenance=decision.provenance,
        result_provenance=result_provenance,
        decision_rest_only=rest_only(decision.provenance, minutes),
        result_rest_only=rest_only(result_provenance, minutes),
        ideal_open=ideal.open if ideal is not None else None,
    )
    return Execution(decision, trade, None, known)


def _checks(
    ficha: Ficha, frames: Frames, decision: Decision, entry_minute: datetime, end: datetime
) -> list[Check | Trail]:
    """The checks of the closed candles that end after the entry, with an instant before the
    end of the window, in order of instant: invalidations and trailing proposals."""
    signal = decision.signal
    found: list[Check | Trail] = []
    for timeframe, read in (
        (ficha.invalidation_timeframe, ficha.invalidation),
        (signal.timeframe, ficha.trailing),
    ):
        frame = frames[timeframe]
        for opened in frame.span(entry_minute - frame.step + MINUTE, end):
            item = read(frames, signal, opened)
            if item is not None and item.at < end:
                found.append(item)
    # In an instant, the trailing goes before the invalidation; their effect is the same.
    return sorted(found, key=lambda item: (item.at, isinstance(item, Check)))
