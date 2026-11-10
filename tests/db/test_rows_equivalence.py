"""``--sink rows`` writes exactly the rows PostgreSQL stores (02 §3.9; review of the E3 design).

Two minutes of books, trades, candles and mark values go through the recorder's core; every
tick is written both by ``DatabaseSink`` and by ``RowSink``. For each table the files hold the
same rows, with the same values, as PostgreSQL once they are stored: compared in the canonical
form of the archive, without ``recorded_at`` (the moment of the insert, which only PostgreSQL
knows). A column an INSERT leaves out is NULL in the files, so PostgreSQL must store NULL too.
Only then do the measurements of stage E3c use the files.
"""

from __future__ import annotations

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

from kbot.archive.canonical import canonical_value, json_text
from kbot.ingest.core import InstrumentSpec, RecorderCore
from kbot.ingest.rows import RowSink
from kbot.ingest.sinks import DatabaseSink
from tests.unit.ingest.messages import book, kline, mark, pong, trade

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]
FreeDay = Callable[[], datetime]


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
        " AND NOT a.attisdropped",
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
    with connect("kbot_ingest") as conn:
        database = DatabaseSink(conn)
        for minute in (start, start + timedelta(minutes=1)):
            feed(core, minute, specs)
            moment = minute + timedelta(seconds=62)
            pong(core, moment)
            output = core.tick(moment)
            database.write_tick(output)
            rows.write_tick(output)
        rows.close()
        for table, count in sorted(rows.counts.items()):
            path = tmp_path / f"{table}.jsonl"
            written = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            assert len(written) == count
            columns: list[str] = []
            for row in written:
                columns.extend(name for name in row if name not in columns)
            types = column_types(conn, table)
            stored = conn.execute(
                sql.SQL("SELECT {} FROM market.{} WHERE symbol = ANY(%s)").format(
                    sql.SQL(", ").join(map(sql.Identifier, columns)), sql.Identifier(table)
                ),
                (symbols,),
            ).fetchall()
            in_files = sorted(
                json.dumps([canonical(r.get(c), types[c], from_file=True) for c in columns])
                for r in written
            )
            in_database = sorted(
                json.dumps(
                    [
                        canonical(v, types[c], from_file=False)
                        for c, v in zip(columns, r, strict=True)
                    ]
                )
                for r in stored
            )
            assert in_files == in_database, table
    expected = {"data_quality_minute", "ws_candles", "candles", "depth_slot_status"}
    assert expected | {"depth_metrics", "depth_snapshots"} <= set(rows.counts)
