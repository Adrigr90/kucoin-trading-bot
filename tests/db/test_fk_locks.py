"""Acceptance test A9 (verification B18): foreign keys take FOR KEY SHARE locks.

Two simultaneous sessions in a scratch database:
(1) parent and child tables with a foreign key, and one parent row;
(2) session 1 inserts a child row and does not commit;
(3) session 2 takes FOR NO KEY UPDATE on the parent row without blocking;
(4) session 2 tries FOR UPDATE and is blocked while session 1 neither commits nor rolls back.
NOWAIT makes a blocked lock fail at once, so the CI never waits.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]

SCRATCH = "kbot_scratch_a9"


@pytest.fixture
def scratch_db(admin: psycopg.Connection, connect: Connect) -> Iterator[str]:
    admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {SCRATCH}")
    try:
        with connect("postgres", dbname=SCRATCH) as setup:
            setup.execute("CREATE TABLE parent (id integer PRIMARY KEY, note text)")
            setup.execute(
                "CREATE TABLE child (id integer PRIMARY KEY,"
                " parent_id integer NOT NULL REFERENCES parent (id))"
            )
            setup.execute("INSERT INTO parent VALUES (1, 'p')")
        yield SCRATCH
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")


def test_a9_foreign_key_takes_for_key_share(connect: Connect, scratch_db: str) -> None:
    with (
        connect("postgres", dbname=scratch_db, autocommit=False) as session1,
        connect("postgres", dbname=scratch_db, autocommit=False) as session2,
    ):
        session1.execute("INSERT INTO child VALUES (1, 1)")

        session2.execute("SELECT id FROM parent WHERE id = 1 FOR NO KEY UPDATE NOWAIT")
        session2.rollback()

        with pytest.raises(errors.LockNotAvailable):
            session2.execute("SELECT id FROM parent WHERE id = 1 FOR UPDATE NOWAIT")
        session2.rollback()

        session1.rollback()
        session2.execute("SELECT id FROM parent WHERE id = 1 FOR UPDATE NOWAIT")
        session2.rollback()
