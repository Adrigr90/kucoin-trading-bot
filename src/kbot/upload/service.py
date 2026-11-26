"""The upload cycle, every hour at :55 as kbot_upload (02 §13.1, point 4, E4c-2, point 3).

kbot_upload has no role in PostgreSQL: it reads only the copies kbot-backup already
published, which are encrypted, and vouches for each with the register of its directory,
as a restore does. One cycle:

1. checks that the versioning of the bucket is on, writes in its state that a listing
   starts, before the request goes out, and lists every version of the bucket, page after
   page (E4C2-02);
2. uploads, in this order, the sealed-day copies the bucket lacks, the newest hot copy of
   the cadence, the newest global objects and the copies taken before an update: each
   object in one ``PutObject`` of a new name, never in parts (E4C2-01), and only when it
   fits in the 19 GB of the budget, versions kept by the lifecycle included (E4-D07);
3. reads every uploaded object again and compares its SHA-256 with the register: only
   then the copy is ``verificada_fuera`` in the state;
4. applies the retention of D25 to the verified copies, and the residual of R-E4-01 to
   the sealed-day copies (``retention``), with an index of the dependencies kept in
   /srv/kbot/subidas and rebuilt from the manifests in the bucket when it is missing;
5. writes its state, durably, with the bytes in the bucket, the requests of the last 32
   days and the moment of the last complete upload, which is the RPO against the loss of
   the server.

Every request goes through the budget (E4C2-03). Above 40,000 requests in 32 days only the
essential runs: the listing, and the sealed-day copies with their reading. A failure stops
the cycle where it is: what was verified stays verified, and the next cycle goes on.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from kbot.backup import local
from kbot.upload import durable, retention
from kbot.upload import state as upload_state
from kbot.upload.budget import BudgetExhausted, CounterUnreadable, RequestBudget
from kbot.upload.store import AlreadyExists, CountedStore, StoreError, Version

BUDGET_BYTES = 19 * 10**9
# The hot copies that go to the bucket: those made at an hour of the day divisible by this,
# so those of 00:40, 06:40, 12:40 and 18:40 UTC. Owner decision of 26 Nov 2026, an
# amendment of D25: every hour does not fit in the 20 GB (tools/simular_copias.py). On the
# server the copy stays hourly (02 §13.1, point 4, E4c-2).
HOT_UPLOAD_HOURS = 6
STALE = timedelta(days=2)  # an unverified leftover of a past cycle, older than this, goes
FOLDERS = {
    local.HOT_PREFIX: "caliente",
    local.DAY_PREFIX: "dia",
    local.GLOBALS_PREFIX: "globales",
    local.PREVIA_PREFIX: "previa",
}


def objects_of(copy: str) -> tuple[str, str]:
    """The two objects of a copy in the bucket: its dump and its manifest."""
    prefix = next(p for p in FOLDERS if copy.startswith(p))
    return f"{FOLDERS[prefix]}/{copy}/{local.DUMP}", f"{FOLDERS[prefix]}/{copy}/{local.MANIFEST}"


def copy_of(name: str) -> str | None:
    """The copy an object of the bucket belongs to, or None for anything else."""
    parts = name.split("/")
    if len(parts) != 3 or parts[2] not in (local.DUMP, local.MANIFEST):
        return None
    prefix = next((p for p, folder in FOLDERS.items() if folder == parts[0]), None)
    if prefix is None or not parts[1].startswith(prefix):
        return None
    return parts[1]


@dataclass(frozen=True)
class LocalCopy:
    name: str
    prefix: str
    path: Path
    manifest: dict[str, Any]
    dump_sha256: str
    manifest_sha256: str

    @property
    def size(self) -> int:
        return (self.path / local.DUMP).stat().st_size + (self.path / local.MANIFEST).stat().st_size


def local_copies(backups: Path, globals_dir: Path) -> dict[str, LocalCopy]:
    """The published copies that the register of their directory vouches for."""
    found: dict[str, LocalCopy] = {}
    for directory, prefixes in (
        (backups, (local.HOT_PREFIX, local.DAY_PREFIX, local.PREVIA_PREFIX)),
        (globals_dir, (local.GLOBALS_PREFIX,)),
    ):
        if not directory.is_dir():
            continue
        for prefix in prefixes:
            for copy in local.published(directory, prefix):
                found[copy.path.name] = LocalCopy(
                    name=copy.path.name,
                    prefix=prefix,
                    path=copy.path,
                    manifest=copy.manifest,
                    dump_sha256=str(copy.manifest["volcado"]["sha256_cifrado"]),
                    manifest_sha256=local.sha256_file(copy.path / local.MANIFEST),
                )
    return found


@dataclass
class Bucket:
    """What a listing of every version says: the objects that exist now (their newest
    version is not a delete marker), and the bytes of every version that is not a marker."""

    current: dict[str, Version]
    used: int

    @classmethod
    def of(cls, versions: list[Version]) -> Bucket:
        newest: dict[str, Version] = {}
        for version in versions:
            known = newest.get(version.name)
            if known is None or (version.modified, version.version_id) > (
                known.modified,
                known.version_id,
            ):
                newest[version.name] = version
        current = {n: v for n, v in newest.items() if not v.delete_marker}
        return cls(current, sum(v.size for v in versions if not v.delete_marker))

    def has(self, copy: str) -> bool:
        return all(name in self.current for name in objects_of(copy))


@dataclass
class Report:
    uploaded: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Uploader:
    def __init__(
        self,
        store: CountedStore,
        *,
        backups: Path = local.BACKUP_DIR,
        globals_dir: Path = local.GLOBALS_DIR,
        directory: Path = upload_state.UPLOAD_DIR,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ops: durable.FileOps = durable.REAL,
        hot_every_hours: int = HOT_UPLOAD_HOURS,
    ) -> None:
        self.store = store
        self.budget: RequestBudget = store.budget
        self.backups = backups
        self.globals_dir = globals_dir
        self.directory = directory
        self.clock = clock
        self.ops = ops
        self.hot_every_hours = hot_every_hours
        self.state: dict[str, Any] = upload_state.empty()

    # -- the state and the index, always durable --------------------------------------
    def _save(self) -> None:
        durable.write_json(self.directory / upload_state.STATE, self.state, self.ops)

    def _now(self) -> str:
        return self.clock().astimezone(UTC).isoformat()

    def _load_index(self) -> dict[str, list[dict[str, Any]]] | None:
        try:
            value = durable.read_json(self.directory / upload_state.DEPENDENCIES)
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _save_index(self, index: dict[str, list[dict[str, Any]]]) -> None:
        durable.write_json(self.directory / upload_state.DEPENDENCIES, index, self.ops)

    # -- the cycle ----------------------------------------------------------------------
    def run(self) -> Report:
        report = Report()
        self.state = upload_state.load(self.directory / upload_state.STATE) or upload_state.empty()
        complete = False
        try:
            complete = self._cycle(report)
        except (BudgetExhausted, CounterUnreadable, durable.PersistenceError, StoreError) as error:
            report.errors.append(f"{type(error).__name__}: {error}")
        finally:
            self.state["errores"] = report.errors
            try:
                self.state["peticiones"] = self.budget.used()
            except CounterUnreadable:
                self.state["peticiones"] = None
            if complete and not report.errors:
                self.state["ultima_subida_completa"] = self._now()
            hot = [n for n in self.state.get("verificadas", {}) if n.startswith(local.HOT_PREFIX)]
            self.state["ultima_caliente_fuera"] = max(hot, default=None)
            self._save()  # if even this fails, the error reaches the service and its log
        return report

    def _cycle(self, report: Report) -> bool:
        namespace = self.state.get("espacio")
        if not namespace:
            namespace = self.store.namespace()
            self.state["espacio"] = namespace
            self._save()
        if not self.store.versioning_enabled(namespace):
            report.errors.append("the versioning of the bucket is not on: nothing is uploaded")
            return False
        listing = {"id": uuid.uuid4().hex, "empezado": self._now(), "terminado": None, "ok": False}
        self.state["listado"] = listing
        self._save()  # E4C2-02: the attempt is known before the request goes out
        bucket = Bucket.of(self.store.list_all(namespace))
        listing.update(terminado=self._now(), ok=True)
        verified: dict[str, dict[str, Any]] = self.state.setdefault("verificadas", {})
        for name in list(verified):
            if bucket.has(name):
                verified[name]["visto"] = listing["id"]
            else:
                del verified[name]  # it left the bucket: it is uploaded again if still here
        self.state["ocupado"] = bucket.used
        self._save()

        copies = local_copies(self.backups, self.globals_dir)
        saving = self.budget.saving()
        wanted = self._wanted(copies, verified, saving)
        if saving:
            report.errors.append(
                "more than 40000 requests in 32 days: only the sealed-day copies are uploaded"
            )
        for copy in wanted:
            if bucket.used + copy.size > BUDGET_BYTES and not bucket.has(copy.name):
                report.errors.append(f"{copy.name}: it does not fit in the 19 GB of the budget")
                continue
            if not self.budget.covers(4):
                report.errors.append(f"{copy.name}: not enough requests left to upload it")
                break
            if self._upload(namespace, copy, bucket, report):
                report.uploaded.append(copy.name)
        if not saving:
            self._retention(namespace, bucket, copies, report)
        self.state["ocupado"] = bucket.used
        return not saving

    def _wanted(
        self, copies: dict[str, LocalCopy], verified: dict[str, Any], saving: bool
    ) -> list[LocalCopy]:
        def missing(prefix: str) -> list[LocalCopy]:
            return [
                c for n, c in sorted(copies.items()) if c.prefix == prefix and n not in verified
            ]

        # A day already eligible is not uploaded again: its copy may just have left the bucket.
        eligible, _ = _knowledge(copies)
        wanted = [
            c
            for c in missing(local.DAY_PREFIX)
            if date.fromisoformat(c.name[len(local.DAY_PREFIX) :]) not in eligible
        ]
        if saving:
            return wanted
        uploaded_hot = [n for n in verified if n.startswith(local.HOT_PREFIX)]
        cadence = [
            c
            for c in missing(local.HOT_PREFIX)
            if retention.copy_time(c.name, local.HOT_PREFIX).hour % self.hot_every_hours == 0
        ]
        if cadence and (not uploaded_hot or cadence[-1].name > max(uploaded_hot)):
            wanted.append(cadence[-1])
        uploaded_globals = [n for n in verified if n.startswith(local.GLOBALS_PREFIX)]
        newest_globals = missing(local.GLOBALS_PREFIX)[-1:]
        if newest_globals and (
            not uploaded_globals or newest_globals[0].name > max(uploaded_globals)
        ):
            wanted += newest_globals
        wanted += missing(local.PREVIA_PREFIX)[-retention.PREVIA :]
        return wanted

    def _upload(self, namespace: str, copy: LocalCopy, bucket: Bucket, report: Report) -> bool:
        """Uploads the two objects of a copy that the bucket lacks, reads both again, and
        records the copy as verified when their SHA-256 match. A name already in use is
        never overwritten (E4C2-01): what is there is read and compared."""
        dump_name, manifest_name = objects_of(copy.name)
        for name, path in (
            (dump_name, copy.path / local.DUMP),
            (manifest_name, copy.path / local.MANIFEST),
        ):
            if name in bucket.current:
                continue
            # A response lost after the upload gives 412 on the retry: the reading below
            # decides whether what is there is this copy.
            with contextlib.suppress(AlreadyExists):
                self.store.put_new(namespace, name, path)
            bucket.used += path.stat().st_size
        dump_sha256 = self.store.sha256(namespace, dump_name)
        manifest_bytes = self.store.read_bytes(namespace, manifest_name)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        entry: dict[str, Any] = {
            "clase": FOLDERS[copy.prefix],
            "objeto": dump_name,
            "sha256": dump_sha256,
            "objeto_manifiesto": manifest_name,
            "sha256_manifiesto": manifest_sha256,
            "verificada": self._now(),
            "visto": None,  # it was not in this cycle's listing (E4C2-02)
        }
        # Only a sealed-day copy made again with the same name can differ and still be the
        # same day: its partitions decide, and the copy in the bucket stays.
        differs = (dump_sha256, manifest_sha256) != (copy.dump_sha256, copy.manifest_sha256)
        if differs and (
            copy.prefix != local.DAY_PREFIX or not _same_day(manifest_bytes, dump_sha256, copy)
        ):
            report.errors.append(f"{copy.name}: the copy in the bucket differs from this one")
            return False
        if copy.prefix == local.DAY_PREFIX:
            entry["particiones"] = upload_state.partitions_digest(copy.manifest["particiones"])
        self.state["verificadas"][copy.name] = entry
        self._save()
        if copy.prefix == local.HOT_PREFIX:
            index = self._load_index() or {}
            index[copy.name] = [d.__dict__ for d in retention.dependencies_of(copy.manifest)]
            self._save_index(index)
        return True

    def _retention(
        self, namespace: str, bucket: Bucket, copies: dict[str, LocalCopy], report: Report
    ) -> None:
        verified: dict[str, dict[str, Any]] = self.state["verificadas"]
        hot = [
            (n, retention.copy_time(n, local.HOT_PREFIX))
            for n in verified
            if n.startswith(local.HOT_PREFIX)
        ]
        keep = retention.keep_hot(hot)
        keep |= retention.keep_last(
            (n for n in verified if n.startswith(local.PREVIA_PREFIX)), retention.PREVIA
        )
        keep |= retention.keep_last(
            (n for n in verified if n.startswith(local.GLOBALS_PREFIX)), retention.GLOBALS
        )
        gone = sorted(n for n in verified if not n.startswith(local.DAY_PREFIX) and n not in keep)
        for name in gone:
            self._delete(namespace, name, bucket, report)
        # Leftovers of an interrupted cycle that nothing verified, once they are old.
        now = self.clock()
        leftovers = {
            copy
            for name, version in bucket.current.items()
            if (copy := copy_of(name)) is not None
            and not copy.startswith(local.DAY_PREFIX)
            and copy not in verified
            and copy not in copies
            and now - version.modified > STALE
        }
        for copy in sorted(leftovers):
            self._delete(namespace, copy, bucket, report)
        self._day_copies(namespace, bucket, copies, report)

    def _day_copies(
        self, namespace: str, bucket: Bucket, copies: dict[str, LocalCopy], report: Report
    ) -> None:
        """The residual of R-E4-01: a sealed-day copy goes only when its day is eligible and
        every retained copy that relies on it can be restored without it."""
        verified: dict[str, dict[str, Any]] = self.state["verificadas"]
        days = sorted(n for n in verified if n.startswith(local.DAY_PREFIX))
        eligible, generations = _knowledge(copies)
        if not days or not eligible:
            return
        retained = {n for n in verified if n.startswith(local.HOT_PREFIX)}
        dependencies = self._dependencies(namespace, retained)
        for name in days:
            day = date.fromisoformat(name[len(local.DAY_PREFIX) :])
            if retention.day_copy_may_go(name, day, dependencies, retained, eligible, generations):
                self._delete(namespace, name, bucket, report)

    def _dependencies(self, namespace: str, retained: set[str]) -> list[retention.Dependency]:
        """The index of the dependencies of the retained hot copies; whatever it lacks is
        read again from the manifests in the bucket."""
        index = self._load_index() or {}
        changed = False
        for name in sorted(retained - set(index)):
            manifest = json.loads(self.store.read_bytes(namespace, objects_of(name)[1]))
            index[name] = [d.__dict__ for d in retention.dependencies_of(manifest)]
            changed = True
        for name in set(index) - retained:
            del index[name]
            changed = True
        if changed:
            self._save_index(index)
        return [retention.Dependency(**item) for name in sorted(retained) for item in index[name]]

    def _delete(self, namespace: str, copy: str, bucket: Bucket, report: Report) -> None:
        for name in objects_of(copy):
            if name in bucket.current:
                self.store.delete(namespace, name)
                bucket.current.pop(name)
        self.state["verificadas"].pop(copy, None)
        self._save()
        report.deleted.append(copy)


def _knowledge(copies: dict[str, LocalCopy]) -> tuple[set[date], set[str]]:
    """The eligible days and the generations verified outside, as the newest hot copy read
    them in its snapshot: kbot_upload has no access to the database."""
    hot = sorted(n for n in copies if n.startswith(local.HOT_PREFIX))
    if not hot:
        return set(), set()
    knowledge = copies[hot[-1]].manifest.get("archivo") or {}
    try:
        eligible = {date.fromisoformat(d) for d in knowledge.get("dias_elegibles", [])}
        generations = {str(g) for g in knowledge.get("generaciones_verificadas_fuera", [])}
    except (AttributeError, TypeError, ValueError):
        return set(), set()
    return eligible, generations


def _same_day(manifest_bytes: bytes, dump_sha256: str, copy: LocalCopy) -> bool:
    """Whether the copy in the bucket is a copy of the same sealed day with the same data:
    its manifest names it, vouches for the dump that is there, and has the same
    partitions, rows and fingerprints."""
    try:
        manifest = json.loads(manifest_bytes)
        return (
            manifest["nombre"] == copy.name
            and manifest["volcado"]["sha256_cifrado"] == dump_sha256
            and upload_state.partitions_digest(manifest["particiones"])
            == upload_state.partitions_digest(copy.manifest["particiones"])
        )
    except (ValueError, KeyError, TypeError):
        return False
