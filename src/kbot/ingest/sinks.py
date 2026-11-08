"""Where the recorder writes: PostgreSQL in production, memory in tests, files on a PC.

``DatabaseSink`` is the only one that matters for the data (02 §3.8). ``MemorySink`` keeps
everything in lists for the tests. ``FileSink`` writes JSON lines so the recorder can run
against the real KuCoin on a PC without PostgreSQL (the owner's PC has none) and its
output can be checked.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import psycopg

from kbot.ingest.core import InstrumentSpec, ResumePoints, TickOutput
from kbot.market import store
from kbot.market.last_candles import CandleDecision, PendingCandle
from kbot.market.mark_candles import FlowState, IndexMinute, OwnCandle
from kbot.market.reference import FundingRow, InstrumentRow, RiskTierRow
from kbot.market.timeutil import floor_to
from kbot.market.universe import UniverseEntry


class Sink(Protocol):
    def resume_points(self, specs: Iterable[InstrumentSpec]) -> ResumePoints: ...
    def pending_candles(self, since: datetime) -> list[PendingCandle]: ...
    def pending_index_rebuilds(self, since: datetime) -> list[IndexMinute]: ...
    def unclosed_days(self, since: datetime, before: datetime) -> list[datetime]: ...
    def load_universe(self, day: datetime) -> list[UniverseEntry]: ...
    def universe_members(self, day: datetime) -> set[tuple[str, str]]: ...
    def write_universe(self, entries: list[UniverseEntry]) -> None: ...
    def write_instruments(self, rows: list[InstrumentRow], observed_at: datetime) -> int: ...
    def write_risk_tiers(
        self, symbol: str, tiers: list[RiskTierRow], observed_at: datetime
    ) -> bool: ...
    def write_funding(self, rows: list[FundingRow]) -> None: ...
    def write_tick(self, output: TickOutput) -> None: ...
    def write_last_candles(self, decisions: list[CandleDecision]) -> None: ...
    def write_index_candles(self, candles: list[OwnCandle]) -> None: ...
    def write_exhausted_index(self, minutes: list[tuple[IndexMinute, int]]) -> None: ...
    def day_is_closed(self, market: str, symbol: str, day: datetime) -> bool: ...
    def close_day(self, market: str, symbol: str, day: datetime) -> int: ...


def write_tick_to(conn: psycopg.Connection, output: TickOutput) -> None:
    """One tick, in the order the foreign keys need: flows, then the WebSocket and own
    candles that point at them, then depth (slot status before metrics and snapshots)
    and gaps, and last the late evidence (its slots and minutes were written before)."""
    with conn.transaction():
        store.write_flow(conn, output.flows)
        store.write_ws_candles(conn, output.ws_candles)
        store.write_own_candles(conn, "FUTURES", output.own_candles)
        store.write_depth(conn, output.slots, output.minute_snapshots, output.gaps)
        store.write_late_books(conn, output.late_books)
        store.write_late_trading(conn, output.late_trading)


class DatabaseSink:
    """PostgreSQL as kbot_ingest: SELECT and INSERT only (02 §9)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def resume_points(self, specs: Iterable[InstrumentSpec]) -> ResumePoints:
        points: dict[tuple[str, str, str], datetime | None] = {}
        for spec in specs:
            for channel in spec.channels():
                if channel.startswith("obu"):
                    depth = int(channel.removeprefix("obu"))
                    value = store.resume_point(self.conn, spec.market, spec.symbol, depth)
                else:
                    value = store.flow_resume_point(self.conn, spec.market, spec.symbol, channel)
                points[(spec.market, spec.symbol, channel)] = value
        return points

    def pending_candles(self, since: datetime) -> list[PendingCandle]:
        return store.pending_candles(self.conn, since)

    def pending_index_rebuilds(self, since: datetime) -> list[IndexMinute]:
        return store.pending_index_rebuilds(self.conn, since)

    def unclosed_days(self, since: datetime, before: datetime) -> list[datetime]:
        return store.unclosed_days(self.conn, since, before)

    def load_universe(self, day: datetime) -> list[UniverseEntry]:
        return store.load_universe(self.conn, day)

    def universe_members(self, day: datetime) -> set[tuple[str, str]]:
        return store.universe_members(self.conn, day)

    def write_universe(self, entries: list[UniverseEntry]) -> None:
        store.write_universe(self.conn, entries)

    def write_instruments(self, rows: list[InstrumentRow], observed_at: datetime) -> int:
        return store.write_instruments(self.conn, rows, observed_at)

    def write_risk_tiers(
        self, symbol: str, tiers: list[RiskTierRow], observed_at: datetime
    ) -> bool:
        return store.write_risk_tiers(self.conn, symbol, tiers, observed_at)

    def write_funding(self, rows: list[FundingRow]) -> None:
        store.write_funding(self.conn, rows)

    def write_tick(self, output: TickOutput) -> None:
        write_tick_to(self.conn, output)

    def write_last_candles(self, decisions: list[CandleDecision]) -> None:
        store.write_last_candles(self.conn, decisions)

    def write_index_candles(self, candles: list[OwnCandle]) -> None:
        store.write_own_candles(self.conn, "FUTURES", candles)

    def write_exhausted_index(self, minutes: list[tuple[IndexMinute, int]]) -> None:
        store.write_exhausted_index(self.conn, minutes)

    def day_is_closed(self, market: str, symbol: str, day: datetime) -> bool:
        return store.day_is_closed(self.conn, market, symbol, day)

    def close_day(self, market: str, symbol: str, day: datetime) -> int:
        return store.close_day(self.conn, market, symbol, day)


class MemorySink:
    """Everything in lists, for the tests. It answers like an empty database unless the
    test sets ``resume`` or ``universe``."""

    def __init__(self) -> None:
        self.resume: dict[tuple[str, str, str], datetime | None] = {}
        self.universe: list[UniverseEntry] = []
        self.instruments: list[InstrumentRow] = []
        self.risk_tiers: dict[str, list[RiskTierRow]] = {}
        self.funding: list[FundingRow] = []
        self.ticks: list[TickOutput] = []
        self.last_candles: list[CandleDecision] = []
        self.index_candles: list[OwnCandle] = []
        self.exhausted_index: list[tuple[IndexMinute, int]] = []
        self.closed_days: list[tuple[str, str, datetime]] = []

    def resume_points(self, specs: Iterable[InstrumentSpec]) -> ResumePoints:
        return dict(self.resume)

    def pending_candles(self, since: datetime) -> list[PendingCandle]:
        """What ``store.pending_candles`` finds, from the lists (same rules)."""
        decided = {(d.market, d.symbol, d.minute) for d in self.last_candles if d.has_candle}
        closed = set(self.closed_days)
        ws = {
            (w.market, w.symbol, w.candle.open_time): w.candle
            for tick in self.ticks
            for w in tick.ws_candles
        }
        tick_size = {(row.market, row.symbol): row.tick_size for row in self.instruments}
        late = {(t.market, t.symbol, t.minute) for tick in self.ticks for t in tick.late_trading}
        pending = []
        for tick in self.ticks:
            for flow in tick.flows:
                key = (flow.market, flow.symbol, flow.minute)
                if (
                    flow.channel != "trade"
                    or flow.minute < since
                    or key in decided
                    or (flow.market, flow.symbol, floor_to(flow.minute, _DAY)) in closed
                    or (flow.market, flow.symbol) not in tick_size
                ):
                    continue
                pending.append(
                    PendingCandle(
                        flow,
                        ws.get(key),
                        tick_size[(flow.market, flow.symbol)],
                        seen_late=key in late,
                    )
                )
        return pending

    def unclosed_days(self, since: datetime, before: datetime) -> list[datetime]:
        """What ``store.unclosed_days`` finds, from the lists (same rules)."""
        closed = set(self.closed_days)
        days = {
            (f.market, f.symbol, floor_to(f.minute, _DAY))
            for t in self.ticks
            for f in t.flows
            if f.channel == "trade" and since <= f.minute < before
        }
        return sorted({day for market, symbol, day in days if (market, symbol, day) not in closed})

    def pending_index_rebuilds(self, since: datetime) -> list[IndexMinute]:
        """What ``store.pending_index_rebuilds`` finds, from the lists (same rules)."""
        done = {(c.symbol, c.open_time) for c in self.index_candles}
        done |= {(m.symbol, m.minute) for m, _ in self.exhausted_index}
        done |= {
            (c.symbol, c.open_time)
            for t in self.ticks
            for c in t.own_candles
            if c.series == "index"
        }
        return [
            IndexMinute(f.symbol, f.minute, f.flow_state)
            for t in self.ticks
            for f in t.flows
            if f.channel == "mark-price"
            and f.flow_state is not FlowState.S1
            and f.minute >= since
            and (f.symbol, f.minute) not in done
        ]

    def load_universe(self, day: datetime) -> list[UniverseEntry]:
        in_day = [e for e in self.universe if day <= e.evaluated_at < day + _DAY]
        if not in_day:
            return []
        latest = max(e.evaluated_at for e in in_day)
        return [e for e in in_day if e.evaluated_at == latest]

    def universe_members(self, day: datetime) -> set[tuple[str, str]]:
        before = [e.evaluated_at for e in self.universe if e.evaluated_at <= day]
        start = max(before) if before else day
        return {
            (e.market, e.symbol)
            for e in self.universe
            if e.in_universe and start <= e.evaluated_at < day + _DAY
        }

    def write_universe(self, entries: list[UniverseEntry]) -> None:
        self.universe += entries

    def write_instruments(self, rows: list[InstrumentRow], observed_at: datetime) -> int:
        self.instruments += rows
        return len(rows)

    def write_risk_tiers(
        self, symbol: str, tiers: list[RiskTierRow], observed_at: datetime
    ) -> bool:
        self.risk_tiers[symbol] = tiers
        return True

    def write_funding(self, rows: list[FundingRow]) -> None:
        self.funding += rows

    def write_tick(self, output: TickOutput) -> None:
        if not output.is_empty():
            self.ticks.append(output)

    def write_last_candles(self, decisions: list[CandleDecision]) -> None:
        self.last_candles += decisions

    def write_index_candles(self, candles: list[OwnCandle]) -> None:
        self.index_candles += candles

    def write_exhausted_index(self, minutes: list[tuple[IndexMinute, int]]) -> None:
        self.exhausted_index += minutes

    def day_is_closed(self, market: str, symbol: str, day: datetime) -> bool:
        return (market, symbol, day) in self.closed_days

    def close_day(self, market: str, symbol: str, day: datetime) -> int:
        self.closed_days.append((market, symbol, day))
        return 24 * 60


class FileSink(MemorySink):
    """JSON lines in a folder, plus counters, for a run on a PC without PostgreSQL."""

    def __init__(self, folder: Path) -> None:
        super().__init__()
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.counts: Counter[str] = Counter()

    def _append(self, name: str, rows: Iterable[Any]) -> None:
        with (self.folder / f"{name}.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(_plain(row), default=str, separators=(",", ":")) + "\n")

    def write_universe(self, entries: list[UniverseEntry]) -> None:
        super().write_universe(entries)
        self._append("universe", entries)

    def write_instruments(self, rows: list[InstrumentRow], observed_at: datetime) -> int:
        self._append("instruments", [{**_plain(r), "raw": None} for r in rows])
        return super().write_instruments(rows, observed_at)

    def write_funding(self, rows: list[FundingRow]) -> None:
        super().write_funding(rows)
        self._append("funding", rows)

    def write_tick(self, output: TickOutput) -> None:
        self._append("flows", output.flows)
        self._append("ws_candles", output.ws_candles)
        self._append("own_candles", output.own_candles)
        self._append("gaps", output.gaps)
        self._append(
            "depth_slots",
            (
                {
                    "market": s.market,
                    "symbol": s.symbol,
                    "depth": s.depth,
                    "slot_start": s.slot_start,
                    "state": s.state.value,
                    "sequence": s.sequence,
                    "age_ms": s.age_ms,
                    "mid": s.metrics.mid if s.metrics else None,
                    "spread": s.metrics.spread if s.metrics else None,
                }
                for s in output.slots
            ),
        )
        self._append("late_books", output.late_books)
        self._append("late_trading", output.late_trading)
        self.counts.update("late_book" for _ in output.late_books)
        self.counts.update("late_trading" for _ in output.late_trading)
        self.counts.update(f"slot:{s.state.value}" for s in output.slots)
        self.counts.update(f"flow:{f.channel}:{f.flow_state.value}" for f in output.flows)
        self.counts.update(f"own:{c.series}" for c in output.own_candles)
        self.counts.update("gap" for _ in output.gaps)
        self.counts.update("minute_snapshot" for _ in output.minute_snapshots)
        self.counts.update("index_rebuild_needed" for _ in output.index_rebuilds)

    def write_last_candles(self, decisions: list[CandleDecision]) -> None:
        self._append(
            "last_candles",
            (
                {
                    "market": d.market,
                    "symbol": d.symbol,
                    "minute": d.minute,
                    "state": d.state.value,
                    "flow": d.flow_state.value,
                    "differences": d.differences,
                    "rest_volume": d.rest.volume if d.rest else None,
                    "ws_volume": d.ws.volume if d.ws else None,
                }
                for d in decisions
            ),
        )
        self.counts.update(f"last:{d.state.value}" for d in decisions)

    def write_index_candles(self, candles: list[OwnCandle]) -> None:
        self._append("index_candles", candles)
        self.counts.update("index_rebuilt" for _ in candles)

    def write_exhausted_index(self, minutes: list[tuple[IndexMinute, int]]) -> None:
        self._append(
            "index_exhausted",
            (
                {
                    "symbol": m.symbol,
                    "minute": m.minute,
                    "flow_state": m.flow_state.value,
                    "attempts": attempts,
                }
                for m, attempts in minutes
            ),
        )
        self.counts.update("index_exhausted" for _ in minutes)


_DAY = timedelta(days=1)


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value
