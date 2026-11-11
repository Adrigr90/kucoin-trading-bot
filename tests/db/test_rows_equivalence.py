"""``--sink rows`` writes exactly the rows PostgreSQL stores (02 §3.9; review of the E3 design).

Every write goes both to ``DatabaseSink`` and to ``RowSink``: the ticks of the recorder's
core, the same late book twice (PostgreSQL keeps one row: review of E3c, E3c-04), and, in
the second test, a whole run of the recorder with its REST writers. For each table the files
hold the same rows as PostgreSQL once they are stored, compared in the canonical form of the
archive over every column but ``recorded_at`` (the moment of the insert, which only
PostgreSQL knows). A column no INSERT names takes, in the files, the constant default that
``tools/measure_archive.py`` reads from migration 0003. The only tables PostgreSQL holds rows
of and the files do not are those ``RowSink`` leaves out on purpose.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from kbot.archive.archiver import TABLES
from kbot.archive.canonical import canonical_value, json_text
from kbot.ingest.core import InstrumentSpec, RecorderCore, TickOutput
from kbot.ingest.rows import RowSink
from kbot.ingest.service import Recorder
from kbot.ingest.sinks import DatabaseSink
from kbot.kucoin.ratelimit import RestRateLimiter
from kbot.kucoin.rest import KucoinRestClient
from kbot.market.depth import LateBook
from kbot.market.last_candles import CandleDecision, KeptEvidence
from kbot.market.mark_candles import IndexMinute, OwnCandle
from tests.unit.ingest.fake_kucoin import FakeKucoin
from tests.unit.ingest.messages import (
    beat,
    book,
    feed_minute,
    kline,
    mark,
    pong,
    rest_candles_until,
    trade,
)
from tools.measure_archive import MIGRATION, table_specs

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]
FreeDay = Callable[[], datetime]
Factory = Callable[..., KucoinRestClient]
# Constant defaults of the columns no INSERT names, as the measurement fills them in.
DEFAULTS = {
    name: {c.name: c.default for c in spec.columns}
    for name, spec in table_specs(MIGRATION.read_text(encoding="utf-8")).items()
}
# What only PostgreSQL holds: the decisions RowSink leaves to MemorySink (kbot/ingest/rows.py).
DATABASE_ONLY = {"minute_status", "trading_seen_late", "late_ws_candles", "late_ws_repeats"}


def feed(core: RecorderCore, minute: datetime, specs: tuple[InstrumentSpec, ...]) -> None:
    """Books every 10 s (also of depth 50 where the instrument has it, except the futures
    contract, whose depth-50 slots stay without data), a trade and a candle per instrument,
    and a mark value per second for the futures contract."""
    for second in range(0, 60, 10):
        moment = minute + timedelta(seconds=second + 1)
        sequence = int(moment.timestamp())
        for spec in specs:
            core.on_message(spec.market, book(spec.market, spec.symbol, sequence, moment), moment)
            if spec.l50 and spec.market == "SPOT":
                message = book(spec.market, spec.symbol, sequence, moment, "50")
                core.on_message(spec.market, message, moment)
    traded = minute + timedelta(seconds=30)
    for spec in specs:
        core.on_message(spec.market, trade(spec.market, spec.symbol, traded), traded)
        core.on_message(spec.market, kline(spec.market, spec.symbol, minute, traded), traded)
    futures = next(spec.symbol for spec in specs if spec.market == "FUTURES")
    for second in range(60):
        moment = minute + timedelta(seconds=second)
        core.on_message("FUTURES", mark(futures, moment), moment)


def column_types(conn: psycopg.Connection, table: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod)"
        " FROM pg_catalog.pg_attribute a WHERE a.attrelid = %s::regclass AND a.attnum > 0"
        " AND NOT a.attisdropped ORDER BY a.attnum",
        (f"market.{table}",),
    ).fetchall()
    return dict(rows)


def canonical(value: Any, sql_type: str, *, from_file: bool) -> Any:
    """A value of the files (JSON) or of PostgreSQL (psycopg) in the canonical form of the
    archive, so that equal values compare equal whatever their origin."""
    if value is None:
        return None
    if sql_type == "jsonb":
        return json_text(value)
    if from_file and sql_type == "numeric":
        value = Decimal(value)
    elif from_file and sql_type == "timestamp with time zone":
        value = datetime.fromisoformat(value)
    return canonical_value(value)


def compare(
    conn: psycopg.Connection, rows: RowSink, symbols: list[str], first: datetime, end: datetime
) -> None:
    """The rows of the files against those PostgreSQL stored for these symbols with their
    time in [``first``, ``end``), table by table, over every column but recorded_at."""

    def stored(table: str, columns: sql.Composable) -> list[tuple[Any, ...]]:
        key = sql.Identifier(TABLES.get(table, "start_at"))
        query = sql.SQL(
            "SELECT {} FROM market.{} WHERE symbol = ANY(%s) AND {} >= %s AND {} < %s"
        ).format(columns, sql.Identifier(table), key, key)
        return conn.execute(query, (symbols, first, end)).fetchall()

    for table, count in sorted(rows.counts.items()):
        path = rows.folder / f"{table}.jsonl"
        written = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert len(written) == count
        types = column_types(conn, table)
        columns = [name for name in types if name != "recorded_at"]
        assert {name for row in written for name in row} <= set(columns), table
        defaults = DEFAULTS.get(table, {})
        in_files = sorted(
            json.dumps(
                [
                    canonical(r[c] if c in r else defaults.get(c), types[c], from_file=True)
                    for c in columns
                ]
            )
            for r in written
        )
        in_database = sorted(
            json.dumps(
                [canonical(v, types[c], from_file=False) for c, v in zip(columns, r, strict=True)]
            )
            for r in stored(table, sql.SQL(", ").join(map(sql.Identifier, columns)))
        )
        assert in_files == in_database, table
    only = {
        table
        for table in [*TABLES, "data_gaps"]
        if table not in rows.counts and stored(table, sql.SQL("1"))
    }
    assert only <= DATABASE_ONLY


def test_the_rows_sink_writes_what_postgresql_stores(
    connect: Connect, free_day: FreeDay, tmp_path: Path
) -> None:
    # Symbols of their own, so that only this test's rows carry them.
    tag = secrets.token_hex(4).upper()
    specs = (
        InstrumentSpec("SPOT", f"S{tag}-USDT", Decimal("0.1"), Decimal(1), l50=True),
        InstrumentSpec("SPOT", f"E{tag}-USDT", Decimal("0.01"), Decimal(1), l50=False),
        InstrumentSpec(
            "FUTURES", f"F{tag}USDTM", Decimal("0.1"), Decimal("0.001"), True, f".K{tag}"
        ),
    )
    symbols = [spec.symbol for spec in specs] + [f".K{tag}"]
    start = free_day() + timedelta(hours=12)
    core = RecorderCore(specs, started_at=start)
    for market in ("SPOT", "FUTURES"):
        core.on_state(market, "connected", start, "wss://...")
    rows = RowSink(tmp_path)
    # A late book that arrives twice: PostgreSQL keeps one row (review of E3c, E3c-04).
    late = LateBook(
        "SPOT",
        specs[0].symbol,
        5,
        start + timedelta(seconds=10),
        start + timedelta(seconds=20),
        7,
        start + timedelta(seconds=15),
        start + timedelta(seconds=75),
    )
    with connect("kbot_ingest") as conn:
        database = DatabaseSink(conn)
        outputs = []
        for minute in (start, start + timedelta(minutes=1)):
            feed(core, minute, specs)
            moment = minute + timedelta(seconds=62)
            pong(core, moment)
            outputs.append(core.tick(moment))
        outputs += [TickOutput(late_books=[late, late]), TickOutput(late_books=[late])]
        for output in outputs:
            database.write_tick(output)
            rows.write_tick(output)
        rows.close()
        assert rows.counts["depth_late_books"] == 1
        compare(conn, rows, symbols, start - timedelta(hours=1), start + timedelta(hours=1))
    expected = {"data_quality_minute", "ws_candles", "candles", "depth_slot_status"}
    assert expected | {"depth_metrics", "depth_snapshots", "depth_late_books"} <= set(rows.counts)


class Both(DatabaseSink):
    """DatabaseSink that also gives every write to a RowSink: the recorder reads from
    PostgreSQL, and both receive the same rows."""

    def __init__(self, conn: psycopg.Connection, rows: RowSink) -> None:
        super().__init__(conn)
        self.rows = rows

    def write_tick(self, output: TickOutput) -> list[KeptEvidence]:
        kept = super().write_tick(output)
        self.rows.write_tick(output)
        return kept

    def write_last_candles(self, decisions: list[CandleDecision]) -> None:
        super().write_last_candles(decisions)
        self.rows.write_last_candles(decisions)

    def write_index_candles(self, candles: list[OwnCandle]) -> None:
        super().write_index_candles(candles)
        self.rows.write_index_candles(candles)

    def write_exhausted_index(self, minutes: list[tuple[IndexMinute, int]]) -> None:
        super().write_exhausted_index(minutes)
        self.rows.write_exhausted_index(minutes)


def test_a_whole_run_with_the_rest_writers_writes_the_same_rows(
    connect: Connect, test_client: Factory, free_day: FreeDay, tmp_path: Path
) -> None:
    """The scenario of tests/db/test_ingest.py: candles of the last series decided with
    REST, and a midnight with the close of the day before (review of E3c, E3c-04)."""
    day = free_day()
    midnight = day + timedelta(days=1)
    start = midnight - timedelta(minutes=2, seconds=30)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"
    fake = FakeKucoin(spot=[spot], futures=[futures], candles=rest_candles_until(midnight))
    rows = RowSink(tmp_path)
    with connect("kbot_ingest") as conn:
        sink = Both(conn, rows)
        recorder = Recorder(sink, rest=test_client(fake, RestRateLimiter(pool_weight=10**6)))

        async def run() -> None:
            await recorder.startup(start)
            core = recorder.core
            assert core is not None
            for market in ("SPOT", "FUTURES"):
                core.on_state(market, "connected", start + timedelta(seconds=1), "wss://...")
            feed_minute(core, midnight - timedelta(minutes=2), spot, futures)
            await beat(recorder, midnight - timedelta(minutes=2))
            await beat(recorder, midnight - timedelta(minutes=1))
            feed_minute(core, midnight - timedelta(minutes=1), spot, futures)
            await beat(recorder, midnight)
            await beat(recorder, midnight + timedelta(seconds=16))
            await beat(recorder, midnight + timedelta(minutes=2, seconds=1))

        asyncio.run(run())
        rows.close()
        written = {
            json.loads(line)["symbol"]
            for table in rows.counts
            for line in (tmp_path / f"{table}.jsonl").read_text(encoding="utf-8").splitlines()
        }
        assert {spot, futures} <= written
        # From the first minute recorded: closing the day before fills in SQL the minutes
        # without flow (store.fill_missing_flow), which only PostgreSQL holds.
        first = midnight - timedelta(minutes=2)
        compare(conn, rows, sorted(written), first, day + timedelta(days=4))
        last = conn.execute(
            "SELECT count(*) FROM market.candles WHERE series = 'last' AND symbol = ANY(%s)",
            ([spot, futures],),
        ).fetchone()
    assert last is not None and last[0] > 0  # the REST writers did write
