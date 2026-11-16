"""Apply ``sql/roles.sql`` so that an interrupted run can be repeated (review of E4b, E4b-03).

``roles.sql`` runs statement by statement in autocommit (``CREATE DATABASE`` cannot run
inside a transaction), so a run cut short leaves some roles created and others not. This
applies each statement on its own: a ``CREATE ROLE`` or ``CREATE DATABASE`` whose object
already exists is skipped, and every other statement of the file (``ALTER ROLE ... SET``,
``GRANT``, ``REVOKE``) is idempotent and runs again. Nothing is ever dropped. The result is
then checked by ``kbot.ops.pgcheck``, which is what decides whether the state is the
approved one.

    sudo -u postgres python -m kbot.ops.bootstrap /opt/kbot/sql/roles.sql
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg

from kbot.db.roles import SOCKET_DIR

_CREATE = re.compile(r"^CREATE\s+(ROLE|DATABASE)\s+([a-z_][a-z0-9_]*)\b", re.IGNORECASE)
_EXISTS = {
    "ROLE": "SELECT 1 FROM pg_roles WHERE rolname = %s",
    "DATABASE": "SELECT 1 FROM pg_database WHERE datname = %s",
}


def statements(text: str) -> list[str]:
    """The statements of a file of global objects: no strings with semicolons, no
    dollar quoting (``roles.sql`` has neither, and this refuses a file that has)."""
    lines = [line.split("--", 1)[0] for line in text.splitlines()]
    body = "\n".join(lines)
    if "$$" in body or "'" in re.sub(r"'[A-Za-z0-9]+'", "", body):
        raise ValueError("only plain statements are supported (no strings, no dollar quoting)")
    return [" ".join(part.split()) for part in body.split(";") if part.strip()]


def created_object(statement: str) -> tuple[str, str] | None:
    """('ROLE' or 'DATABASE', name) if the statement creates one, else None."""
    match = _CREATE.match(statement)
    return (match[1].upper(), match[2]) if match else None


def apply(conn: psycopg.Connection, text: str) -> list[str]:
    """Runs the file and returns one line per statement: applied or skipped."""
    log = []
    for statement in statements(text):
        created = created_object(statement)
        if created is not None:
            kind, name = created
            if conn.execute(_EXISTS[kind], (name,)).fetchone() is not None:
                log.append(f"skipped (exists): {kind} {name}")
                continue
        conn.execute(statement)  # type: ignore[arg-type]
        log.append(f"applied: {statement[:60]}")
    return log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="apply sql/roles.sql, resumable")
    parser.add_argument("file", type=Path)
    arguments = parser.parse_args(argv)
    dsn = os.environ.get("KBOT_ADMIN_DSN", f"host={SOCKET_DIR} dbname=postgres user=postgres")
    with psycopg.connect(dsn, autocommit=True) as conn:
        for line in apply(conn, arguments.file.read_text(encoding="utf-8")):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
