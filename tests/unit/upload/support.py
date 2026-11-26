"""What the tests of the uploads share: an Object Storage in memory with the versioning and
the permissions of the policy of the bucket, a recorder of the steps of every durable
write and of every request, and local copies made through the real protocol of
kbot.backup (02 §13.1, point 4, E4c-2, point 7)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from kbot.backup import local
from kbot.upload import budget as budget_module
from kbot.upload import durable
from kbot.upload.store import AlreadyExists, NotFound, StoreError, Transient, Version

# The permissions of the policy kbot-copias (docs/operacion/copias_object_storage.md):
# never OBJECT_OVERWRITE nor OBJECT_VERSION_DELETE.
POLICY = frozenset({"OBJECT_CREATE", "OBJECT_INSPECT", "OBJECT_READ", "OBJECT_DELETE"})
NAMESPACE = "frxyz"
START = datetime(2026, 12, 1, 0, 55, tzinfo=UTC)


class Forbidden(StoreError):
    """403/404 for a request the policy does not allow."""


class Clock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@dataclass
class _Stored:
    version_id: str
    data: bytes | None  # None is a delete marker
    modified: datetime


@dataclass
class FakeObjectStorage:
    """One bucket with versioning, as the policy lets the instance use it. Every request
    that reaches it is appended to ``log`` (shared with the recorder of durable writes);
    ``fail`` holds the failures the next requests of an operation raise."""

    clock: Clock = field(default_factory=Clock)
    permissions: frozenset[str] = POLICY
    versioning: bool = True
    page_size: int = 1000
    log: list[tuple[str, str]] = field(default_factory=list)
    fail: dict[str, list[Exception]] = field(default_factory=dict)
    lose_response: set[str] = field(default_factory=set)  # PutObject done, answer lost
    refuse: set[str] = field(default_factory=set)  # PutObject refused for these names
    corrupt: set[str] = field(default_factory=set)  # PutObject stores other bytes
    objects: dict[str, list[_Stored]] = field(default_factory=dict)
    _next_id: int = 0

    def _request(self, operation: str, name: str = "") -> None:
        self.log.append((operation, name))
        queued = self.fail.get(operation)
        if queued:
            raise queued.pop(0)

    def _need(self, permission: str) -> None:
        if permission not in self.permissions:
            raise Forbidden(f"the policy does not grant {permission}")

    def _current(self, name: str) -> _Stored | None:
        versions = self.objects.get(name, [])
        return versions[-1] if versions and versions[-1].data is not None else None

    def _add(self, name: str, data: bytes | None) -> None:
        self._next_id += 1
        self.objects.setdefault(name, []).append(_Stored(f"v{self._next_id}", data, self.clock()))

    # -- the ObjectStore protocol -----------------------------------------------------
    def namespace(self) -> str:
        self._request("GetNamespace")
        return NAMESPACE

    def versioning_enabled(self, namespace: str) -> bool:
        self._request("GetBucket")
        return self.versioning

    def list_versions(self, namespace: str, page: str | None) -> tuple[list[Version], str | None]:
        self._request("ListObjectVersions", page or "")
        self._need("OBJECT_INSPECT")
        every = [
            Version(
                name, stored.version_id, len(stored.data or b""), stored.modified, not stored.data
            )
            for name in sorted(self.objects)
            for stored in reversed(self.objects[name])
        ]
        start = int(page or 0)
        end = start + self.page_size
        return every[start:end], (str(end) if end < len(every) else None)

    def put_new(self, namespace: str, name: str, path: Path) -> None:
        self._request("PutObject", name)
        if name in self.refuse:
            raise Forbidden(f"refused: {name}")
        exists = self._current(name) is not None
        if exists:  # If-None-Match: * is checked first, and the name is in use
            raise AlreadyExists(f"412: {name} exists")
        self._need("OBJECT_CREATE")
        data = path.read_bytes()
        self._add(name, data + b"!" if name in self.corrupt else data)
        if name in self.lose_response:
            self.lose_response.discard(name)
            raise Transient("the connection closed before the answer")

    def read(self, namespace: str, name: str) -> tuple[str | None, Iterator[bytes]]:
        self._request("GetObject", name)
        self._need("OBJECT_READ")
        stored = self._current(name)
        if stored is None or stored.data is None:
            raise NotFound(name)
        data = stored.data
        chunks = [data[i : i + (1 << 20)] for i in range(0, len(data), 1 << 20)] or [b""]
        return stored.version_id, iter(chunks)

    def delete(self, namespace: str, name: str) -> None:
        self._request("DeleteObject", name)
        self._need("OBJECT_DELETE")
        if self._current(name) is None:
            raise NotFound(name)
        self._add(name, None)  # versioning: a delete marker, the data stays as a version

    # -- what the policy forbids and the service never calls (E4C2-01) ------------------
    def create_multipart_upload(self, name: str) -> None:
        self._request("CreateMultipartUpload", name)
        self._need("OBJECT_OVERWRITE")

    def upload_part(self, name: str) -> None:
        self._request("UploadPart", name)
        self._need("OBJECT_OVERWRITE")

    def commit_multipart_upload(self, name: str) -> None:
        self._request("CommitMultipartUpload", name)
        self._need("OBJECT_OVERWRITE")

    # -- for the tests -----------------------------------------------------------------
    def tamper(self, name: str, data: bytes) -> None:
        """Someone with the console adds a different version of an object."""
        self._add(name, data)

    def remove(self, name: str) -> None:
        """The owner deletes an object from the console."""
        self._add(name, None)

    def current_names(self) -> list[str]:
        return sorted(n for n in self.objects if self._current(n) is not None)

    def bytes_of(self, name: str) -> bytes:
        stored = self._current(name)
        assert stored is not None and stored.data is not None
        return stored.data

    def sent(self, operation: str) -> list[str]:
        return [name for op, name in self.log if op == operation]


class RecordingOps(durable.FileOps):
    """The real steps of a durable write, appended to a log in their order; ``fail_at``
    makes one step raise, as a full disk or an I/O error would."""

    def __init__(self, log: list[tuple[str, str]], fail_at: str | None = None) -> None:
        self.log = log
        self.fail_at = fail_at

    def _step(self, step: str, path: Path) -> None:
        self.log.append((step, path.name))
        if step == self.fail_at:
            raise OSError(5, f"{step} failed")

    def write(self, path: Path, data: bytes) -> None:
        self._step("write", path)
        super().write(path, data)

    def fsync_file(self, path: Path) -> None:
        self._step("fsync_file", path)
        super().fsync_file(path)

    def rename(self, source: Path, target: Path) -> None:
        self._step("rename", target)
        super().rename(source, target)

    def fsync_directory(self, directory: Path) -> None:
        self._step("fsync_directory", directory)
        super().fsync_directory(directory)


def counter(directory: Path, used: dict[str, int] | None = None) -> Path:
    """The request counter, as 05_copias.sh creates it, with some days already used."""
    path = directory / "peticiones.json"
    value = budget_module.empty() | {"dias": used or {}}
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def publish(
    directory: Path, name: str, payload: bytes = b"cipher", **manifest_extra: Any
) -> local.Copy:
    """A copy published through the real protocol of kbot.backup: attempt, register,
    rename. The dump is ``payload``; the manifest carries what the uploads read."""
    kind = next(
        k
        for p, k in (
            (local.HOT_PREFIX, "caliente"),
            (local.DAY_PREFIX, "dia_sellado"),
            (local.GLOBALS_PREFIX, "globales"),
            (local.PREVIA_PREFIX, "previa"),
        )
        if name.startswith(p)
    )
    with local._attempt(directory, name) as (temporary, final):
        (temporary / local.DUMP).write_bytes(payload)
        manifest: dict[str, Any] = {
            "formato": local.FORMAT,
            "nombre": name,
            "clase": kind,
            "creada": START.isoformat(),
            "volcado": {
                "archivo": local.DUMP,
                "sha256_claro": hashlib.sha256(b"plain" + payload).hexdigest(),
                "sha256_cifrado": hashlib.sha256(payload).hexdigest(),
            },
            **manifest_extra,
        }
        if name.startswith(local.DAY_PREFIX):
            manifest.setdefault("dia", name[len(local.DAY_PREFIX) :])
            manifest.setdefault("particiones", partitions(name[len(local.DAY_PREFIX) :]))
        if name.startswith((local.HOT_PREFIX, local.PREVIA_PREFIX)):
            manifest.setdefault("dias_excluidos", [])
            manifest.setdefault(
                "archivo", {"dias_elegibles": [], "generaciones_verificadas_fuera": []}
            )
        return local._finish(directory, temporary, final, manifest)


def partitions(day: str) -> list[dict[str, Any]]:
    compact = day.replace("-", "")
    return [
        {"tabla": t, "particion": f"market.{t}_p{compact}", "filas": 3, "huella": "a" * 64}
        for t in ("candles", "depth_metrics")
    ]


def hot_name(moment: datetime) -> str:
    return f"{local.HOT_PREFIX}{moment:%Y%m%dT%H%M%SZ}"
