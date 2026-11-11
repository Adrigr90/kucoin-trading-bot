"""``--sink rows``: the rows PostgreSQL would receive, written to files (02 §3.9, stage E3c).

A PC without PostgreSQL cannot measure the archive with the real tables, and ``--sink file``
writes only a summary. This sink runs the very writers of ``market.store`` that
``DatabaseSink`` runs, against a connection that executes nothing: it records each INSERT
with its table, its columns and its values, and appends the row as a JSON line to
``<folder>/<table>.jsonl``. So the rows are those that would reach PostgreSQL, built by the
same code; a test in the CI compares them with the rows PostgreSQL stores. A row with the key
of an earlier one is left out, as PostgreSQL leaves it out (review of E3c, E3c-04): the sink
keeps a 16-byte digest of every key it wrote, some 150 MB for a day of the reference universe.

After each write it flushes every file and then appends a progress mark to ``PROGRESS``:
the rows each file held at that moment. Each file has its own buffer, so a recording that
stops abruptly may keep more of one file than of another; the measurement only uses the
rows of the latest mark that every file reaches (review of E3c, E3c-01).

Decisions that need the database stay those of ``MemorySink``, and their rows are not
written here: the late evidence of the minutes (a few rows of ``trading_seen_late``,
``late_ws_candles`` and ``late_ws_repeats``) and the close of a day, computed in SQL from what
the database holds: ``minute_status``, and the S3 rows of ``data_quality_minute`` that
``store.fill_missing_flow`` adds for the minutes of the day without any. They are few and
small, and a recording without interruptions has none of the latter.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO

from psycopg.types.json import Jsonb

from kbot.ingest.core import TickOutput
from kbot.ingest.sinks import MemorySink
from kbot.market import store
from kbot.market.last_candles import CandleDecision, KeptEvidence
from kbot.market.mark_candles import IndexMinute, OwnCandle

# The primary key of every table the writers insert into (migrations 0002 and 0003; a test
# compares them). A row with the key of an earlier one is what PostgreSQL rejects: with
# ON CONFLICT DO NOTHING it keeps the first, and without it the statement fails.
PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "data_quality_minute": ("market", "symbol", "channel", "minute_start"),
    "candles": ("series", "market", "symbol", "open_time"),
    "ws_candles": ("market", "symbol", "open_time"),
    "exhausted_index_rebuilds": ("market", "symbol", "minute_start"),
    "minute_status": ("market", "symbol", "minute_start"),
    "depth_slot_status": ("market", "symbol", "depth", "slot_start"),
    "depth_metrics": ("market", "symbol", "depth", "slot_start"),
    "depth_snapshots": ("market", "symbol", "depth", "minute_start"),
    "depth_late_books": ("market", "symbol", "depth", "first_slot_start", "sequence"),
    "trading_seen_late": ("market", "symbol", "minute_start"),
    "late_ws_candles": ("market", "symbol", "open_time", "published_at", "latest"),
    "late_ws_repeats": ("market", "symbol", "open_time", "published_at"),
    "data_gaps": ("market", "channel", "symbol", "start_at"),
}

PROGRESS = "_progress.jsonl"

_INSERT = re.compile(
    r"^INSERT INTO market\.(\w+) \(([^)]*)\) VALUES \((.*?)\)(?: ON CONFLICT .*)?$", re.S
)


class _NoRows:
    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []


class RecordingConnection:
    """Stands for a psycopg connection: records every statement it is given and answers
    every query with no rows. Transactions and cursors are only names here."""

    def __init__(self, record: Callable[[str, tuple[Any, ...]], None]) -> None:
        self._record = record

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    @contextmanager
    def cursor(self) -> Iterator[RecordingConnection]:
        yield self

    def execute(self, query: object, params: Iterable[Any] = ()) -> _NoRows:
        self._record(str(query), tuple(params))
        return _NoRows()

    def executemany(self, query: object, params_seq: Iterable[Iterable[Any]]) -> None:
        for params in params_seq:
            self._record(str(query), tuple(params))


def _literal(item: str) -> Any:
    if item == "NULL":
        return None
    if item.startswith("'") and item.endswith("'"):
        return item[1:-1]
    return int(item)


def _plain(value: Any) -> Any:
    """A value as JSON keeps it exactly: decimals and moments as text, jsonb as its JSON."""
    if isinstance(value, Jsonb):
        return value.obj
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def insert_row(query: str, params: tuple[Any, ...]) -> tuple[str, dict[str, Any]] | None:
    """The table and the row of an INSERT of ``market.store``, or None for any other
    statement. A literal of the VALUES list (``0``, ``'trade'``, ``NULL``) is its value."""
    match = _INSERT.match(" ".join(query.split()))
    if match is None:
        return None
    table, columns, values = match.groups()
    names = [name.strip() for name in columns.split(",")]
    items = [item.strip() for item in values.split(",")]
    given = iter(params)
    row = {
        name: _plain(next(given)) if item == "%s" else _literal(item)
        for name, item in zip(names, items, strict=True)
    }
    if next(given, None) is not None:
        raise ValueError(f"{table}: more values than columns")
    return table, row


class RowSink(MemorySink):
    """MemorySink, plus the rows PostgreSQL would receive in ``<folder>/<table>.jsonl``."""

    def __init__(self, folder: Path) -> None:
        super().__init__()
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.counts: Counter[str] = Counter()
        self._files: dict[str, TextIO] = {}
        self._keys: dict[str, set[bytes]] = {}  # digests of the keys written, per table
        self._conn = RecordingConnection(self._record)
        self._progress = (folder / PROGRESS).open("a", encoding="utf-8", newline="\n")

    def _record(self, query: str, params: tuple[Any, ...]) -> None:
        found = insert_row(query, params)
        if found is None:
            return  # a read, answered with no rows: nothing is stored yet
        table, row = found
        if table not in PRIMARY_KEYS:
            raise ValueError(f"{table}: no primary key known to --sink rows")
        key = hashlib.blake2b(
            json.dumps([row[c] for c in PRIMARY_KEYS[table]]).encode(), digest_size=16
        ).digest()
        seen = self._keys.setdefault(table, set())
        if key in seen:
            # The other unique constraints of these tables contain their primary key, so the
            # primary key alone decides what PostgreSQL rejects (review of E3c, E3c-04).
            if "ON CONFLICT DO NOTHING" in " ".join(query.split()):
                return
            raise ValueError(f"{table}: a second row with the key of another")
        seen.add(key)
        file = self._files.get(table)
        if file is None:
            file = (self.folder / f"{table}.jsonl").open("a", encoding="utf-8", newline="\n")
            self._files[table] = file
        file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.counts[table] += 1

    def _mark(self) -> None:
        """Every row written so far is in the files: flushed first, then the mark."""
        for file in self._files.values():
            file.flush()
        self._progress.write(json.dumps(dict(sorted(self.counts.items()))) + "\n")
        self._progress.flush()

    def write_tick(self, output: TickOutput) -> list[KeptEvidence]:
        conn: Any = self._conn
        store.write_flow(conn, output.flows)
        store.write_ws_candles(conn, output.ws_candles)
        store.write_own_candles(conn, "FUTURES", output.own_candles)
        store.write_depth(conn, output.slots, output.minute_snapshots, output.gaps)
        store.write_late_books(conn, output.late_books)
        self._mark()
        return super().write_tick(output)

    def write_last_candles(self, decisions: list[CandleDecision]) -> None:
        store.write_last_candles(self._conn, decisions)  # type: ignore[arg-type]
        self._mark()
        super().write_last_candles(decisions)

    def write_index_candles(self, candles: list[OwnCandle]) -> None:
        store.write_own_candles(self._conn, "FUTURES", candles)  # type: ignore[arg-type]
        self._mark()
        super().write_index_candles(candles)

    def write_exhausted_index(self, minutes: list[tuple[IndexMinute, int]]) -> None:
        store.write_exhausted_index(self._conn, minutes)  # type: ignore[arg-type]
        self._mark()
        super().write_exhausted_index(minutes)

    def close(self) -> None:
        for file in self._files.values():
            file.close()
        self._files.clear()
        self._progress.close()
