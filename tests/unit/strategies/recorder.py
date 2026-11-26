"""What the recorder writes, in memory, for the tests of ``svc-strategy`` without a
database: the rows of candles, instruments and universe with their ``recorded_at``, a view
of what is visible in a cut, and a writing that keeps the first row of each key, as
``ON CONFLICT DO NOTHING`` does."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from kbot.backtest.engine import Window, run
from kbot.backtest.execution import Costs
from kbot.history.timeframes import MinuteCandle
from kbot.strategies.ficha import Ficha
from kbot.strategies.recorded import (
    CandleRow,
    Evaluation,
    Observation,
    Universe,
    minutes_from_rows,
)
from kbot.strategies.service import WARMUP, View, day_start, signal_row

MINUTE = timedelta(minutes=1)
COMMIT = "e" * 40
KEY = (
    "family",
    "code_commit",
    "params_hash",
    "market",
    "symbol",
    "timeframe",
    "open_time",
    "direction",
)


def rows_of(
    minutes: Iterable[MinuteCandle],
    *,
    state: str = "cerrada_rest",
    lag: timedelta = timedelta(seconds=15),
) -> list[CandleRow]:
    """The rows the recorder would write for these minutes, ``lag`` after their end."""
    return [
        CandleRow(
            m.candle.open_time,
            m.candle.open,
            m.candle.high,
            m.candle.low,
            m.candle.close,
            m.candle.volume,
            m.candle.turnover,
            state,
            m.candle.open_time + MINUTE + lag,
        )
        for m in minutes
    ]


@dataclass
class Recorder:
    candles: dict[tuple[str, str], list[CandleRow]] = field(default_factory=dict)
    observations: dict[tuple[str, str], list[tuple[Observation, datetime]]] = field(
        default_factory=dict
    )
    evaluations: list[tuple[Evaluation, datetime]] = field(default_factory=list)
    approved: set[tuple[str, str]] = field(default_factory=set)
    written: dict[tuple[Any, ...], dict[str, Any]] = field(default_factory=dict)
    failing: bool = False

    def add(self, market: str, symbol: str, rows: Iterable[CandleRow]) -> None:
        self.candles.setdefault((market, symbol), []).extend(rows)

    def observe(self, market: str, symbol: str, at: datetime, multiplier: str) -> None:
        self.observations.setdefault((market, symbol), []).append(
            (Observation(at, Decimal(multiplier)), at)
        )

    def evaluate(
        self,
        at: datetime,
        members: Iterable[tuple[str, str]],
        *,
        out: Iterable[tuple[str, str]] = (),
    ) -> None:
        for market, symbol in members:
            self.evaluations.append((Evaluation(at, market, symbol, True), at))
        for market, symbol in out:
            self.evaluations.append((Evaluation(at, market, symbol, False), at))

    @contextmanager
    def open_view(self, corte: datetime) -> Iterator[View]:
        yield MemoryView(self, corte)

    def write(self, rows: list[dict[str, Any]]) -> None:
        if self.failing:
            raise RuntimeError("the database is down")
        for row in rows:
            self.written.setdefault(tuple(row[k] for k in KEY), row)

    def visible_minutes(
        self, market: str, symbol: str, start: datetime, end: datetime, corte: datetime
    ) -> list[MinuteCandle]:
        view = MemoryView(self, corte)
        rows = view.candles(market, symbol, start, end)
        return minutes_from_rows(market, symbol, rows, view.observations(market, symbol))


class MemoryView:
    def __init__(self, recorder: Recorder, corte: datetime) -> None:
        self.recorder = recorder
        self.corte = corte

    def approved(self) -> frozenset[tuple[str, str]]:
        return frozenset(self.recorder.approved)

    def evaluations(self, until: datetime) -> list[Evaluation]:
        return [
            e
            for e, recorded in self.recorder.evaluations
            if recorded <= self.corte and e.evaluated_at < until
        ]

    def universe_rows(self) -> int:
        return sum(1 for _e, recorded in self.recorder.evaluations if recorded <= self.corte)

    def observations(self, market: str, symbol: str) -> list[Observation]:
        held = self.recorder.observations.get((market, symbol), [])
        return sorted(
            (o for o, recorded in held if recorded <= self.corte), key=lambda o: o.observed_at
        )

    def candles(self, market: str, symbol: str, start: datetime, end: datetime) -> list[CandleRow]:
        return sorted(
            (
                r
                for r in self.recorder.candles.get((market, symbol), [])
                if start <= r.open_time < end and r.recorded_at <= self.corte
            ),
            key=lambda r: r.open_time,
        )

    def candle_count(self, market: str, symbol: str, start: datetime, end: datetime) -> int:
        return len(self.candles(market, symbol, start, end))


def engine_rows(
    recorder: Recorder,
    ficha: Ficha,
    market: str,
    symbol: str,
    day: datetime,
    corte: datetime,
) -> set[tuple[Any, ...]]:
    """The signals of the engine for that day, with the minutes visible in ``corte`` and
    ``D_s`` up to it, as rows without their ids (02 §12.6.4, the equivalence)."""
    end = min(day + timedelta(days=1), corte.replace(second=0, microsecond=0))
    minutes = recorder.visible_minutes(market, symbol, day - WARMUP, end, corte)
    if not minutes:
        return set()
    done = run(
        [ficha], minutes, Window(day, day + timedelta(days=1)), Costs(Decimal(0), Decimal(0))
    )
    frames = ficha.frames(minutes, downloaded_until=end)
    universe = Universe(e for e, recorded in recorder.evaluations if recorded <= corte)
    return {
        comparable(signal_row(s, frames, ficha.params_hash(), COMMIT, run_id_zero()))
        for s in done.signals
        if s.at <= corte and (market, symbol) in universe.at(s.at)
    }


def comparable(row: dict[str, Any]) -> tuple[Any, ...]:
    """A row without its ids, hashable."""
    return tuple(
        (k, _frozen(v)) for k, v in sorted(row.items()) if k not in ("signal_id", "run_id")
    )


def _frozen(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((k, _frozen(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_frozen(v) for v in value)
    return value


def run_id_zero() -> UUID:

    return UUID(int=0)


def written_rows(recorder: Recorder, *, day: datetime | None = None) -> set[tuple[Any, ...]]:
    rows = recorder.written.values()
    if day is not None:
        rows = [r for r in rows if day <= r["signal_at"] < day + timedelta(days=1)]
    return {comparable(r) for r in rows}


def days(start: datetime, count: int) -> Sequence[datetime]:
    return [day_start(start.date()) + n * timedelta(days=1) for n in range(count)]
