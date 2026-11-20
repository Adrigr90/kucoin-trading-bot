"""A run of the backtest over one instrument and one window (``02`` §12.4.5).

* **Data:** only the minutes before the end of the window (F3E4-02); before its start, they
  only warm the indicators up. Each ficha builds its frames from them (§12.3).
* **Signals and decisions:** those of every ficha, each signal unique by its key (§1.3: the
  run stops on a repeated key). The engine acts on the decisions inside the window.
* **The combiner** takes, instant by instant, the decisions of that instant together, after
  the liberation of the position before (F3E4-01). In the mode by family, a single ficha
  runs, with the same rules.
* **The record:** the versions, the instrument, the window, the costs and the mode; the
  signals; every decision with what became of it; and the trades. Its order depends only on
  the data, never on the order in which the fichas or the events come.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from kbot.backtest.execution import Costs
from kbot.backtest.position import Execution, Trade, simulate
from kbot.history.timeframes import MinuteCandle
from kbot.strategies.combiner import FAMILY_ORDER, allocate, rank
from kbot.strategies.ficha import Ficha
from kbot.strategies.rules import Decision, Signal

EXECUTED = "ejecutada"
BY_FAMILY = "por_familia"
COMBINED = "combinado"


class RepeatedSignal(RuntimeError):
    """Two signals with the same key (§1.3): the run stops."""


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not self.start < self.end:
            raise ValueError("a window needs its start before its end")

    def holds(self, instant: datetime) -> bool:
        return self.start <= instant < self.end


@dataclass(frozen=True)
class DecisionRecord:
    """A decision or an expiry of the walk, with what became of it: ``ejecutada``, the
    expiry of the walk, the refusal of the combiner, or the entry that did not happen."""

    decision: Decision
    status: str


@dataclass(frozen=True)
class Run:
    market: str
    symbol: str
    window: Window
    costs: Costs
    mode: str
    versions: tuple[tuple[str, str | None], ...]  # None: «sin versión»
    signals: tuple[Signal, ...]
    decisions: tuple[DecisionRecord, ...]
    trades: tuple[Trade, ...]


def run(
    fichas: Sequence[Ficha],
    minutes: Iterable[MinuteCandle],
    window: Window,
    costs: Costs,
    versions: Mapping[str, str] | None = None,
) -> Run:
    if not fichas:
        raise ValueError("a run needs at least one ficha")
    fichas = sorted(fichas, key=lambda f: FAMILY_ORDER.index(f.family))
    if len({f.family for f in fichas}) != len(fichas):
        raise ValueError("one ficha per family")
    versions = versions or {}
    kept = [m for m in minutes if m.candle.open_time < window.end]
    if len({(m.market, m.symbol) for m in kept}) != 1:
        raise ValueError("a run is over the minutes of exactly one instrument")
    frames = {f.family: f.frames(kept, downloaded_until=window.end) for f in fichas}
    by_family = {f.family: f for f in fichas}
    signals: list[Signal] = []
    decisions: list[Decision] = []
    keys: set[tuple[object, ...]] = set()
    for ficha in fichas:
        for signal in ficha.signals(frames[ficha.family]):
            key = (
                signal.family,
                versions.get(signal.family),
                signal.market,
                signal.symbol,
                signal.timeframe,
                signal.open_time,
                signal.direction,
            )
            if key in keys:
                raise RepeatedSignal(f"two signals with the key {key}")
            keys.add(key)
            signals.append(signal)
            decisions.append(ficha.decide(frames[ficha.family], signal))
    inside = [d for d in decisions if window.holds(d.at)]
    records = [DecisionRecord(d, d.outcome.value) for d in inside if not d.decided]
    executions: dict[Decision, Execution] = {}

    def execute(decision: Decision) -> datetime:
        family = decision.signal.family
        executions[decision] = simulate(
            by_family[family], frames[family], decision, window.end, costs
        )
        return executions[decision].liberation

    allocated = allocate([d for d in inside if d.decided], execute, window_end=window.end)
    trades: list[Trade] = []
    for decision, refusal in allocated:
        if refusal is not None:
            records.append(DecisionRecord(decision, refusal.value))
            continue
        execution = executions[decision]
        status = EXECUTED if execution.failure is None else execution.failure.value
        records.append(DecisionRecord(decision, status))
        if execution.trade is not None:
            trades.append(execution.trade)
    market, symbol = kept[0].market, kept[0].symbol
    return Run(
        market=market,
        symbol=symbol,
        window=window,
        costs=costs,
        mode=BY_FAMILY if len(fichas) == 1 else COMBINED,
        versions=tuple((f.family, versions.get(f.family)) for f in fichas),
        signals=tuple(sorted((s for s in signals if window.holds(s.at)), key=_signal_order)),
        decisions=tuple(sorted(records, key=lambda r: (r.decision.at, rank(r.decision)))),
        trades=tuple(trades),
    )


def _signal_order(signal: Signal) -> tuple[datetime, int, datetime, int]:
    return (signal.at, FAMILY_ORDER.index(signal.family), signal.open_time, -signal.direction)
