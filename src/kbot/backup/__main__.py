"""``python -m kbot.backup run [--previa]|globals``: the local backups (02 §13.1, point 4;
stages E4c-1 and E4c-2)."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import structlog

from kbot.backup import local
from kbot.upload import state

log = structlog.get_logger()


@contextmanager
def _exclusive(directory: Path) -> Iterator[None]:
    """Two runs never work at once on the same directory (a lock the system releases when
    the process ends, even if it crashes)."""
    import fcntl  # only on the server, which is Linux

    descriptor = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise local.BackupError(f"another backup is running in {directory}") from None
        yield
    finally:
        os.close(descriptor)


def _clean(directory: Path) -> None:
    """The attempts of a stopped run, and every copy the register does not vouch for."""
    for attempt in local.remove_attempts(directory):
        log.info("attempt_removed", path=str(attempt))
    for copy in local.reconcile(directory):
        log.warning("unregistered_copy_removed", copy=copy.name)


def run(directory: Path, recipients_file: Path, upload_state: Path = local.UPLOAD_STATE) -> None:
    with _exclusive(directory):
        _clean(directory)
        eligible = local.eligible_days()
        for day in local.pending_sealed_days(directory, eligible):
            copy = local.sealed_day_copy(directory, recipients_file, day, now=datetime.now(UTC))
            log.info("sealed_day_copied", copy=copy.path.name)
        now = datetime.now(UTC)
        listing, omit = local.omissions(directory, state.load(upload_state), now)
        copy = local.hot_copy(directory, recipients_file, now=now, omit=omit, listing=listing)
        log.info(
            "hot_copy_done",
            copy=copy.path.name,
            bytes=copy.manifest["volcado"]["bytes_cifrado"],
            days_left_out=[day.isoformat() for day in sorted(omit)],
        )
        for deleted in local.retention(directory, eligible):
            log.info("copy_deleted", copy=deleted.name)


def run_previa(directory: Path, recipients_file: Path) -> None:
    """Before an update (02_codigo.sh): a self-contained copy of the whole database."""
    with _exclusive(directory):
        _clean(directory)
        copy = local.previa_copy(directory, recipients_file, now=datetime.now(UTC))
        log.info(
            "previa_copy_done", copy=copy.path.name, bytes=copy.manifest["volcado"]["bytes_cifrado"]
        )
        for deleted in local.previa_retention(directory):
            log.info("copy_deleted", copy=deleted.name)


def run_globals(directory: Path, recipients_file: Path) -> None:
    with _exclusive(directory):
        _clean(directory)
        copy = local.globals_copy(directory, recipients_file, now=datetime.now(UTC))
        log.info("globals_copied", copy=copy.path.name)
        for deleted in local.globals_retention(directory):
            log.info("copy_deleted", copy=deleted.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="local backups of the database (stage E4c)")
    parser.add_argument("command", choices=("run", "globals"))
    parser.add_argument("--recipients", type=Path, default=local.RECIPIENTS)
    parser.add_argument("--previa", action="store_true", help="the copy before an update")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "run" and arguments.previa:
            run_previa(local.BACKUP_DIR, arguments.recipients)
        elif arguments.command == "run":
            run(local.BACKUP_DIR, arguments.recipients)
        else:
            run_globals(local.GLOBALS_DIR, arguments.recipients)
    except local.BackupError as error:
        log.error("backup_failed", error=str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
