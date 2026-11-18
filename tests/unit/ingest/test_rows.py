"""``--sink rows`` records the rows that the writers of ``market.store`` send (02 §3.9, E3c).

Here without PostgreSQL; ``tests/db/test_rows_equivalence.py`` compares them, in the CI, with
the rows PostgreSQL stores for the same input.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from kbot.ingest.core import TickOutput
from kbot.ingest.rows import PRIMARY_KEYS, FolderInUse, RowSink, insert_row
from kbot.market import store
from kbot.market.depth import LateBook
from kbot.market.gaps import DataGap, GapCause
from kbot.market.last_candles import FlowMinute
from kbot.market.mark_candles import FlowState
from tools.measure_archive import MIGRATION, table_specs

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def test_an_insert_becomes_its_table_and_its_row_with_its_literals() -> None:
    query = (
        "INSERT INTO market.candles (series, market, symbol, low, samples, flow_channel)"
        " VALUES ('last', %s, %s, %s, NULL, 'trade') ON CONFLICT DO NOTHING"
    )
    table, row = insert_row(query, ("SPOT", "BTC-USDT", Decimal("100.50"))) or ("", {})
    assert table == "candles"
    assert row == {
        "series": "last",
        "market": "SPOT",
        "symbol": "BTC-USDT",
        "low": "100.50",
        "samples": None,
        "flow_channel": "trade",
    }
    assert insert_row("SELECT day FROM market.archive_seals WHERE day = ANY(%s)", ([],)) is None
    snapshot = insert_row(
        "INSERT INTO market.depth_snapshots (bids, received_at) VALUES (%s, %s)",
        (Jsonb([["100", "2"]]), T0),
    )
    assert snapshot == ("depth_snapshots", {"bids": [["100", "2"]], "received_at": T0.isoformat()})


def test_the_rows_sink_writes_what_the_store_writers_send(tmp_path: Path) -> None:
    sink = RowSink(tmp_path)
    flow = FlowMinute("SPOT", "BTC-USDT", "trade", T0, FlowState.S1, 12, 0, 0)
    store.write_flow(sink._conn, [flow])  # type: ignore[arg-type]
    sink.close()
    (line,) = (tmp_path / "data_quality_minute.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(line) == {
        "market": "SPOT",
        "symbol": "BTC-USDT",
        "channel": "trade",
        "minute_start": T0.isoformat(),
        "flow_state": "S1",
        "messages": 12,
        "anomalies": 0,
        "sequence_breaks": 0,
        "reconnections": 0,
    }
    assert sink.counts == {"data_quality_minute": 1}


def test_the_primary_keys_are_those_of_the_migrations() -> None:
    specs = table_specs(MIGRATION.read_text(encoding="utf-8"))
    for name, spec in specs.items():
        assert PRIMARY_KEYS[name] == spec.primary_key, name
    gaps = (MIGRATION.parent / "0002_mercado.sql").read_text(encoding="utf-8")
    table = gaps.split("CREATE TABLE market.data_gaps (", 1)[1].split(");", 1)[0]
    assert "PRIMARY KEY (market, channel, symbol, start_at)" in table
    assert PRIMARY_KEYS["data_gaps"] == ("market", "channel", "symbol", "start_at")
    assert set(PRIMARY_KEYS) == set(specs) | {"data_gaps"}


def test_a_row_with_the_key_of_another_is_left_out_as_postgresql_does(tmp_path: Path) -> None:
    sink = RowSink(tmp_path)
    query = (
        "INSERT INTO market.ws_candles (market, symbol, open_time, open)"
        " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING"
    )
    sink._record(query, ("SPOT", "BTC-USDT", T0, Decimal(1)))
    sink._record(query, ("SPOT", "BTC-USDT", T0, Decimal(2)))  # kept: the first
    sink._record(query, ("SPOT", "ETH-USDT", T0, Decimal(3)))
    plain = query.removesuffix(" ON CONFLICT DO NOTHING")
    with pytest.raises(ValueError, match="key of another"):
        sink._record(plain, ("SPOT", "ETH-USDT", T0, Decimal(4)))
    sink.close()
    lines = (tmp_path / "ws_candles.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["open"] for line in lines] == ["1", "3"]
    assert sink.counts == {"ws_candles": 2}


def test_a_gap_whose_start_is_taken_keeps_its_tail_as_in_postgresql(tmp_path: Path) -> None:
    """Review GR-06: the recording connection answers what ``store.write_gap`` asks, from
    the gaps written, so the files keep the tails PostgreSQL keeps: starts taken one after
    another, a gap already covered and a gap written twice add nothing."""
    sink = RowSink(tmp_path)

    def gap(start: int, end: int, cause: GapCause = GapCause.SIN_PONG) -> DataGap:
        return DataGap(
            "SPOT",
            "kline",
            "BTC-USDT",
            T0 + timedelta(seconds=start),
            T0 + timedelta(seconds=end),
            cause,
            None,
        )

    late = gap(0, 30, GapCause.PROCESO_DETENIDO)
    for written in (gap(0, 10), gap(10, 20), late, gap(0, 5), late):
        sink.write_tick(TickOutput(gaps=[written]))
    sink.close()
    lines = (tmp_path / "data_gaps.jsonl").read_text(encoding="utf-8").splitlines()

    def iso(seconds: int) -> str:
        return (T0 + timedelta(seconds=seconds)).isoformat()

    assert [(row["start_at"], row["end_at"], row["cause"]) for row in map(json.loads, lines)] == [
        (iso(0), iso(10), "sin_pong"),
        (iso(10), iso(20), "sin_pong"),
        (iso(20), iso(30), "proceso_detenido"),
    ]


def test_the_other_writers_that_ask_still_get_no_rows(tmp_path: Path) -> None:
    """Review GR-06: only the gaps are answered. A late book (``RETURNING 1``) is written
    once, twice given, and its writer is answered with no rows, as before."""
    sink = RowSink(tmp_path)
    late = LateBook(
        "SPOT",
        "BTC-USDT",
        5,
        T0 + timedelta(seconds=10),
        T0 + timedelta(seconds=20),
        7,
        T0 + timedelta(seconds=15),
        T0 + timedelta(seconds=75),
    )
    sink.write_tick(TickOutput(late_books=[late, late]))
    sink.close()
    assert sink.counts["depth_late_books"] == 1


def test_a_folder_used_before_is_refused_untouched(tmp_path: Path) -> None:
    """Review of E3c, E3c-07: the marks of a second run would count from the start of the
    files of the first one."""
    sink = RowSink(tmp_path)
    flow = FlowMinute("SPOT", "BTC-USDT", "trade", T0, FlowState.S1, 12, 0, 0)
    store.write_flow(sink._conn, [flow])  # type: ignore[arg-type]
    sink.close()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(FolderInUse):
        RowSink(tmp_path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
