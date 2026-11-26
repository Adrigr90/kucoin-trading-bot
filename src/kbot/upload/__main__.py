"""``python -m kbot.upload run``: one upload cycle, as kbot_upload (02 §13.1, point 4, E4c-2).

Exit 0 when the cycle had no error, 1 otherwise; the errors are in the log and in
/srv/kbot/subidas/estado.json.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import structlog

from kbot.upload import state
from kbot.upload.budget import RequestBudget
from kbot.upload.service import Uploader
from kbot.upload.store import CountedStore, StoreError

log = structlog.get_logger()


@contextmanager
def _exclusive(directory: Path) -> Iterator[bool]:
    """Two cycles never run at once (a lock the system releases when the process ends)."""
    import fcntl  # only on the server, which is Linux

    descriptor = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(descriptor)


def run(directory: Path = state.UPLOAD_DIR) -> int:
    from kbot.upload.oci_store import OciStore

    with _exclusive(directory) as alone:
        if not alone:
            log.error("upload_failed", error="another upload cycle is running")
            return 1
        try:
            store = OciStore.from_instance()
        except StoreError as error:
            log.error("upload_failed", error=str(error))
            return 1
        except Exception as error:  # the instance principal could not be obtained
            log.error("upload_failed", error=f"{type(error).__name__}: {error}")
            return 1
        budget = RequestBudget(directory / state.COUNTER)
        report = Uploader(CountedStore(store, budget), directory=directory).run()
    for copy in report.uploaded:
        log.info("copy_uploaded", copy=copy)
    for copy in report.deleted:
        log.info("copy_deleted", copy=copy)
    for error in report.errors:
        log.error("upload_error", error=error)
    return 1 if report.errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="upload of the copies to Object Storage (E4c-2)")
    parser.add_argument("command", choices=("run",))
    parser.parse_args(argv)
    return run()


if __name__ == "__main__":
    sys.exit(main())
