"""Connections to the ephemeral PostgreSQL 18 of the CI.

The CI runs PostgreSQL on the runner itself, reachable only through the Unix socket with
peer authentication. A CI-only pg_ident.conf map lets the runner user connect as each
project role and as the administrator ``postgres`` (ci/postgres/pg_ident.conf).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from kbot.db.roles import DATABASE, SOCKET_DIR

# Below every date a fixed test writes (2026-11-07 and later), and below them goes on.
FIRST_FREE_DAY = datetime(2026, 1, 1, tzinfo=UTC)


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


@pytest.fixture(scope="session")
def free_day(connect: Callable[..., psycopg.Connection]) -> Callable[[], datetime]:
    """A UTC midnight of its own for each run of the recorder (review of E2, sixth round,
    R6-E2-04).

    The universe is read per day for every symbol (``store.load_universe``), and the one in
    force when a day starts is the latest evaluation before it (``universe_members``), so
    runs that shared days, or ran after an earlier one, would see each other's universe.
    Each day handed out lies ten days below every evaluation stored and every day handed
    out before, so no other universe is on it or before it, on a new database or on one
    used before; no randomness, so neither a coincidence nor the order of the tests
    matters."""
    handed_out: list[datetime] = []

    def allocate() -> datetime:
        with connect("kbot_ingest") as conn:
            row = conn.execute("SELECT min(evaluated_at) FROM market.universe_snapshots").fetchone()
        stored = [row[0].astimezone(UTC)] if row is not None and row[0] is not None else []
        lowest = min([*stored, *handed_out, FIRST_FREE_DAY])
        day = datetime(lowest.year, lowest.month, lowest.day, tzinfo=UTC) - timedelta(days=10)
        handed_out.append(day)
        return day

    return allocate
