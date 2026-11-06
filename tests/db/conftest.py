"""Connections to the ephemeral PostgreSQL 18 of the CI.

The CI runs PostgreSQL on the runner itself, reachable only through the Unix socket with
peer authentication. A CI-only pg_ident.conf map lets the runner user connect as each
project role and as the administrator ``postgres`` (ci/postgres/pg_ident.conf).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import psycopg
import pytest

from kbot.db.roles import DATABASE, SOCKET_DIR


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
