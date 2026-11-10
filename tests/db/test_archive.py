"""The archive of a sealed day, in PostgreSQL (02 §3.9, stage E3b).

A day with depth rows, flow minutes and a snapshot is sealed and archived as kbot_archive:
one generation of twelve files, also the empty ones, each read back with the rows and the
fingerprint it was written from. A run that stops before publishing leaves nothing
published, and the next one removes what it left; two archivers never work at once.
"""

from __future__ import annotations

import errno
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
import pytest

from kbot.archive import archiver, parquet

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]
Partitions = Callable[[date, date], None]
SYMBOL = "ARCHIVE-USDT"
EXPECTED_ROWS = {"depth_slot_status": 3, "depth_metrics": 1, "depth_snapshots": 1}
EXPECTED_ROWS["data_quality_minute"] = 2


def midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def sealed_day(
    connect: Connect, partitions: Partitions, day: date, bids: str = '[["100", "2"]]'
) -> None:
    """A closed day (no trade minute, no universe) with rows in four of its tables, sealed."""
    partitions(day, day)
    start = midnight(day)
    with connect("kbot_ingest") as ingest:
        for i in range(3):
            ingest.execute(
                "INSERT INTO market.depth_slot_status (market, symbol, depth, slot_start, state,"
                " sequence, snapshot_age_ms) VALUES ('SPOT', %s, 5, %s, 'valida', %s, 0)",
                (SYMBOL, start + timedelta(seconds=10 * i), i + 1),
            )
        ingest.execute(
            "INSERT INTO market.depth_metrics (market, symbol, depth, slot_start, best_bid,"
            " best_ask, mid, spread, bid_levels, ask_levels, bid_quantity, ask_quantity,"
            " bid_notional, ask_notional, bid_notional_10bps, ask_notional_10bps,"
            " bid_notional_50bps, ask_notional_50bps, bid_deepest_price, ask_deepest_price)"
            " VALUES ('SPOT', %s, 5, %s, 100, 101, 100.5, 1, 1, 1, 2, 3, 200, 303,"
            " 0, 0, 0, 0, 100, 101)",
            (SYMBOL, start),
        )
        ingest.execute(
            "INSERT INTO market.depth_snapshots (market, symbol, depth, minute_start, slot_start,"
            " sequence, published_at, received_at, bids, asks)"
            " VALUES ('SPOT', %s, 5, %s, %s, 1, %s, %s, %s, %s)",
            (
                SYMBOL,
                start,
                start,
                start + timedelta(seconds=1),
                start + timedelta(seconds=2),
                bids,
                '[["101", "3"]]',
            ),
        )
        for minute in range(2):
            ingest.execute(
                "INSERT INTO market.data_quality_minute (market, symbol, channel, minute_start,"
                " flow_state, messages, anomalies, sequence_breaks, reconnections)"
                " VALUES ('SPOT', %s, 'obu5', %s, 'S1', 6, 0, 0, 0)",
                (SYMBOL, start + timedelta(minutes=minute)),
            )
    with connect("kbot_archive") as archive:
        assert archive.execute("SELECT market.seal_day(%s)", (day,)).fetchone() == (12,)


def generations(connect: Connect, day: date) -> list[tuple[object, ...]]:
    with connect("kbot_archive") as archive:
        return archive.execute(
            "SELECT g.generation_id, s.state FROM market.archive_generations g"
            " JOIN market.archive_generation_states s ON s.generation_id = g.generation_id"
            " WHERE g.day = %s",
            (day,),
        ).fetchall()


def test_a_sealed_day_is_archived_and_every_file_holds_its_rows(
    connect: Connect, partitions: Partitions, tmp_path: Path
) -> None:
    day = date(2017, 4, 10)
    sealed_day(connect, partitions, day)
    published = archiver.archive_pending(lambda: connect("kbot_archive"), tmp_path)
    assert published is not None
    ((generation, state),) = generations(connect, day)
    assert generation in published and state == "archivado_local"
    with connect("kbot_archive") as archive:
        files = archive.execute(
            "SELECT table_name, path, sha256, fingerprint, row_count, bytes"
            " FROM market.archive_files WHERE generation_id = %s",
            (generation,),
        ).fetchall()
        assert sorted(name for name, *_ in files) == sorted(archiver.TABLES)
        for name, path, sha256, fingerprint, rows, size in files:
            file = tmp_path / path
            assert rows == EXPECTED_ROWS.get(name, 0), name
            assert (parquet.sha256(file), file.stat().st_size) == (sha256, size), name
            read = parquet.read_fingerprint(file, archiver.describe(archive, name).columns)
            assert (read.rows, read.hexdigest()) == (rows, fingerprint), name
    assert archiver.archive_pending(lambda: connect("kbot_archive"), tmp_path) == []
    assert len(generations(connect, day)) == 1, "an archived day is not archived again"


def test_a_run_that_stops_before_publishing_leaves_nothing_published(
    connect: Connect, partitions: Partitions, tmp_path: Path
) -> None:
    day = date(2017, 5, 10)
    sealed_day(connect, partitions, day)

    def stop() -> None:
        raise RuntimeError("the process stops here")

    with pytest.raises(RuntimeError):
        archiver.archive_day(lambda: connect("kbot_archive"), tmp_path, day, before_publish=stop)
    assert generations(connect, day) == []
    left = list((tmp_path / "market" / day.isoformat()).iterdir())
    assert len(left) == 1 and left[0].name.startswith("g"), "the files, never published"
    published = archiver.archive_pending(lambda: connect("kbot_archive"), tmp_path)
    assert published is not None and len(generations(connect, day)) == 1
    assert not left[0].exists(), "the next run removes what was never published"


def test_two_archivers_never_work_at_once(
    connect: Connect, partitions: Partitions, tmp_path: Path
) -> None:
    day = date(2017, 6, 10)
    sealed_day(connect, partitions, day)
    with connect("kbot_archive") as other:
        other.execute("SELECT pg_advisory_lock(%s)", (archiver.ARCHIVER_LOCK,))
        assert archiver.archive_pending(lambda: connect("kbot_archive"), tmp_path) is None
        assert generations(connect, day) == []
        other.execute("SELECT pg_advisory_unlock(%s)", (archiver.ARCHIVER_LOCK,))
    assert archiver.archive_pending(lambda: connect("kbot_archive"), tmp_path)
    assert len(generations(connect, day)) == 1


def test_the_daily_check_finds_a_changed_or_unsealed_partition(
    connect: Connect, partitions: Partitions, tmp_path: Path, admin: psycopg.Connection
) -> None:
    """02 §3.9: an archived day keeps its seal and the fingerprint of its generation. Only
    an administrator could break either; the check reports it."""
    day = date(2017, 7, 10)
    sealed_day(connect, partitions, day)
    assert archiver.archive_pending(lambda: connect("kbot_archive"), tmp_path)
    assert archiver.verify(lambda: connect("kbot_archive"), [day]) == []
    slots = f"depth_slot_status_p{day:%Y%m%d}"
    admin.execute(f"DELETE FROM market.{slots} WHERE sequence = 3")
    admin.execute(f"ALTER TABLE market.{slots} DROP CONSTRAINT {slots}_sellada")
    problems = archiver.verify(lambda: connect("kbot_archive"), [day])
    assert problems == [
        f"{slots}: it has no seal",
        f"{slots}: 2 rows now, not those of generation {generations(connect, day)[0][0]}",
    ]


def test_a_generation_is_published_only_after_every_sync(
    connect: Connect, partitions: Partitions, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E3b-02: the twelve files, then the directory that holds them, then, after the rename,
    the day, market and the root. An I/O error in any of them stops the publication, and
    the next run removes what was left and publishes."""
    day = date(2017, 8, 10)
    sealed_day(connect, partitions, day)
    synced: list[Path] = []
    real_sync = parquet.sync

    def failing(path: Path) -> None:
        synced.append(path)
        if path.name.startswith(".g"):
            raise OSError(errno.EIO, "input/output error")
        real_sync(path)

    def recording(path: Path) -> None:
        synced.append(path)
        real_sync(path)

    monkeypatch.setattr(parquet, "sync", failing)
    with pytest.raises(OSError):
        archiver.archive_day(lambda: connect("kbot_archive"), tmp_path, day)
    assert generations(connect, day) == []
    monkeypatch.setattr(parquet, "sync", recording)
    synced.clear()
    assert archiver.archive_pending(lambda: connect("kbot_archive"), tmp_path)
    ((generation, _state),) = generations(connect, day)
    day_dir = tmp_path / "market" / day.isoformat()
    assert [p.name for p in synced[:12]] == [f"{name}.parquet" for name in archiver.TABLES]
    assert synced[12].name.startswith(".g") and synced[12].parent == day_dir
    assert synced[13:] == [day_dir, day_dir.parent, tmp_path]
    assert [p.name for p in day_dir.iterdir()] == [f"g{generation}"], "the failed run is gone"


def test_a_json_null_is_archived_as_json_null_and_never_as_a_missing_value(
    connect: Connect, partitions: Partitions, tmp_path: Path
) -> None:
    """E3b-03: 'null'::jsonb is a value, not SQL NULL. It is archived as its JCS text."""
    day = date(2017, 9, 10)
    sealed_day(connect, partitions, day, bids="null")
    assert archiver.archive_pending(lambda: connect("kbot_archive"), tmp_path)
    ((generation, _state),) = generations(connect, day)
    path = tmp_path / "market" / day.isoformat() / f"g{generation}" / "depth_snapshots.parquet"
    assert pq.read_table(path).column("bids").to_pylist() == ["null"]
