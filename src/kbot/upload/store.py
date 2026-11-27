"""What the uploads need from Object Storage, and the count of every request (02 §13.1,
point 4, E4c-2, points 2, 3 and 5).

``ObjectStore`` is the whole surface the service uses, and each method is exactly one
request: the namespace, the versioning of the bucket, one page of the listing of every
version, one ``PutObject`` of a new name (never in parts, E4C2-01), one ``GetObject`` and
one ``DeleteObject``. ``oci_store.OciStore`` implements it with the SDK of Oracle; the
tests use a fake one in memory with the versioning and the permissions of the policy.

``CountedStore`` reserves every request in the budget before sending it, every page and
every retry included, and retries only a transient failure (E4C2-03).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, TypeVar

from kbot.upload.budget import RequestBudget

BUCKET = "kbot-backups"
REGION = "eu-frankfurt-1"
RETRIES = 2  # after the first attempt, only for a transient failure
T = TypeVar("T")


class StoreError(Exception):
    """Object Storage refused a request or did not answer."""


class Transient(StoreError):
    """A failure that may pass: a timeout, a lost connection, 429 or 5xx."""


class AlreadyExists(StoreError):
    """A ``PutObject`` with ``If-None-Match: *`` found the name in use (412)."""


class NotFound(StoreError):
    """The object does not exist (404)."""


@dataclass(frozen=True)
class Version:
    """One version of an object, as the listing of every version gives it."""

    name: str
    version_id: str
    size: int
    modified: datetime
    delete_marker: bool


class ObjectStore(Protocol):
    def namespace(self) -> str: ...

    def versioning_enabled(self, namespace: str) -> bool: ...

    def list_versions(
        self, namespace: str, page: str | None
    ) -> tuple[list[Version], str | None]: ...

    def put_new(self, namespace: str, name: str, path: Path) -> None: ...

    def read(self, namespace: str, name: str) -> tuple[str | None, Iterator[bytes]]: ...

    def delete(self, namespace: str, name: str) -> None: ...


class CountedStore:
    """An ObjectStore whose every request is reserved in the budget before it is sent."""

    def __init__(self, store: ObjectStore, budget: RequestBudget) -> None:
        self._store = store
        self.budget = budget

    def _call(self, request: Callable[[], T]) -> T:
        for attempt in range(RETRIES + 1):
            self.budget.reserve()  # raises, and nothing is sent
            try:
                return request()
            except Transient:
                if attempt == RETRIES:
                    raise
        raise AssertionError("unreachable")

    def namespace(self) -> str:
        return self._call(self._store.namespace)

    def versioning_enabled(self, namespace: str) -> bool:
        return self._call(lambda: self._store.versioning_enabled(namespace))

    def list_all(self, namespace: str) -> list[Version]:
        """Every version of the bucket, page after page, each page a counted request."""
        versions: list[Version] = []
        page: str | None = None
        while True:
            found, page = self._call(lambda token=page: self._store.list_versions(namespace, token))
            versions.extend(found)
            if page is None:
                return versions

    def put_new(self, namespace: str, name: str, path: Path) -> None:
        self._call(lambda: self._store.put_new(namespace, name, path))

    def sha256(self, namespace: str, name: str) -> tuple[str, str | None]:
        """Reads the object again: the SHA-256 of what came back and the version that was
        read (E4C2-09). A transient failure in the middle starts the read again, as a new
        counted request."""

        def read() -> tuple[str, str | None]:
            version, chunks = self._store.read(namespace, name)
            digest = hashlib.sha256()
            for chunk in chunks:
                digest.update(chunk)
            return digest.hexdigest(), version

        return self._call(read)

    def read_bytes(self, namespace: str, name: str) -> tuple[bytes, str | None]:
        def read() -> tuple[bytes, str | None]:
            version, chunks = self._store.read(namespace, name)
            return b"".join(chunks), version

        return self._call(read)

    def delete(self, namespace: str, name: str) -> None:
        self._call(lambda: self._store.delete(namespace, name))
