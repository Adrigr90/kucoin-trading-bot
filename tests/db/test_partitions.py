"""Partitions by UTC day and their SECURITY DEFINER functions (02 §3.9, stage E3).

Migration 0003 rebuilds twelve market tables partitioned by UTC day. kbot_archive prepares
and seals partitions only through market.prepare_partitions() and market.seal_day(date);
market.retire_day(date) exists, but nobody may execute it until stage E4 passes a restore
test, so here only the administrator calls it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg import errors, sql

from tools.grant_policy import PARTITION_FUNCTIONS

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]
Partitions = Callable[[date, date], None]

KEYS = {
    "data_quality_minute": "minute_start",
    "candles": "open_time",
    "ws_candles": "open_time",
    "exhausted_index_rebuilds": "minute_start",
    "minute_status": "minute_start",
    "depth_slot_status": "slot_start",
    "depth_metrics": "slot_start",
    "depth_snapshots": "minute_start",
    "depth_late_books": "slot_start",
    "trading_seen_late": "minute_start",
    "late_ws_candles": "open_time",
    "late_ws_repeats": "open_time",
}
VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
SYMBOL = "ARCH-USDT"


def partition(table: str, day: date) -> str:
    return f"{table}_p{day:%Y%m%d}"


def exists(conn: psycopg.Connection, relation: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"market.{relation}",)).fetchone()
    return bool(row and row[0])


def utc_today(conn: psycopg.Connection) -> date:
    row = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()
    assert row is not None
    return row[0]


def slot(conn: psycopg.Connection, at: datetime, symbol: str = SYMBOL) -> None:
    conn.execute(
        "INSERT INTO market.depth_slot_status (market, symbol, depth, slot_start, state)"
        " VALUES ('SPOT', %s, 5, %s, 'no_recibida')",
        (symbol, at),
    )


def midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def archive_day(connect: Connect, day: date, wrong: dict[str, int] | None = None) -> None:
    """Seal ``day`` and publish a generation eligible to leave the backups, whose files
    hold the rows of each partition, or those of ``wrong`` where given."""
    generation = uuid.uuid4()
    with connect("kbot_archive") as archive:
        archive.execute("SELECT market.seal_day(%s)", (day,))
        archive.execute(
            "INSERT INTO market.archive_generations (generation_id, day, schema_version,"
            " format_version, snapshot_at) VALUES (%s, %s, '0003', '1', now())",
            (generation, day),
        )
        for table, key in KEYS.items():
            counted = archive.execute(
                sql.SQL("SELECT count(*) FROM market.{} WHERE {} >= %s AND {} < %s").format(
                    sql.Identifier(table), sql.Identifier(key), sql.Identifier(key)
                ),
                (midnight(day), midnight(day) + timedelta(days=1)),
            ).fetchone()
            rows = (wrong or {}).get(table, counted[0] if counted else 0)
            archive.execute(
                "INSERT INTO market.archive_files (generation_id, day, table_name, path,"
                " sha256, fingerprint, row_count, bytes) VALUES (%s, %s, %s, %s, %s, %s, %s, 1)",
                (
                    generation,
                    day,
                    table,
                    f"market/{day}/g/{table}.parquet",
                    "0" * 64,
                    "0" * 64,
                    rows,
                ),
            )
        archive.execute(
            "INSERT INTO market.archive_generation_states (generation_id, state)"
            " VALUES (%s, 'elegible_para_exclusion_backup')",
            (generation,),
        )


def test_the_twelve_tables_are_partitioned_by_range_of_their_time(
    admin: psycopg.Connection,
) -> None:
    rows = admin.execute(
        "SELECT c.relname, pg_get_partkeydef(c.oid) FROM pg_partitioned_table p"
        " JOIN pg_class c ON c.oid = p.partrelid"
        " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'market'"
    ).fetchall()
    assert dict(rows) == {table: f"RANGE ({key})" for table, key in KEYS.items()}


def test_prepare_keeps_the_recovery_range_in_utc_from_any_session(
    connect: Connect, admin: psycopg.Connection
) -> None:
    """[today - 30, today + 14] in UTC, owned by kbot_owner, the same from sessions in
    Madrid and New York; a second call creates nothing (review of the E3 design)."""
    with connect("kbot_archive") as archive:
        archive.execute("SELECT market.prepare_partitions()")
        for zone in ("Europe/Madrid", "America/New_York"):
            archive.execute(sql.SQL("SET TimeZone = {}").format(sql.Literal(zone)))
            assert archive.execute("SELECT market.prepare_partitions()").fetchone() == (0,)
    admin.execute("SET TimeZone = 'UTC'")
    today = utc_today(admin)
    expected = {}
    for offset in range(-30, 15):
        day = today + timedelta(days=offset)
        bounds = (
            f"FOR VALUES FROM ('{day:%Y-%m-%d} 00:00:00+00')"
            f" TO ('{day + timedelta(days=1):%Y-%m-%d} 00:00:00+00')"
        )
        for table in KEYS:
            expected[partition(table, day)] = (table, bounds, "kbot_owner")
    rows = admin.execute(
        "SELECT c.relname, p.relname, pg_get_expr(c.relpartbound, c.oid),"
        " pg_get_userbyid(c.relowner) FROM pg_inherits i"
        " JOIN pg_class c ON c.oid = i.inhrelid JOIN pg_class p ON p.oid = i.inhparent"
        " WHERE c.relname = ANY(%s)",
        (list(expected),),
    ).fetchall()
    assert {child: (parent, bounds, owner) for child, parent, bounds, owner in rows} == expected


def test_a_table_in_the_place_of_a_partition_stops_prepare(
    connect: Connect, admin: psycopg.Connection
) -> None:
    """The catalog check: a relation with an expected name that is not that day's partition
    of its table stops the function instead of being taken for it."""
    with connect("kbot_archive") as archive:
        archive.execute("SELECT market.prepare_partitions()")
    admin.execute("SET TimeZone = 'UTC'")
    last = partition("candles", utc_today(admin) + timedelta(days=14))
    admin.execute(
        sql.SQL("ALTER TABLE market.candles DETACH PARTITION market.{}").format(
            sql.Identifier(last)
        )
    )
    try:
        with (
            connect("kbot_archive") as archive,
            pytest.raises(errors.RaiseException, match="is not the partition"),
        ):
            archive.execute("SELECT market.prepare_partitions()")
    finally:
        admin.execute(sql.SQL("DROP TABLE market.{}").format(sql.Identifier(last)))
    with connect("kbot_archive") as archive:
        assert archive.execute("SELECT market.prepare_partitions()").fetchone() == (1,)


def test_only_kbot_archive_prepares_and_seals_and_nobody_retires(
    connect: Connect, admin: psycopg.Connection
) -> None:
    def allowed(role: str) -> list[bool]:
        return [
            admin.execute(
                "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, function)
            ).fetchone()[0]
            for function in PARTITION_FUNCTIONS
        ]

    assert allowed("kbot_archive") == [True, True, False]
    for role in ("kbot_ingest", "kbot_backup", "kbot_web", "kbot_fn_owner"):
        assert allowed(role) == [False, False, False], role
    public = admin.execute(
        "SELECT count(*) FROM pg_proc p, aclexplode(p.proacl) a"
        " WHERE p.oid = ANY(%s::regprocedure[]) AND a.grantee = 0",
        (list(PARTITION_FUNCTIONS),),
    ).fetchone()
    assert public == (0,)
    with connect("kbot_archive") as archive, pytest.raises(errors.InsufficientPrivilege):
        archive.execute("SELECT market.retire_day('2000-01-01')")
    with connect("kbot_ingest") as ingest, pytest.raises(errors.InsufficientPrivilege):
        ingest.execute("SELECT market.prepare_partitions()")


def test_a_sealed_day_keeps_its_rows_and_takes_no_new_one(
    connect: Connect, partitions: Partitions
) -> None:
    day = date(2020, 1, 15)
    partitions(day, day)
    with connect("kbot_ingest") as ingest:
        slot(ingest, midnight(day) + timedelta(seconds=10))
    with connect("kbot_archive") as archive:
        assert archive.execute("SELECT market.seal_day(%s)", (day,)).fetchone() == (12,)
        assert archive.execute("SELECT market.seal_day(%s)", (day,)).fetchone() == (0,)
        sealed = archive.execute("SELECT count(*) FROM market.archive_seals WHERE day = %s", (day,))
        assert sealed.fetchone() == (1,)
    with connect("kbot_ingest") as ingest:
        with pytest.raises(errors.CheckViolation):
            slot(ingest, midnight(day) + timedelta(seconds=20))
        kept = ingest.execute(
            "SELECT count(*) FROM market.depth_slot_status WHERE symbol = %s AND slot_start >= %s"
            " AND slot_start < %s",
            (SYMBOL, midnight(day), midnight(day) + timedelta(days=1)),
        )
        assert kept.fetchone() == (1,)


def test_a_day_is_sealed_only_72_hours_after_its_end_and_once_closed(
    connect: Connect, partitions: Partitions, admin: psycopg.Connection
) -> None:
    recent = utc_today(admin) - timedelta(days=3)
    with connect("kbot_archive") as archive:
        with pytest.raises(errors.RaiseException, match="72 h"):
            archive.execute("SELECT market.seal_day(%s)", (recent,))
        with pytest.raises(errors.RaiseException):
            archive.execute("SELECT market.seal_day(NULL)")
    open_day = date(2020, 2, 15)
    partitions(open_day, open_day)
    with connect("kbot_ingest") as ingest:
        ingest.execute(
            "INSERT INTO market.data_quality_minute (market, symbol, channel, minute_start,"
            " flow_state, messages, anomalies, sequence_breaks, reconnections)"
            " VALUES ('SPOT', 'OPEN-USDT', 'trade', %s, 'S1', 1, 0, 0, 0)",
            (midnight(open_day) + timedelta(hours=12),),
        )
    with (
        connect("kbot_archive") as archive,
        pytest.raises(errors.RaiseException, match="not closed"),
    ):
        archive.execute("SELECT market.seal_day(%s)", (open_day,))


def test_retire_drops_an_archived_old_day_once(
    connect: Connect, partitions: Partitions, admin: psycopg.Connection
) -> None:
    day = date(2019, 6, 15)
    partitions(day, day)
    with connect("kbot_ingest") as ingest:
        slot(ingest, midnight(day) + timedelta(seconds=10))
    archive_day(connect, day)
    assert admin.execute("SELECT market.retire_day(%s)", (day,)).fetchone() == (12,)
    assert not any(exists(admin, partition(table, day)) for table in KEYS)
    retired = admin.execute(
        "SELECT count(*) FROM market.archive_retirements WHERE day = %s", (day,)
    )
    assert retired.fetchone() == (1,)
    assert admin.execute("SELECT market.retire_day(%s)", (day,)).fetchone() == (0,)


def test_retire_refuses_and_changes_nothing(
    connect: Connect, partitions: Partitions, admin: psycopg.Connection
) -> None:
    """A recent day, a day without an eligible generation and a file with other rows than
    its partition: refused, and every partition stays."""
    with pytest.raises(errors.RaiseException, match="90 days"):
        admin.execute("SELECT market.retire_day(%s)", (utc_today(admin) - timedelta(days=10),))
    unpublished = date(2019, 9, 15)
    partitions(unpublished, unpublished)
    with connect("kbot_archive") as archive:
        archive.execute("SELECT market.seal_day(%s)", (unpublished,))
    with pytest.raises(errors.RaiseException, match="no generation"):
        admin.execute("SELECT market.retire_day(%s)", (unpublished,))
    mismatch = date(2019, 7, 15)
    partitions(mismatch, mismatch)
    with connect("kbot_ingest") as ingest:
        slot(ingest, midnight(mismatch) + timedelta(seconds=10))
    archive_day(connect, mismatch, wrong={"depth_slot_status": 0})
    with pytest.raises(errors.RaiseException, match="holds"):
        admin.execute("SELECT market.retire_day(%s)", (mismatch,))
    for day in (unpublished, mismatch):
        assert all(exists(admin, partition(table, day)) for table in KEYS), day


def test_a_reference_from_the_day_before_stops_the_retirement(
    connect: Connect, partitions: Partitions, admin: psycopg.Connection
) -> None:
    """A late book of the 15th whose range reaches the first slot of the 16th: retiring the
    16th first fails and changes nothing; in chronological order both go (review of E3)."""
    first, second = date(2019, 8, 15), date(2019, 8, 16)
    partitions(first, second)
    before, after = midnight(second) - timedelta(seconds=10), midnight(second)
    with connect("kbot_ingest") as ingest:
        slot(ingest, before)
        slot(ingest, after)
        ingest.execute(
            "INSERT INTO market.depth_late_books (market, symbol, depth, slot_start,"
            " last_slot_start, sequence, published_at, received_at)"
            " VALUES ('SPOT', %s, 5, %s, %s, 1, %s, %s)",
            (SYMBOL, before, after, before + timedelta(seconds=1), after + timedelta(seconds=5)),
        )
    archive_day(connect, first)
    archive_day(connect, second)
    with pytest.raises(psycopg.Error):
        admin.execute("SELECT market.retire_day(%s)", (second,))
    assert all(exists(admin, partition(table, second)) for table in KEYS)
    assert admin.execute("SELECT market.retire_day(%s)", (first,)).fetchone() == (12,)
    assert admin.execute("SELECT market.retire_day(%s)", (second,)).fetchone() == (12,)


def test_minutes_are_utc_minutes_and_a_snapshot_stays_in_its_minute(connect: Connect) -> None:
    t0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    with connect("kbot_ingest") as ingest:
        with pytest.raises(errors.CheckViolation):
            ingest.execute(
                "INSERT INTO market.data_quality_minute (market, symbol, channel, minute_start,"
                " flow_state, messages, anomalies, sequence_breaks, reconnections)"
                " VALUES ('SPOT', 'ALIGN-USDT', 'trade', %s, 'S1', 0, 0, 0, 0)",
                (t0 + timedelta(seconds=30),),
            )
        late_slot = t0 + timedelta(seconds=60)
        ingest.execute(
            "INSERT INTO market.depth_slot_status (market, symbol, depth, slot_start, state,"
            " sequence, snapshot_age_ms) VALUES ('SPOT', 'ALIGN-USDT', 5, %s, 'valida', 1, 0)",
            (late_slot,),
        )
        with pytest.raises(errors.CheckViolation):
            ingest.execute(
                "INSERT INTO market.depth_snapshots (market, symbol, depth, minute_start,"
                " slot_start, sequence, published_at, received_at, bids, asks)"
                " VALUES ('SPOT', 'ALIGN-USDT', 5, %s, %s, 1, %s, %s, '[]', '[]')",
                (t0, late_slot, late_slot, late_slot),
            )


def test_a_row_without_its_partition_fails(connect: Connect) -> None:
    """No default partition: a row of a day that was never prepared fails visibly."""
    with connect("kbot_ingest") as ingest, pytest.raises(errors.CheckViolation):
        slot(ingest, datetime(1990, 1, 1, tzinfo=UTC))


# --- migration 0003 on a database of its own ---------------------------------------------


@pytest.fixture
def scratch(connect: Connect, admin: psycopg.Connection) -> Iterator[str]:
    """A new database with migrations 0001 and 0002 applied as kbot_owner."""
    database = "kbot_rebuild_" + uuid.uuid4().hex[:8]
    admin.execute(sql.SQL("CREATE DATABASE {} OWNER kbot_owner").format(sql.Identifier(database)))
    try:
        with connect("postgres", dbname=database, autocommit=False) as conn:
            conn.execute("SET ROLE kbot_owner")
            for migration in ("0001_esquemas_y_limites.sql", "0002_mercado.sql"):
                conn.execute((VERSIONS / migration).read_text(encoding="utf-8"))
            conn.commit()
        yield database
    finally:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))


def rebuild() -> str:
    return (VERSIONS / "0003_particiones.sql").read_text(encoding="utf-8")


def partitioned_tables(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT count(*) FROM pg_partitioned_table").fetchone()
    return row[0] if row else -1


def test_0003_refuses_tables_with_data_and_changes_nothing(connect: Connect, scratch: str) -> None:
    with connect("postgres", dbname=scratch, autocommit=False) as conn:
        conn.execute("SET ROLE kbot_owner")
        slot(conn, datetime(2026, 11, 7, 12, tzinfo=UTC))
        conn.commit()
        with pytest.raises(errors.CheckViolation):
            conn.execute(rebuild())
        conn.rollback()
        assert conn.execute("SELECT count(*) FROM market.depth_slot_status").fetchone() == (1,)
        assert partitioned_tables(conn) == 0


def test_0003_gives_up_if_a_table_is_in_use_and_changes_nothing(
    connect: Connect, scratch: str
) -> None:
    with connect("postgres", dbname=scratch, autocommit=False) as holder:
        holder.execute("SET ROLE kbot_owner")
        holder.execute("LOCK TABLE market.candles IN ACCESS SHARE MODE")
        with connect("postgres", dbname=scratch, autocommit=False) as conn:
            conn.execute("SET ROLE kbot_owner")
            with pytest.raises(errors.LockNotAvailable):
                conn.execute(rebuild())
            conn.rollback()
            assert partitioned_tables(conn) == 0
        holder.rollback()


def test_0003_on_empty_tables_partitions_the_twelve(connect: Connect, scratch: str) -> None:
    """The sequence 0001 -> 0002 -> 0003 on a new database, as the server will apply it."""
    with connect("postgres", dbname=scratch, autocommit=False) as conn:
        conn.execute("SET ROLE kbot_owner")
        conn.execute(rebuild())
        conn.commit()
        assert partitioned_tables(conn) == 12
