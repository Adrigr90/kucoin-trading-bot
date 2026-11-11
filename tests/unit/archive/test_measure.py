"""The measurement of the archive on a recording (``tools/measure_archive.py``, stage E3c)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kbot.archive.archiver import TABLES
from kbot.ingest.core import InstrumentSpec, RecorderCore
from kbot.ingest.rows import RowSink
from tests.unit.ingest.messages import feed_minute, pong
from tools import measure_archive
from tools.measure_archive import (
    MIGRATION,
    arrow_table,
    main,
    minute_status_rows,
    read_rows,
    table_specs,
    typed_rows,
    window,
)

T0 = datetime(2026, 11, 10, 23, 58, tzinfo=UTC)
SPECS = table_specs(MIGRATION.read_text(encoding="utf-8"))


def test_the_tables_are_those_of_migration_0003() -> None:
    assert set(SPECS) == set(TABLES)
    late = SPECS["depth_late_books"]
    assert late.primary_key == ("market", "symbol", "depth", "first_slot_start", "sequence")
    assert late.key == "first_slot_start"
    ws = {c.name: c for c in SPECS["ws_candles"].columns}
    assert ws["flow_channel"].default == "trade"
    assert ws["recorded_at"].sql_type == "timestamp with time zone"
    assert ws["recorded_at"].default is None
    books = {c.name: c.sql_type for c in SPECS["depth_snapshots"].columns}
    assert books["bids"] == books["asks"] == "jsonb"


def test_a_row_still_being_written_is_left_out(tmp_path: Path) -> None:
    path = tmp_path / "ws_candles.jsonl"
    path.write_text('{"a": 1}\n{"a": 2}\n{"a": ', encoding="utf-8")
    assert read_rows(path) == [{"a": 1}, {"a": 2}]


def test_rows_get_their_defaults_and_a_recorded_at_and_go_in_key_order() -> None:
    spec = SPECS["ws_candles"]
    row = {
        "market": "SPOT",
        "symbol": "BTC-USDT",
        "open": "100.10",
        "high": "101",
        "low": "99",
        "close": "100.5",
        "volume": "2",
        "turnover": "201",
        "published_at": T0.isoformat(),
        "traded": True,
    }
    later = {**row, "open_time": (T0 + timedelta(minutes=1)).isoformat()}
    first, second = typed_rows(spec, [later, {**row, "open_time": T0.isoformat()}])
    names = [c.name for c in spec.columns]
    values = dict(zip(names, first, strict=True))
    assert values["open_time"] == T0
    assert values["open"] == Decimal("100.10")
    assert values["flow_channel"] == "trade"
    assert T0 + timedelta(seconds=70) <= values["recorded_at"] < T0 + timedelta(seconds=71)
    assert dict(zip(names, second, strict=True))["open_time"] == T0 + timedelta(minutes=1)
    with pytest.raises(ValueError, match="not in 0003"):
        typed_rows(spec, [{**row, "open_time": T0.isoformat(), "extra": 1}])


def test_books_become_typed_lists_of_exact_decimals() -> None:
    spec = SPECS["depth_snapshots"]
    rows = typed_rows(
        spec,
        [
            {
                "market": "SPOT",
                "symbol": "BTC-USDT",
                "depth": 5,
                "minute_start": T0.isoformat(),
                "slot_start": (T0 + timedelta(seconds=50)).isoformat(),
                "sequence": 7,
                "published_at": T0.isoformat(),
                "exchange_ns": 1,
                "received_at": T0.isoformat(),
                "bids": [["73759.6", "0.038"]],
                "asks": [["73759.7", "1.46028203"]],
            }
        ],
    )
    text = arrow_table(spec, rows)
    typed = arrow_table(spec, rows, typed_books=True)
    assert text.column("bids").to_pylist() == ['[["73759.6","0.038"]]']
    assert typed.schema.field("bids").type == pa.list_(measure_archive.BOOK_LEVEL)
    assert typed.column("asks").to_pylist() == [
        [{"price": Decimal("73759.7"), "size": Decimal("1.46028203")}]
    ]


def test_minute_statuses_follow_the_trade_flows() -> None:
    quality = [
        {"market": "SPOT", "symbol": "A", "channel": "trade", "minute_start": "m", "flow_state": s}
        for s in ("S1", "S2")
    ] + [{"market": "SPOT", "symbol": "A", "channel": "obu5", "minute_start": "m"}]
    states = [(r["status"], r["candle_state"]) for r in minute_status_rows(quality)]
    assert states == [("datos", "cerrada_cruzada"), ("datos", "cerrada_rest")]


def test_a_recording_is_measured_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(measure_archive, "RUNS", 1)
    monkeypatch.setattr(measure_archive, "QUERY_DAYS", 1)
    spot = InstrumentSpec("SPOT", "BTC-USDT", Decimal("0.1"), Decimal(1), l50=True)
    futures = InstrumentSpec(
        "FUTURES", "XBTUSDTM", Decimal("0.1"), Decimal("0.001"), l50=True, index_symbol=".KXBT"
    )
    core = RecorderCore((spot, futures), started_at=T0)
    for market in ("SPOT", "FUTURES"):
        core.on_state(market, "connected", T0, "wss://...")
    recording = tmp_path / "recording"
    sink = RowSink(recording)
    for n in range(5):  # across midnight: two UTC days
        minute = T0 + timedelta(minutes=n)
        feed_minute(core, minute, "BTC-USDT", "XBTUSDTM")
        pong(core, minute + timedelta(seconds=62))
        sink.write_tick(core.tick(minute + timedelta(seconds=62)))
    sink.close()
    out = tmp_path / "parquet"
    assert main([str(recording), "--out", str(out)]) == 0
    summary = json.loads((out / "measure.json").read_text(encoding="utf-8"))
    minutes = summary["minutes"]
    assert 3 <= minutes <= 5
    metrics = summary["tables"]["depth_metrics"]
    assert set(metrics["days"]) == {"2026-11-10", "2026-11-11"}
    assert metrics["bytes"]["zstd3"] > 0
    assert summary["tables"]["minute_status"]["rows"] == 2 * minutes  # two trade flows
    snapshots = summary["tables"]["depth_snapshots"]["bytes"]
    assert {"none", "zstd3", "zstd9", "zstd3_typed", "zstd9_typed"} <= set(snapshots)
    written = pq.read_table(out / "zstd3" / "depth_metrics" / "2026-11-11.parquet")
    assert written.num_rows == metrics["days"]["2026-11-11"]
    copies = -(-1440 // minutes)
    assert f"series, 1 days as {copies} repetitions of the same files" in summary["duckdb_ms"]
    with pytest.raises(SystemExit):
        main([str(recording), "--out", str(out)])  # not empty: E3c-05
    printed = capsys.readouterr().out
    assert "Per year (365 days)" in printed
    assert "90 hot days" in printed
    database = summary["postgres_bytes"]
    assert database["day"] == database["heap"] + database["toast"] + database["index"]
    grown = database["data_gaps_year_2"] - database["data_gaps_year_1"]
    assert database["year_2"] - database["year_1"] == pytest.approx(
        database["parquet_year"] + grown
    )


def test_a_recording_stopped_abruptly_ends_at_its_zeros(tmp_path: Path) -> None:
    path = tmp_path / "ws_candles.jsonl"
    path.write_bytes(b'{"a": 1}\n{"a": 2}\n{"a"' + b"\x00" * 64)
    assert read_rows(path) == [{"a": 1}, {"a": 2}]


def _clock(minutes: int, last_rows: int) -> dict[str, list[dict[str, Any]]]:
    """Two channels per minute and two depth channels per slot; the last minute keeps only
    ``last_rows`` rows of each, as a recording stopped inside it."""
    quality, slots = [], []
    for n in range(minutes):
        minute = T0 + timedelta(minutes=n)
        rows = last_rows if n == minutes - 1 else 2
        quality += [{"minute_start": minute.isoformat(), "channel": str(c)} for c in range(rows)]
        for i in range(6):
            slot = (minute + timedelta(seconds=10 * i)).isoformat()
            slots += [{"slot_start": slot, "depth": d} for d in range(rows)]
    return {"data_quality_minute": quality, "depth_slot_status": slots}


def test_the_window_leaves_out_a_minute_cut_short() -> None:
    assert window(_clock(5, 2)) == (T0, T0 + timedelta(minutes=5))
    assert window(_clock(5, 1)) == (T0, T0 + timedelta(minutes=4))


def test_an_old_late_book_does_not_move_the_window() -> None:
    loaded = _clock(5, 1)
    old = (T0 - timedelta(hours=2)).isoformat()
    loaded["depth_late_books"] = [{"first_slot_start": old}]
    assert window(loaded) == (T0, T0 + timedelta(minutes=4))
