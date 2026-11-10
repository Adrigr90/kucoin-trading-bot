"""Run the archive: ``python -m kbot.archive prepare|seal|archive`` (02 §3.9).

On the server (E4) a timer runs it as kbot_archive over the Unix socket with peer
authentication; ``KBOT_ARCHIVE_DSN`` and ``--root`` change where it connects and writes.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from kbot.archive import archiver
from kbot.db.roles import ARCHIVE, DATABASE, SOCKET_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description="Historical archive of the market data")
    parser.add_argument("step", choices=("prepare", "seal", "archive", "verify"))
    parser.add_argument("--root", type=Path, default=Path("/var/lib/kbot/archive"))
    arguments = parser.parse_args()
    dsn = os.environ.get("KBOT_ARCHIVE_DSN", f"host={SOCKET_DIR} dbname={DATABASE} user={ARCHIVE}")

    def connect() -> psycopg.Connection:
        return psycopg.connect(dsn, autocommit=True)

    if arguments.step == "prepare":
        with connect() as conn:
            print(f"partitions created: {archiver.prepare(conn)}")
    elif arguments.step == "seal":
        with connect() as conn:
            sealed = archiver.seal(conn, datetime.now(UTC).date())
        print("days sealed:", ", ".join(day.isoformat() for day in sealed) or "none")
    elif arguments.step == "verify":
        problems = archiver.verify(connect)
        for problem in problems:
            print("PROBLEM", problem)
        print(f"archived days checked: {'problems found' if problems else 'all match'}")
        return 1 if problems else 0
    else:
        generations = archiver.archive_pending(connect, arguments.root)
        if generations is None:
            print("another archiver is running")
            return 1
        print(f"generations published: {len(generations)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
