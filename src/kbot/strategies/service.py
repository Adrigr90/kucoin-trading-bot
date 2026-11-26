"""``svc-strategy``: the signals of the approved versions, from what the recorder writes
(``02`` §12.6.4; F3-E5b).

* **Versions and families:** the commit of the code that runs and the hash of each ficha.
  Only the fichas whose (family, hash) is in ``config.strategy_versions`` run.
* **The signals of a day** are those of the fichas with ``D_s`` in [d, d+1), computed with
  the minutes visible from d - 30 days, the same functions as the engine (§12.4): with the
  same visible minutes, a cycle gives exactly the signals of ``engine.run`` with ``D_s`` up
  to its cut (F3E5B-02). Each signal stays only if its instrument counted at its ``D_s``
  (F3E5B-01).
* **Cycles:** every 15 minutes, at minutes 01, 16, 31 and 46, second 30. Each fixes its
  ``corte``, 60 s before, and looks at the days that touch [corte - 48 h, corte]. A day is
  computed again, by family and instrument, when this process has no good computation of it
  with a cut after its end, or when what it uses changed: the number of its visible rows of
  candles, observations of the instrument and evaluations of the universe. Rows are only
  inserted, so another number is a new row, also one confirmed late (F3E5B-03).
* **Writing:** one transaction per cycle, ``ON CONFLICT DO NOTHING``. A signal written is
  never removed or changed. A cycle that fails writes nothing and changes no state: the next
  one retries every pending day of the window, without a restart.
* **Without network:** the process only opens the Unix socket of PostgreSQL, as
  ``kbot_strategy``, and never imports the KuCoin client (§1.1).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg import IsolationLevel
from psycopg.types.json import Jsonb

from kbot.market.timeutil import MINUTE, as_utc, floor_to
from kbot.strategies.ficha import Ficha, Frames
from kbot.strategies.frames import ONE_MINUTE, rest_only
from kbot.strategies.recorded import (
    CandleRow,
    Evaluation,
    Observation,
    Universe,
    minutes_from_rows,
)
from kbot.strategies.rules import Signal

DAY = timedelta(days=1)
CUT_MARGIN = timedelta(seconds=60)  # for the inserts of the recorder to be confirmed [R]
RECOVERY = timedelta(hours=48)  # while the recorder still recovers REST candles (§3.8)
WARMUP = timedelta(days=30)  # the declared warm-up of the folds (§12.5.2)
QUARTER = timedelta(minutes=15)
CYCLE_AT = timedelta(minutes=1, seconds=30)  # minutes 01, 16, 31 and 46, second 30

Key = tuple[str, str, str, date]  # family, market, symbol, day


class View(Protocol):
    """What is visible in a cut: the rows with ``recorded_at`` at or before ``corte``."""

    corte: datetime

    def approved(self) -> frozenset[tuple[str, str]]: ...

    def evaluations(self, until: datetime) -> list[Evaluation]: ...

    def universe_rows(self) -> int: ...

    def observations(self, market: str, symbol: str) -> list[Observation]: ...

    def candles(
        self, market: str, symbol: str, start: datetime, end: datetime
    ) -> list[CandleRow]: ...

    def candle_count(self, market: str, symbol: str, start: datetime, end: datetime) -> int: ...


def cut_of(now: datetime) -> datetime:
    return as_utc(now, "now") - CUT_MARGIN


def next_cycle(now: datetime) -> datetime:
    """The first cycle instant after ``now``."""
    at = floor_to(now, QUARTER) + CYCLE_AT
    return at if at > as_utc(now, "now") else at + QUARTER


def day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def recovery_days(corte: datetime) -> list[date]:
    """The days that touch [corte - 48 h, corte], in order."""
    first, last = (as_utc(corte, "corte") - RECOVERY).date(), as_utc(corte, "corte").date()
    return [first + n * DAY for n in range((last - first).days + 1)]


def signal_row(
    signal: Signal, frames: Frames, params_hash: str, commit: str, run_id: UUID
) -> dict[str, Any]:
    """The row of ``trading.signals`` of a signal (02 §12.6.1), without its id."""
    total, rest = rest_only(signal.provenance, frames[ONE_MINUTE])
    (context_direction, context_volatility) = signal.context_regime
    (signal_direction, signal_volatility) = signal.signal_regime
    return {
        "family": signal.family,
        "code_commit": commit,
        "params_hash": params_hash,
        "market": signal.market,
        "symbol": signal.symbol,
        "timeframe": signal.timeframe,
        "open_time": signal.open_time,
        "direction": signal.direction,
        "signal_at": signal.at,
        "context_open": signal.context_open,
        "context_direction": context_direction.value if context_direction else None,
        "context_volatility": context_volatility.value if context_volatility else None,
        "signal_direction": signal_direction.value if signal_direction else None,
        "signal_volatility": signal_volatility.value if signal_volatility else None,
        "levels": dict(signal.levels),
        "provenance": [[a.isoformat(), b.isoformat()] for a, b in signal.provenance],
        "provenance_minutes": total,
        "rest_only_minutes": rest,
        "run_id": run_id,
    }


@dataclass(frozen=True)
class Done:
    """A good computation of a day: its cut and the number of rows it used."""

    corte: datetime
    counts: tuple[int, int, int]


@dataclass(frozen=True)
class Cycle:
    corte: datetime
    computed: int  # (family, instrument, day) computed
    signals: int  # rows offered to the insert; those already written are not repeated


class Service:
    def __init__(self, fichas: Sequence[Ficha], commit: str, run_id: UUID | None = None) -> None:
        if len({f.family for f in fichas}) != len(fichas):
            raise ValueError("one ficha per family")
        self.fichas = list(fichas)
        self.commit = commit
        self.run_id = run_id or uuid4()
        self.done: dict[Key, Done] = {}

    def compute(self, view: View) -> tuple[list[dict[str, Any]], dict[Key, Done]]:
        """The rows to write and the new states, with nothing written yet."""
        corte = as_utc(view.corte, "corte")
        approved = view.approved()
        active = [f for f in self.fichas if (f.family, f.params_hash()) in approved]
        if not active:
            return [], {}  # without an approved version there are no signals (§0, rule 1)
        days = recovery_days(corte)
        universe = Universe(view.evaluations(day_start(days[-1]) + DAY))
        universe_rows = view.universe_rows()
        visible_until = floor_to(corte, MINUTE)
        rows: list[dict[str, Any]] = []
        states: dict[Key, Done] = {}
        for day in days:
            start = day_start(day)
            end = min(start + DAY, visible_until)
            if end <= start:
                continue  # nothing of this day is visible yet
            for market, symbol in sorted(universe.during(day)):
                observations = view.observations(market, symbol)
                read_from = start - WARMUP
                counts = (
                    view.candle_count(market, symbol, read_from, end),
                    len(observations),
                    universe_rows,
                )
                pending = [
                    f for f in active if self._needs((f.family, market, symbol, day), counts, day)
                ]
                if not pending:
                    continue
                rows_read = view.candles(market, symbol, read_from, end)
                minutes = minutes_from_rows(market, symbol, rows_read, observations)
                for ficha in pending:
                    if minutes:
                        frames = ficha.frames(minutes, downloaded_until=end)
                        rows += [
                            signal_row(s, frames, ficha.params_hash(), self.commit, self.run_id)
                            for s in ficha.signals(frames)
                            if start <= s.at < start + DAY
                            and s.at <= corte
                            and (market, symbol) in universe.at(s.at)
                        ]
                    states[(ficha.family, market, symbol, day)] = Done(corte, counts)
        return rows, states

    def _needs(self, key: Key, counts: tuple[int, int, int], day: date) -> bool:
        held = self.done.get(key)
        return held is None or held.corte < day_start(day) + DAY or held.counts != counts

    def cycle(
        self,
        open_view: Callable[[datetime], AbstractContextManager[View]],
        write: Callable[[list[dict[str, Any]]], None],
        now: datetime,
    ) -> Cycle:
        """One cycle. If reading or writing fails, it raises and no state changes."""
        corte = cut_of(now)
        with open_view(corte) as view:
            rows, states = self.compute(view)
        write(rows)
        self.done.update(states)
        oldest = recovery_days(corte)[0]
        self.done = {key: held for key, held in self.done.items() if key[3] >= oldest}
        return Cycle(corte, len(states), len(rows))


# --- PostgreSQL --------------------------------------------------------------------------


class PostgresView:
    """The rows visible in a cut, read in one REPEATABLE READ, read-only transaction, so
    that every count and every row comes from the same snapshot."""

    def __init__(self, conn: psycopg.Connection, corte: datetime) -> None:
        self.conn = conn
        self.corte = as_utc(corte, "corte")

    def approved(self) -> frozenset[tuple[str, str]]:
        rows = self.conn.execute("SELECT family, params_hash FROM config.strategy_versions")
        return frozenset((family, params_hash) for family, params_hash in rows)

    def evaluations(self, until: datetime) -> list[Evaluation]:
        rows = self.conn.execute(
            "SELECT evaluated_at, market, symbol, in_universe FROM market.universe_snapshots"
            " WHERE recorded_at <= %s AND evaluated_at < %s",
            (self.corte, until),
        )
        return [Evaluation(*row) for row in rows]

    def universe_rows(self) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM market.universe_snapshots WHERE recorded_at <= %s",
            (self.corte,),
        ).fetchone()
        return int(row[0]) if row else 0

    def observations(self, market: str, symbol: str) -> list[Observation]:
        rows = self.conn.execute(
            "SELECT observed_at, multiplier FROM market.instruments"
            " WHERE market = %s AND symbol = %s AND recorded_at <= %s ORDER BY observed_at",
            (market, symbol, self.corte),
        )
        return [Observation(*row) for row in rows]

    def candles(self, market: str, symbol: str, start: datetime, end: datetime) -> list[CandleRow]:
        rows = self.conn.execute(
            "SELECT open_time, open, high, low, close, volume, turnover, state, recorded_at"
            " FROM market.candles WHERE series = 'last' AND market = %s AND symbol = %s"
            " AND open_time >= %s AND open_time < %s AND recorded_at <= %s ORDER BY open_time",
            (market, symbol, start, end, self.corte),
        )
        return [CandleRow(*row) for row in rows]

    def candle_count(self, market: str, symbol: str, start: datetime, end: datetime) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM market.candles WHERE series = 'last' AND market = %s"
            " AND symbol = %s AND open_time >= %s AND open_time < %s AND recorded_at <= %s",
            (market, symbol, start, end, self.corte),
        ).fetchone()
        return int(row[0]) if row else 0


INSERT = (
    "INSERT INTO trading.signals (signal_id, family, code_commit, params_hash, market, symbol,"
    " timeframe, open_time, direction, signal_at, context_open, context_direction,"
    " context_volatility, signal_direction, signal_volatility, levels, provenance,"
    " provenance_minutes, rest_only_minutes, run_id) VALUES (%(signal_id)s, %(family)s,"
    " %(code_commit)s, %(params_hash)s, %(market)s, %(symbol)s, %(timeframe)s, %(open_time)s,"
    " %(direction)s, %(signal_at)s, %(context_open)s, %(context_direction)s,"
    " %(context_volatility)s, %(signal_direction)s, %(signal_volatility)s, %(levels)s,"
    " %(provenance)s, %(provenance_minutes)s, %(rest_only_minutes)s, %(run_id)s)"
    " ON CONFLICT DO NOTHING"
)


def postgres(
    connect: Callable[[], psycopg.Connection],
) -> tuple[
    Callable[[datetime], AbstractContextManager[View]],
    Callable[[list[dict[str, Any]]], None],
]:
    """The two sides of a cycle against PostgreSQL: the view of a cut, and the writing."""

    @contextmanager
    def open_view(corte: datetime) -> Iterator[View]:
        conn = connect()
        try:
            conn.autocommit = False
            conn.isolation_level = IsolationLevel.REPEATABLE_READ
            conn.read_only = True
            yield PostgresView(conn, corte)
        finally:
            conn.rollback()
            conn.close()

    def write(rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        conn = connect()
        try:
            conn.autocommit = False
            with conn.transaction(), conn.cursor() as cursor:
                cursor.executemany(
                    INSERT,
                    [
                        {
                            **row,
                            "signal_id": uuid4(),
                            "levels": Jsonb(row["levels"]),
                            "provenance": Jsonb(row["provenance"]),
                        }
                        for row in rows
                    ],
                )
        finally:
            conn.close()

    return open_view, write
