"""Measurements of stage E3c in the PostgreSQL 18 of the CI (02 §3.9, points 1 and 8).

Production keeps about 1,250 partitions: twelve tables, each with its 90 hot days and the 14
days prepared ahead. With that many in place, the first test measures how long PostgreSQL
takes to plan the recorder's queries, and how long ``market.prepare_partitions()`` takes to
create its range and to check it again (its daily run). The second measures how many bytes a
row of each table takes in PostgreSQL, with its indexes, and in the WAL, for an hour of
realistic books, trades, candles and mark values.

The values are published as notices of the CI, which the API serves without signing in.
The asserts only stop values far beyond any reasonable bound, so that a slow runner does not
fail the CI; the values themselves go to 02 §3.9.
"""

from __future__ import annotations

import random
import secrets
import statistics
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg import sql

from kbot.archive.archiver import TABLES
from kbot.ingest.core import InstrumentSpec, RecorderCore
from kbot.ingest.sinks import DatabaseSink
from tests.unit.ingest.messages import ns, pong, seconds

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]
Partitions = Callable[[date, date], None]
FreeDay = Callable[[], datetime]
HOT_DAYS = 90
RUNS = 5
MINUTES = 60


def publish(capsys: pytest.CaptureFixture[str], title: str, lines: list[str]) -> None:
    """A notice of the CI with these lines: GitHub reads it from the output of the job."""
    text = "%0A".join(line.replace("%", "%25").replace("\r", "%0D") for line in lines)
    with capsys.disabled():
        print(f"\n::notice title={title}::{text}", flush=True)


def _relations(plan: dict[str, Any]) -> int:
    own = 1 if "Relation Name" in plan else 0
    return own + sum(_relations(child) for child in plan.get("Plans", ()))


def planning(conn: psycopg.Connection, query: sql.Composable) -> tuple[float, int]:
    """Milliseconds PostgreSQL took to plan the query, and the relations of the plan."""
    row = conn.execute(sql.SQL("EXPLAIN (SUMMARY, FORMAT JSON) ") + query).fetchone()
    assert row is not None
    (result,) = row[0]
    return float(result["Planning Time"]), _relations(result["Plan"])


def test_planning_and_maintenance_with_the_partitions_of_production(
    connect: Connect,
    partitions: Partitions,
    recovery_range: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def database_size() -> int:
        with connect("postgres") as admin:
            row = admin.execute("SELECT pg_database_size(current_database())").fetchone()
            assert row is not None
            return int(row[0])

    with connect("postgres") as admin:
        row = admin.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()
        assert row is not None
        today: date = row[0]
    # The hot days that prepare_partitions() no longer covers: [today - 90, today - 31].
    # What they add to the database, still empty, is what each partition costs on disk:
    # the files of its indexes and its TOAST table, and its rows in the catalog.
    empty = database_size()
    started = time.perf_counter()
    partitions(today - timedelta(days=HOT_DAYS), today - timedelta(days=31))
    older = time.perf_counter() - started
    empty = (database_size() - empty) // ((HOT_DAYS - 30) * 12)
    with connect("kbot_archive") as archive:
        started = time.perf_counter()
        row = archive.execute("SELECT market.prepare_partitions()").fetchone()
        prepared = time.perf_counter() - started
        created = int(row[0]) if row else 0
        started = time.perf_counter()
        row = archive.execute("SELECT market.prepare_partitions()").fetchone()
        daily = time.perf_counter() - started
        assert row is not None and row[0] == 0
    with connect("postgres") as admin:
        row = admin.execute(
            "SELECT count(*) FROM pg_catalog.pg_inherits i"
            " JOIN pg_catalog.pg_class p ON p.oid = i.inhparent"
            " JOIN pg_catalog.pg_namespace n ON n.oid = p.relnamespace"
            " WHERE n.nspname = 'market' AND p.relkind = 'p'"
        ).fetchone()
        assert row is not None
        total = int(row[0])
    assert total >= 12 * (HOT_DAYS + 1 + 14)

    day = today - timedelta(days=1)
    start = sql.Literal(datetime(day.year, day.month, day.day, tzinfo=UTC))
    queries = {
        "resume_point (every day)": sql.SQL(
            "SELECT max(slot_start) FROM market.depth_slot_status"
            " WHERE market = 'SPOT' AND symbol = 'BTC-USDT' AND depth = 5"
        ),
        "flow_resume_point (every day)": sql.SQL(
            "SELECT max(minute_start) FROM market.data_quality_minute"
            " WHERE market = 'SPOT' AND symbol = 'BTC-USDT' AND channel = 'trade'"
        ),
        "one day of depth_metrics": sql.SQL(
            "SELECT * FROM market.depth_metrics WHERE market = 'SPOT' AND symbol = 'BTC-USDT'"
            " AND depth = 5 AND slot_start >= {start} AND slot_start < {start} + interval '1 day'"
        ).format(start=start),
        "insert of a slot": sql.SQL(
            "INSERT INTO market.depth_slot_status (market, symbol, depth, slot_start, state,"
            " sequence, snapshot_age_ms) VALUES ('SPOT', 'BTC-USDT', 5, {start}, 'valida', 1, 0)"
        ).format(start=start),
    }
    lines = [
        f"{total} partitions of the market schema",
        f"{(HOT_DAYS - 30) * 12} partitions of [today - 90, today - 31] created by the tests"
        f" in {older:.1f} s; each one, empty, adds {empty} bytes to the database",
        f"prepare_partitions(): {created} created in {prepared:.1f} s"
        f" ({prepared / max(created, 1) * 1000:.1f} ms each); run again, 0 created"
        f" in {daily:.2f} s",
        "planning (ms): first in a new connection / median of the next 5; relations planned",
    ]
    for name, query in queries.items():
        with connect("kbot_ingest") as conn:
            cold, relations = planning(conn, query)
            warm = statistics.median(planning(conn, query)[0] for _ in range(RUNS))
        lines.append(f"  {name}: {cold:.1f} / {warm:.2f}; {relations}")
        assert warm < 1000, name
        if name == "one day of depth_metrics":
            assert relations == 1  # pruned when planned: only that day's partition
    publish(capsys, "E3c planning and maintenance", lines)
    assert daily < 120


# An hour of a small realistic universe: prices, sizes and ticks of real instruments, so
# that the numeric values take the room they take in production.
UNIVERSE = (
    # market, symbol, tick, multiplier, level 50, starting mid, size decimals
    ("SPOT", "BTC", "0.1", "1", True, "73759.6", 8),
    ("SPOT", "ETH", "0.01", "1", True, "2875.43", 7),
    ("SPOT", "SOL", "0.001", "1", False, "162.385", 4),
    ("SPOT", "XRP", "0.00001", "1", False, "0.61234", 2),
    ("SPOT", "PEPE", "0.000000001", "1", False, "0.000023417", 0),
    ("SPOT", "USDC", "0.0001", "1", False, "1.0002", 4),
    ("FUTURES", "XBT", "0.1", "0.001", True, "73771.3", 0),
    ("FUTURES", "ETH", "0.01", "0.01", False, "2876.12", 0),
    ("FUTURES", "SOL", "0.001", "0.1", False, "162.401", 0),
    ("FUTURES", "DOGE", "0.00001", "100", False, "0.17345", 0),
)


def _text(value: Decimal) -> str:
    return format(value.normalize(), "f")


class Market:
    """The books and prices of one instrument, moving a few ticks at a time."""

    def __init__(self, row: tuple[str, str, str, str, bool, str, int], tag: str) -> None:
        market, base, tick, multiplier, l50, mid, decimals = row
        self.market = market
        self.symbol = f"{base}{tag}-USDT" if market == "SPOT" else f"{base}{tag}USDTM"
        self.index = f".K{base}{tag}USDT" if market == "FUTURES" else None
        self.tick = Decimal(tick)
        self.mid = Decimal(mid)
        self.decimals = decimals
        self.spec = InstrumentSpec(
            market, self.symbol, self.tick, Decimal(multiplier), l50, self.index
        )
        self.trades = 0

    def size(self, rng: random.Random) -> str:
        units = rng.randint(1, 5 * 10 ** max(self.decimals, 2))
        return _text(Decimal(units).scaleb(-self.decimals))

    def move(self, rng: random.Random) -> None:
        self.mid = max(self.tick * 10, self.mid + self.tick * rng.randint(-3, 3))

    def book(self, rng: random.Random, sequence: int, at: datetime, depth: int) -> dict[str, Any]:
        bids = [[_text(self.mid - self.tick * (i + 1)), self.size(rng)] for i in range(depth)]
        asks = [[_text(self.mid + self.tick * i), self.size(rng)] for i in range(depth)]
        return {
            "T": f"obu.{self.market}",
            "dp": str(depth),
            "t": "snapshot",
            "P": ns(at),
            "d": {
                "s": self.symbol,
                "C": sequence,
                "O": sequence,
                "M": ns(at),
                "b": bids,
                "a": asks,
            },
        }

    def trade(self, rng: random.Random, at: datetime) -> dict[str, Any]:
        self.trades += 1
        return {
            "T": f"trade.{self.market}",
            "P": ns(at),
            "d": {
                "s": self.symbol,
                "p": _text(self.mid),
                "q": self.size(rng),
                "S": "buy",
                "M": ns(at),
                "ti": str(self.trades),
                "E": 1,
            },
        }

    def kline(self, rng: random.Random, minute: datetime, at: datetime) -> dict[str, Any]:
        opened = seconds(minute)
        low, high = self.mid - self.tick * 4, self.mid + self.tick * 5
        volume = Decimal(self.size(rng)) * 7
        return {
            "T": f"kline.{self.market}",
            "P": ns(at),
            "d": {
                "s": self.symbol,
                "i": "1min",
                "O": opened,
                "C": opened + 60,
                "o": _text(self.mid - self.tick),
                "h": _text(high),
                "l": _text(low),
                "c": _text(self.mid),
                "v": _text(volume),
                "a": _text(volume * self.mid),
                "S": False,
            },
        }

    def mark(self, at: datetime) -> dict[str, Any]:
        ms = seconds(at) * 1000
        return {
            "T": "mark-price",
            "P": ns(at),
            "d": {
                "s": self.symbol,
                "mp": _text(self.mid + self.tick / 4),
                "ip": _text(self.mid - self.tick / 2),
                "oi": "5",
                "ts": ms,
            },
        }


def feed(core: RecorderCore, markets: list[Market], minute: datetime, rng: random.Random) -> None:
    for second in range(0, 60, 10):
        moment = minute + timedelta(seconds=second + 1)
        sequence = int(moment.timestamp())
        for m in markets:
            m.move(rng)
            core.on_message(m.market, m.book(rng, sequence, moment, 5), moment)
            if m.spec.l50:
                core.on_message(m.market, m.book(rng, sequence, moment, 50), moment)
    traded = minute + timedelta(seconds=30)
    for m in markets:
        core.on_message(m.market, m.trade(rng, traded), traded)
        core.on_message(m.market, m.kline(rng, minute, traded), traded)
    for second in range(60):
        moment = minute + timedelta(seconds=second)
        for m in markets:
            if m.market == "FUTURES":
                core.on_message("FUTURES", m.mark(moment), moment)


# What the core does not write: the candles of the last series (the service, after REST)
# and the minute statuses (the close of a day), here one per minute with a candle.
LAST_CANDLES = (
    "INSERT INTO market.candles (series, market, symbol, open_time, open, high, low, close,"
    " volume, turnover, ws_volume, ws_turnover, state, evidence, flow_channel, flow_state)"
    " SELECT 'last', w.market, w.symbol, w.open_time, w.open, w.high, w.low, w.close,"
    " w.volume, w.turnover, w.volume, w.turnover, 'cerrada_cruzada',"
    " 'vela_cruzada_consistente', 'trade', 'S1' FROM market.ws_candles w"
    " JOIN market.data_quality_minute d ON d.market = w.market AND d.symbol = w.symbol"
    " AND d.channel = 'trade' AND d.minute_start = w.open_time"
    " WHERE d.flow_state = 'S1' AND w.symbol = ANY(%s)"
)
MINUTE_STATUS = (
    "INSERT INTO market.minute_status (market, symbol, minute_start, status, candle_state,"
    " flow_channel, flow_state, candle_series) SELECT market, symbol, open_time, 'datos',"
    " state, 'trade', flow_state, 'last' FROM market.candles"
    " WHERE series = 'last' AND symbol = ANY(%s)"
)


def sizes(admin: psycopg.Connection, day: date) -> Iterator[tuple[str, int, int, int, int]]:
    """Rows, heap, TOAST and index bytes of each table's partition of ``day``."""
    for table in TABLES:
        partition = f"market.{table}_p{day:%Y%m%d}"
        row = admin.execute(
            sql.SQL("SELECT count(*) FROM {}").format(
                sql.Identifier("market", f"{table}_p{day:%Y%m%d}")
            )
        ).fetchone()
        assert row is not None
        rows = int(row[0])
        row = admin.execute(
            "SELECT pg_relation_size(oid),"
            " COALESCE(pg_total_relation_size(NULLIF(reltoastrelid, 0)), 0),"
            " pg_indexes_size(oid) FROM pg_catalog.pg_class WHERE oid = %s::regclass",
            (partition,),
        ).fetchone()
        assert row is not None
        yield table, rows, int(row[0]), int(row[1]), int(row[2])


def test_bytes_per_row_in_postgresql(
    connect: Connect, free_day: FreeDay, capsys: pytest.CaptureFixture[str]
) -> None:
    rng = random.Random(9303)
    tag = secrets.token_hex(3).upper()
    markets = [Market(row, tag) for row in UNIVERSE]
    symbols = [m.symbol for m in markets] + [m.index for m in markets if m.index]
    day = free_day()
    start = day + timedelta(hours=1)
    core = RecorderCore([m.spec for m in markets], started_at=start)
    for market in ("SPOT", "FUTURES"):
        core.on_state(market, "connected", start, "wss://...")
    with connect("postgres") as admin:
        row = admin.execute("SELECT pg_current_wal_insert_lsn()::text").fetchone()
        assert row is not None
        before = row[0]
    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        for n in range(MINUTES):
            minute = start + timedelta(minutes=n)
            feed(core, markets, minute, rng)
            moment = minute + timedelta(seconds=62)
            pong(core, moment)
            sink.write_tick(core.tick(moment))
    with connect("postgres") as admin:
        admin.execute(LAST_CANDLES, (symbols,))
        admin.execute(MINUTE_STATUS, (symbols,))
        row = admin.execute(
            "SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(), %s::pg_lsn)", (before,)
        ).fetchone()
        assert row is not None
        wal = int(row[0])
        measured = list(sizes(admin, day.date()))
    lines = ["table: rows; bytes per row of heap + TOAST + indexes = total"]
    all_rows = 0
    for table, rows, heap, toast, indexes in measured:
        if not rows:
            continue
        all_rows += rows
        total = (heap + toast + indexes) / rows
        lines.append(
            f"  {table}: {rows}; {heap / rows:.0f} + {toast / rows:.0f} + {indexes / rows:.0f}"
            f" = {total:.0f}"
        )
        assert total < 20_000, table
    lines.append(f"WAL: {wal} bytes for {all_rows} rows ({wal / all_rows:.0f} per row)")
    publish(capsys, "E3c bytes per row in PostgreSQL", lines)
    written = {table for table, rows, *_ in measured if rows}
    assert {
        "data_quality_minute",
        "candles",
        "ws_candles",
        "minute_status",
        "depth_slot_status",
        "depth_metrics",
        "depth_snapshots",
    } <= written
