"""Durable writes of the small files of the uploads (02 §13.1, point 4, E4c-2, point 5;
residual of E4C2-03).

A file is replaced only through this sequence, and nothing that depends on it happens
before the sequence ends:

    write the temporary file → fsync the file → rename → fsync the directory

Without the last step a crash of the server can bring back the previous file, which is
still valid: the request counter would then let one request too many through. Any step
that fails raises, and the caller sends nothing.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from kbot.archive import parquet


class PersistenceError(Exception):
    """A step of a durable write failed: what depends on it must not happen."""


class FileOps:
    """The four steps of a durable write, one method each, so that a test can record their
    order and make any of them fail."""

    def write(self, path: Path, data: bytes) -> None:
        with path.open("wb") as handle:
            handle.write(data)

    def fsync_file(self, path: Path) -> None:
        parquet.sync(path)

    def rename(self, source: Path, target: Path) -> None:
        source.replace(target)

    def fsync_directory(self, directory: Path) -> None:
        parquet.sync(directory)


REAL = FileOps()


def write_json(path: Path, value: Any, ops: FileOps = REAL) -> None:
    """Replaces ``path`` with ``value`` as JSON, durably, or raises PersistenceError."""
    data = (json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode()
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        ops.write(temporary, data)
        ops.fsync_file(temporary)
        ops.rename(temporary, path)
        ops.fsync_directory(path.parent)
    except OSError as error:
        raise PersistenceError(f"{path}: {error}") from error


def read_json(path: Path) -> Any:
    """The JSON of ``path``; raises OSError or ValueError when it cannot be read."""
    with path.open("rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


def remove_temporary(path: Path) -> None:
    """The temporary file a crash left behind, if any."""
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path.with_name(f".{path.name}.tmp"))
