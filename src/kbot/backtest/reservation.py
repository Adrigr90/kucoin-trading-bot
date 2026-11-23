"""The reservation of a query of the validation (``02`` §12.5.1 and §12.5.5; F3E5-01).

* **Before the first minute of the validation is read,** the tool creates the reservation
  of the family and version: a file in ``<history>/validacion/`` created only if it does not
  exist (``O_CREAT | O_EXCL``, atomic on NTFS and on POSIX). Of two processes that try at
  once, only one creates it.
* **Three states:** ``iniciada`` when created, and ``completada`` or ``fallida`` when the
  query ends. A failure after reading leaves it ``fallida``: it is never deleted.
* **Any reservation, in any state, prevents another query** of that family and version; an
  empty or unreadable file too, a reservation whose writing stopped halfway.
* **Its copy, with the result,** goes to ``docs/f3/consultas_validacion.json``, in the
  repository: one entry per family and version, in order. Reading, merging and publishing
  it go together under an exclusive lock, and it is replaced whole (F3E5-06).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from kbot.archive import parquet

FORMAT = "kbot.backtest reserva 1"
COPY_LOCK = "copia.lock"  # in the folder of the reservations, outside the repository
COPY_FORMAT = "kbot.backtest consultas de la validacion 1"
STARTED = "iniciada"
COMPLETED = "completada"
FAILED = "fallida"


class AlreadyReserved(RuntimeError):
    """The validation of this family and version was already reserved: it is not read
    again."""


def reservation_path(folder: Path, family: str, version: str) -> Path:
    return folder / f"{family}+{version}.json"


def reserve(folder: Path, family: str, version: str, at: datetime) -> Path:
    """Create the reservation, or raise ``AlreadyReserved`` if any exists."""
    folder.mkdir(parents=True, exist_ok=True)
    path = reservation_path(folder, family, version)
    content = {
        "formato": FORMAT,
        "familia": family,
        "version": version,
        "estado": STARTED,
        "iniciada": at.isoformat(),
    }
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except FileExistsError:
        raise AlreadyReserved(
            f"{family} {version}: the validation is already reserved ({path.name})"
        ) from None
    with os.fdopen(descriptor, "wb") as file:
        file.write(_text(content).encode("utf-8"))
        file.flush()
        os.fsync(file.fileno())
    parquet.sync(folder)
    return path


def finish(
    path: Path,
    state: str,
    at: datetime,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Mark the reservation ``completada`` or ``fallida``. The file is replaced whole, so it
    exists at every instant."""
    if state not in (COMPLETED, FAILED):
        raise ValueError(f"a query ends completed or failed, not {state!r}")
    content = json.loads(path.read_text(encoding="utf-8"))
    if content.get("estado") != STARTED:
        raise ValueError(f"{path.name}: the reservation already ended")
    content.update(estado=state, terminada=at.isoformat())
    if result is not None:
        content["resultado"] = result
    if error is not None:
        content["error"] = error
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        file.write(_text(content))
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
    parquet.sync(path.parent)
    return content


@dataclass
class Query:
    """A reserved query: the tool sets its ``result`` before it ends."""

    path: Path
    family: str
    version: str
    result: dict[str, Any] | None = None
    record: dict[str, Any] = field(default_factory=dict)


@contextmanager
def validation_query(
    folder: Path, family: str, version: str, now: Callable[[], datetime], copy: Path | None
) -> Iterator[Query]:
    """Reserve, then let the caller read. Whatever happens after the reservation, it ends
    ``completada`` or ``fallida``, and its copy goes to ``copy``."""
    query = Query(reserve(folder, family, version, now()), family, version)
    lock = folder / COPY_LOCK
    try:
        yield query
    except BaseException as error:
        query.record = finish(query.path, FAILED, now(), error=f"{type(error).__name__}: {error}")
        if copy is not None:
            write_copy(copy, query.record, lock)
        raise
    query.record = finish(query.path, COMPLETED, now(), result=query.result)
    if copy is not None:
        write_copy(copy, query.record, lock)


def write_copy(path: Path, record: dict[str, Any], lock: Path) -> None:
    """Put the entry of a reservation in the copy of the repository, in place of the one of
    the same family and version, if there was one (F3E5-06). Reading, merging and
    publishing go together under ``lock``, an exclusive lock of the operating system, so
    two queries that end at once keep both entries. The copy is replaced whole, through a
    temporary file: whoever reads it finds the old one or the new one, always valid."""
    with exclusive(lock):
        entries: list[dict[str, Any]] = []
        if path.exists():
            held = json.loads(path.read_text(encoding="utf-8"))
            if held.get("formato") != COPY_FORMAT:
                raise ValueError(f"unknown format of {path}: {held.get('formato')!r}")
            entries = list(held["consultas"])
        key = (record["familia"], record["version"])
        entries = [e for e in entries if (e["familia"], e["version"]) != key] + [record]
        entries.sort(key=lambda e: (e["iniciada"], e["familia"], e["version"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            file.write(_text({"formato": COPY_FORMAT, "consultas": entries}))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)


@contextmanager
def exclusive(lock: Path, wait: float = 60.0) -> Iterator[None]:
    """Hold the exclusive lock of the file ``lock`` (the operating system's, which it frees
    if the process dies), waiting for it at most ``wait`` seconds."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + wait
        while not _try_lock(descriptor):
            if time.monotonic() > deadline:
                raise TimeoutError(f"{lock} stayed locked for {wait} s")
            time.sleep(0.005)
        try:
            yield
        finally:
            _unlock(descriptor)
    finally:
        os.close(descriptor)


def _try_lock(descriptor: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _text(content: dict[str, Any]) -> str:
    return json.dumps(content, ensure_ascii=False, indent=1) + "\n"
