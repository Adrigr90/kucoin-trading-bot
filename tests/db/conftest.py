"""Connections to the ephemeral PostgreSQL 18 of the CI.

The CI runs PostgreSQL on the runner itself, reachable only through the Unix socket with
peer authentication. A CI-only pg_ident.conf map lets the runner user connect as each
project role and as the administrator ``postgres`` (ci/postgres/pg_ident.conf).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from psycopg import sql

from kbot.db.roles import DATABASE, SOCKET_DIR
from tools.grant_policy import PARTITIONED_TABLES

# Below every date a fixed test writes (2026-11-07 and later), and below them goes on.
FIRST_FREE_DAY = datetime(2026, 1, 1, tzinfo=UTC)

# The twelve market tables partitioned by UTC day (02 §3.9). In production their
# partitions come from market.prepare_partitions(), which only keeps [today - 30, today +
# 14]; the tests write on days of their own, so they create those days' partitions as
# kbot_owner, with the same names and bounds as the function.
PARTITIONED = tuple(name.split(".", 1)[1] for name in PARTITIONED_TABLES)


def create_partitions(admin: psycopg.Connection, first: date, last: date) -> None:
    admin.execute("SET ROLE kbot_owner")
    try:
        day = first
        while day <= last:
            start = datetime(day.year, day.month, day.day, tzinfo=UTC)
            for table in PARTITIONED:
                admin.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS market.{} PARTITION OF market.{}"
                        " FOR VALUES FROM ({}) TO ({})"
                    ).format(
                        sql.Identifier(f"{table}_p{day:%Y%m%d}"),
                        sql.Identifier(table),
                        sql.Literal(start),
                        sql.Literal(start + timedelta(days=1)),
                    )
                )
            day += timedelta(days=1)
    finally:
        admin.execute("RESET ROLE")


@pytest.fixture(scope="session")
def pghost() -> str:
    return os.environ.get("KBOT_PGHOST", SOCKET_DIR)


@pytest.fixture(scope="session")
def connect(pghost: str) -> Callable[..., psycopg.Connection]:
    def open_connection(
        role: str, dbname: str = DATABASE, autocommit: bool = True
    ) -> psycopg.Connection:
        return psycopg.connect(
            host=pghost, dbname=dbname, user=role, autocommit=autocommit, connect_timeout=10
        )

    return open_connection


@pytest.fixture
def admin(connect: Callable[..., psycopg.Connection]) -> Iterator[psycopg.Connection]:
    with connect("postgres") as conn:
        yield conn


Partitions = Callable[[date, date], None]


def drop_partitions(admin: psycopg.Connection, days: list[date]) -> None:
    """Drops, all at once, the twelve partitions of each day that exist."""
    names = [
        sql.SQL("market.{}").format(sql.Identifier(f"{table}_p{day:%Y%m%d}"))
        for day in days
        for table in PARTITIONED
    ]
    if names:
        admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.SQL(", ").join(names)))


@pytest.fixture
def partitions(connect: Callable[..., psycopg.Connection]) -> Iterator[Partitions]:
    """Creates the partitions of the days from ``first`` to ``last``, both included, and
    drops them when the test ends: a query over a partitioned table locks every partition
    it cannot prune, so tests never pile up thousands of them."""
    created: list[date] = []

    def create(first: date, last: date) -> None:
        with connect("postgres") as conn:
            create_partitions(conn, first, last)
        created.extend(first + timedelta(days=n) for n in range((last - first).days + 1))

    yield create
    with connect("postgres") as conn:
        drop_partitions(conn, sorted(set(created) - FIXED_DAYS))


# The days of the tests with fixed dates: T0 (7 November 2026) and the days around it.
FIXED_DAYS = {date(2026, 11, 6) + timedelta(days=n) for n in range(4)}


@pytest.fixture(scope="session", autouse=True)
def fixed_test_days(connect: Callable[..., psycopg.Connection]) -> None:
    with connect("postgres") as conn:
        create_partitions(conn, min(FIXED_DAYS), max(FIXED_DAYS))


@pytest.fixture(scope="session")
def handed_out() -> list[datetime]:
    return []


@pytest.fixture
def free_day(
    connect: Callable[..., psycopg.Connection],
    partitions: Partitions,
    handed_out: list[datetime],
) -> Callable[[], datetime]:
    """A UTC midnight of its own for each run of the recorder (review of E2, sixth round,
    R6-E2-04).

    The universe is read per day for every symbol (``store.load_universe``), and the one in
    force when a day starts is the latest evaluation before it (``universe_members``), so
    runs that shared days, or ran after an earlier one, would see each other's universe.
    Each day handed out lies ten days below every evaluation stored and every day handed
    out before, so no other universe is on it or before it, on a new database or on one
    used before; no randomness, so neither a coincidence nor the order of the tests
    matters. Its partitions exist during the test only."""

    def allocate() -> datetime:
        with connect("kbot_ingest") as conn:
            row = conn.execute("SELECT min(evaluated_at) FROM market.universe_snapshots").fetchone()
        stored = [row[0].astimezone(UTC)] if row is not None and row[0] is not None else []
        lowest = min([*stored, *handed_out, FIRST_FREE_DAY])
        day = datetime(lowest.year, lowest.month, lowest.day, tzinfo=UTC) - timedelta(days=10)
        handed_out.append(day)
        # The recorder may also write the day before (its resume point) and a few after.
        partitions(day.date() - timedelta(days=1), day.date() + timedelta(days=4))
        return day

    return allocate


@pytest.fixture
def recovery_range(connect: Callable[..., psycopg.Connection]) -> Iterator[None]:
    """For a test that calls market.prepare_partitions(): when it ends, drops the
    partitions of [today - 30, today + 14] except the fixed days."""
    yield
    with connect("postgres") as conn:
        row = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()
        assert row is not None
        days = [row[0] + timedelta(days=n) for n in range(-30, 15)]
        drop_partitions(conn, [day for day in days if day not in FIXED_DAYS])
