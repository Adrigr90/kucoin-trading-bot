"""``--sink rows`` records the rows that the writers of ``market.store`` send (02 §3.9, E3c).

Here without PostgreSQL; ``tests/db/test_rows_equivalence.py`` compares them, in the CI, with
the rows PostgreSQL stores for the same input.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from psycopg.types.json import Jsonb

from kbot.ingest.rows import RowSink, insert_row
from kbot.market import store
from kbot.market.last_candles import FlowMinute
from kbot.market.mark_candles import FlowState

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
