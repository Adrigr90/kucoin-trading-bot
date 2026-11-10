"""Prepare, seal and archive the days of the twelve partitioned tables (02 §3.9, stage E3b).

It runs as kbot_archive. Each step can be repeated safely:

* **prepare:** ``market.prepare_partitions()`` keeps the partitions of [today - 30, today + 14];
* **seal:** ``market.seal_day(day)`` for each day of [today - 30, today - 3] not sealed yet. A
  day that is not closed, or not 72 h old, is left for a later run;
* **archive:** every sealed day without a published generation gets one. Its twelve files,
  also the empty ones, are read from one REPEATABLE READ snapshot. They are written in a
  temporary directory, synced, read back and compared with the rows (count and canonical
  fingerprint). The directory is then renamed to its generation, and only then is the
  generation published, in one transaction, with its files and the state archivado_local.
  A run that stops before that leaves a directory that no generation names, and the next
  run removes it.

Two archivers never work at once: a run holds a session advisory lock (``ARCHIVER_LOCK``) and
gives up if another holds it. In E3 nothing leaves the server, nothing is left out of the
backups and nothing is retired (02 §3.9, until stage E4).
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import structlog
from psycopg import errors, sql

from kbot.archive import parquet
from kbot.archive.canonical import json_text

# The twelve partitioned tables and the column of their partition key (migration 0003).
TABLES: dict[str, str] = {
    "data_quality_minute": "minute_start",
    "candles": "open_time",
    "ws_candles": "open_time",
    "exhausted_index_rebuilds": "minute_start",
    "minute_status": "minute_start",
    "depth_slot_status": "slot_start",
    "depth_metrics": "slot_start",
    "depth_snapshots": "minute_start",
    "depth_late_books": "first_slot_start",
    "trading_seen_late": "minute_start",
    "late_ws_candles": "open_time",
    "late_ws_repeats": "open_time",
}
SCHEMA_VERSION = "0003"
ARCHIVER_LOCK = 9302
BATCH = 20_000

Connect = Callable[[], psycopg.Connection]

log = structlog.get_logger()


class ArchiveMismatch(Exception):
    """A file read back does not hold the rows it was written from."""


@dataclass(frozen=True)
class Table:
    name: str
    key: str
    columns: tuple[parquet.Column, ...]
    primary_key: tuple[str, ...]


@dataclass(frozen=True)
class ArchivedFile:
    table: str
    path: str  # relative to the root of the archive
    sha256: str
    fingerprint: str
    rows: int
    bytes: int


def prepare(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT market.prepare_partitions()").fetchone()
    return int(row[0]) if row else 0


def seal(conn: psycopg.Connection, today: date) -> list[date]:
    """Seals each day of [today - 30, today - 3] not sealed yet, oldest first, each in its
    own transaction (``conn`` in autocommit). Returns the days sealed now."""
    already = {
        row[0]
        for row in conn.execute(
            "SELECT day FROM market.archive_seals WHERE day >= %s", (today - timedelta(days=30),)
        ).fetchall()
    }
    sealed: list[date] = []
    for offset in range(30, 2, -1):
        day = today - timedelta(days=offset)
        if day in already:
            continue
        try:
            row = conn.execute("SELECT market.seal_day(%s)", (day,)).fetchone()
        except errors.RaiseException as error:
            log.info("day_not_sealed", day=day.isoformat(), reason=error.diag.message_primary)
            continue
        if row and row[0]:
            sealed.append(day)
    return sealed


def describe(conn: psycopg.Connection, name: str) -> Table:
    """Columns, in table order, and primary key of a partitioned table, from the catalog."""
    relation = f"market.{name}"
    columns = conn.execute(
        "SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod)"
        " FROM pg_catalog.pg_attribute a WHERE a.attrelid = %s::regclass"
        " AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
        (relation,),
    ).fetchall()
    key = conn.execute(
        "SELECT a.attname FROM pg_catalog.pg_index i"
        " JOIN pg_catalog.pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
        " WHERE i.indrelid = %s::regclass AND i.indisprimary"
        " ORDER BY array_position(i.indkey::int2[], a.attnum)",
        (relation,),
    ).fetchall()
    return Table(
        name,
        TABLES[name],
        tuple(parquet.Column(column, sql_type) for column, sql_type in columns),
        tuple(row[0] for row in key),
    )


def _batches(conn: psycopg.Connection, table: Table, day: date) -> Iterator[list[Sequence[Any]]]:
    """The rows of the day, in primary key order, with jsonb as its JCS text."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    query = sql.SQL(
        "SELECT {columns} FROM market.{table} WHERE {key} >= %s AND {key} < %s ORDER BY {order}"
    ).format(
        columns=sql.SQL(", ").join(sql.Identifier(c.name) for c in table.columns),
        table=sql.Identifier(table.name),
        key=sql.Identifier(table.key),
        order=sql.SQL(", ").join(sql.Identifier(c) for c in table.primary_key),
    )
    json_columns = {i for i, c in enumerate(table.columns) if c.sql_type == "jsonb"}
    with conn.cursor(name=f"archive_{table.name}") as cursor:
        cursor.execute(query, (start, start + timedelta(days=1)))
        while batch := cursor.fetchmany(BATCH):
            if json_columns:
                batch = [
                    tuple(
                        json_text(v) if i in json_columns and v is not None else v
                        for i, v in enumerate(row)
                    )
                    for row in batch
                ]
            yield batch


def archive_day(
    connect: Connect,
    root: Path,
    day: date,
    *,
    before_publish: Callable[[], None] = lambda: None,
) -> uuid.UUID:
    """Writes, verifies and publishes one generation of a sealed day."""
    generation = uuid.uuid4()
    day_dir = root / "market" / day.isoformat()
    temporary = day_dir / f".g{generation}.tmp"
    temporary.mkdir(parents=True)
    files: list[ArchivedFile] = []
    with connect() as conn:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with conn.transaction():
            moment = conn.execute("SELECT now()").fetchone()
            snapshot_at = moment[0] if moment else None
            for name in TABLES:
                table = describe(conn, name)
                path = temporary / f"{name}.parquet"
                metadata = {
                    "kbot.generation": str(generation),
                    "kbot.day": day.isoformat(),
                    "kbot.table": name,
                    "kbot.format": parquet.FORMAT_VERSION,
                    "kbot.schema": SCHEMA_VERSION,
                }
                written = parquet.write(path, table.columns, _batches(conn, table, day), metadata)
                read = parquet.read_fingerprint(path, table.columns)
                if (read.rows, read.hexdigest()) != (written.rows, written.hexdigest()):
                    raise ArchiveMismatch(f"{day} {name}: the file does not hold its rows")
                files.append(
                    ArchivedFile(
                        name,
                        f"market/{day.isoformat()}/g{generation}/{name}.parquet",
                        parquet.sha256(path),
                        written.hexdigest(),
                        written.rows,
                        path.stat().st_size,
                    )
                )
    final = day_dir / f"g{generation}"
    temporary.rename(final)
    parquet.sync(day_dir)
    before_publish()
    with connect() as conn, conn.transaction():
        conn.execute(
            "INSERT INTO market.archive_generations (generation_id, day, schema_version,"
            " format_version, snapshot_at) VALUES (%s, %s, %s, %s, %s)",
            (generation, day, SCHEMA_VERSION, parquet.FORMAT_VERSION, snapshot_at),
        )
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO market.archive_files (generation_id, day, table_name, path, sha256,"
                " fingerprint, row_count, bytes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (generation, day, f.table, f.path, f.sha256, f.fingerprint, f.rows, f.bytes)
                    for f in files
                ],
            )
        conn.execute(
            "INSERT INTO market.archive_generation_states (generation_id, state)"
            " VALUES (%s, 'archivado_local')",
            (generation,),
        )
    log.info("day_archived", day=day.isoformat(), generation=str(generation))
    return generation


def remove_orphans(conn: psycopg.Connection, root: Path) -> list[Path]:
    """Removes the directories of generations that were never published: what a run that
    stopped before publishing left behind. Called only while holding ``ARCHIVER_LOCK``."""
    published = {
        str(row[0]) for row in conn.execute("SELECT generation_id FROM market.archive_generations")
    }
    removed: list[Path] = []
    base = root / "market"
    if not base.is_dir():
        return removed
    for day_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for entry in sorted(day_dir.iterdir()):
            name = entry.name
            if name.startswith(".g") and name.endswith(".tmp"):
                generation = name[2:-4]
            elif name.startswith("g"):
                generation = name[1:]
            else:
                continue
            if generation not in published:
                shutil.rmtree(entry)
                removed.append(entry)
                log.warning("unpublished_generation_removed", path=str(entry))
    return removed


def archive_pending(connect: Connect, root: Path) -> list[uuid.UUID] | None:
    """Archives every sealed day without a generation. None if another archiver runs."""
    with connect() as lock:
        row = lock.execute("SELECT pg_try_advisory_lock(%s)", (ARCHIVER_LOCK,)).fetchone()
        if not row or not row[0]:
            log.info("archiver_busy")
            return None
        try:
            remove_orphans(lock, root)
            days = [
                row[0]
                for row in lock.execute(
                    "SELECT s.day FROM market.archive_seals s WHERE NOT EXISTS ("
                    "   SELECT 1 FROM market.archive_generations g WHERE g.day = s.day)"
                    " AND NOT EXISTS ("
                    "   SELECT 1 FROM market.archive_retirements r WHERE r.day = s.day)"
                    " ORDER BY s.day"
                ).fetchall()
            ]
            return [archive_day(connect, root, day) for day in days]
        finally:
            lock.execute("SELECT pg_advisory_unlock(%s)", (ARCHIVER_LOCK,))
