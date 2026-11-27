"""The external disk: what kbot_archive publishes for the owner's PC, and the verification
of its receipts and challenge answers (02 §13.1, point 4, E4c-3).

``python -m kbot.archive.externo run`` runs every 15 minutes as kbot_archive, with the group
kbot_upload only in its unit (E4C3-07), so it reads the register and the manifests of the
copies and modifies none. One run:

1. publishes ``generaciones.json`` (every archived generation with its exact set of files)
   and ``procesados.json`` (what of ``recibos/`` it already processed, for the PC to delete)
   in /srv/kbot/externo/publico, which the PC sees read-only as ``desafios/``;
2. reads ``recibos/`` without following any link and only regular files of kbot_sync, of a
   bounded size and number (E4C3-04 and the design's point 1);
3. a generation receipt is valid only if its set of files is exactly the server's (E4-D01).
   The last receipt of each pair (generation, disk) is the one that counts (E4C3-01), and
   repeating it changes nothing (its residual). A valid receipt inserts ``replicado_fuera``
   and issues one challenge bound to it: an offset, a length and a nonce per file, whose
   expected answers stay in /srv/kbot/externo/privado;
4. an answer inserts ``verificado_fuera`` only for the pending challenge of its pair, bound
   to the last valid receipt, issued in an earlier run, with every SHA-256 equal;
5. the receipt of the recoverable point carries the whole manifest of a hot copy, which is
   authenticated against the register of the copies, never deleted (E4C3-08); its
   dependencies must be exactly the ones that manifest fixes (E4C3-02 and E4C3-03). It is
   the disk's current observation: a newer one replaces the point, also with an older copy
   or with none, and an older one is refused (E4C3-10);
6. at most two disks are admitted (E4C3-04); ``retirar-disco`` frees one, and what was
   refused for lack of room is looked at again (E4C3-12).

Every transition is saved before anything announces it (E4C3-09): the challenges, their
withdrawal, the observations of each disk and the acknowledgements of ``procesados.json``
are published only from the saved state, at the end of each run and again at the start of
the next one. A file of ``recibos/`` already processed and not touched since is skipped
without reading it, so the limit of reads per run always moves the queue on (E4C3-13).

It never inserts ``elegible_para_exclusion_backup``: that comes with stage E4c-4.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg

from kbot.archive.archiver import Connect
from kbot.upload import durable

BASE = Path("/srv/kbot/externo")
ARCHIVE_ROOT = Path("/srv/kbot/archive")
RECEIPTS = Path("/srv/kbot/sftp/recibos")
COPIES = Path("/srv/kbot/backups")
REGISTER = "registro.jsonl"
FORMAT = "kbot-externo-1"
MAX_DISKS = 2
RECEIPT_LIMIT = 1 << 20  # a generation receipt or an answer
POINT_LIMIT = 4 << 20  # the receipt of the recoverable point carries a whole manifest
PER_RUN = 1000
CHALLENGE_BYTES = 4096
PROCESSED_DAYS = 30
HOT_PREFIX = "caliente-"
_DISK = frozenset("0123456789abcdef")


class Rejected(Exception):
    """A receipt or an answer that does not hold: it changes nothing but its own record."""


class Ignored(Rejected):
    """Not even read: a link, a directory, a file of another owner or too big."""


class NotAdmitted(Rejected):
    """Refused only because of which disks are admitted: looked at again when that set
    changes (E4C3-12)."""


@dataclass(frozen=True)
class ServerFile:
    path: str  # relative to the archive root, as market.archive_files keeps it
    size: int
    rows: int
    sha256: str


@dataclass
class Report:
    published: bool = False
    accepted: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    points: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def empty() -> dict[str, Any]:
    return {"formato": FORMAT, "ejecucion": 0, "discos": {}, "pares": {}, "procesados": {}}


def _disk(value: object) -> str:
    if not isinstance(value, str) or len(value) != 32 or not set(value) <= _DISK:
        raise Rejected("the disk identifier is not 32 hexadecimal characters")
    return value


def _stamp(info: os.stat_result) -> str:
    """What changes whenever a file of ``recibos/`` is written again or replaced."""
    return f"{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"


@contextmanager
def _opened(path: Path, owner: int | None, limit: int) -> Iterator[tuple[bytes, str]]:
    """The bytes of a file of ``recibos/`` and its stamp, read without following any link,
    only if it is a regular file of ``owner`` (kbot_sync) and no bigger than ``limit``."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise Ignored(f"cannot open without following links: {error.strerror}") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise Ignored("not a regular file")
        if owner is not None and info.st_uid != owner:
            raise Ignored("not a file of kbot_sync")
        if info.st_size > limit:
            raise Ignored(f"bigger than {limit} bytes")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            content = handle.read(limit + 1)
        if len(content) > limit:
            raise Ignored(f"bigger than {limit} bytes")
        yield content, _stamp(info)
    finally:
        os.close(descriptor)


def server_generations(conn: psycopg.Connection) -> dict[str, tuple[date, dict[str, ServerFile]]]:
    """Every archived generation with its exact set of files."""
    rows = conn.execute(
        "SELECT g.generation_id, g.day, f.path, f.bytes, f.row_count, f.sha256"
        " FROM market.archive_generations g"
        " JOIN market.archive_files f ON f.generation_id = g.generation_id"
        " ORDER BY g.day, g.generation_id, f.path"
    ).fetchall()
    found: dict[str, tuple[date, dict[str, ServerFile]]] = {}
    for generation, day, path, size, rows_count, sha256 in rows:
        entry = found.setdefault(str(generation), (day, {}))
        entry[1][path] = ServerFile(path, int(size), int(rows_count), str(sha256))
    return found


def generation_states(conn: psycopg.Connection) -> dict[str, set[str]]:
    states: dict[str, set[str]] = {}
    for generation, state in conn.execute(
        "SELECT generation_id, state FROM market.archive_generation_states"
    ):
        states.setdefault(str(generation), set()).add(state)
    return states


def insert_state(conn: psycopg.Connection, generation: str, state: str) -> None:
    if state not in ("replicado_fuera", "verificado_fuera"):
        raise ValueError(f"this service never inserts {state}")  # never the eligibility
    conn.execute(
        "INSERT INTO market.archive_generation_states (generation_id, state)"
        " VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (generation, state),
    )


def index(
    generations: Mapping[str, tuple[date, Mapping[str, ServerFile]]],
    verified: Mapping[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """The generations for the PC, each with the disks it is already verified with: the PC
    checks again, in each visit, the ones its disk has not verified yet."""
    verified = verified or {}
    return [
        {
            "generacion": generation,
            "dia": day.isoformat(),
            "verificada_en_discos": sorted(verified.get(generation, [])),
            "archivos": [
                {"ruta": f.path, "bytes": f.size, "filas": f.rows, "sha256": f.sha256}
                for _, f in sorted(files.items())
            ],
        }
        for generation, (day, files) in sorted(generations.items(), key=lambda g: (g[1][0], g[0]))
    ]


def register_entries(copies: Path) -> dict[str, dict[str, Any]]:
    """The entries of the register of the copies by copy name. The register is append-only
    and never deleted, so it vouches for a copy after the copy itself has gone."""
    found: dict[str, dict[str, Any]] = {}
    path = copies / REGISTER
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("copia"), str):
            found[entry["copia"]] = entry
    return found


def copy_time(name: str) -> datetime:
    return datetime.strptime(name[len(HOT_PREFIX) :], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


class Verifier:
    def __init__(
        self,
        connect: Connect,
        *,
        base: Path = BASE,
        archive_root: Path = ARCHIVE_ROOT,
        receipts: Path = RECEIPTS,
        copies: Path = COPIES,
        owner: int | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ops: durable.FileOps = durable.REAL,
        nonce: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self.connect = connect
        self.public = base / "publico"
        self.private = base / "privado"
        self.archive_root = archive_root
        self.receipts = receipts
        self.copies = copies
        self.owner = owner
        self.clock = clock
        self.ops = ops
        self.nonce = nonce
        self.state: dict[str, Any] = empty()

    # -- the private state, always durable ----------------------------------------------
    def _load(self) -> None:
        try:
            value = durable.read_json(self.private / "estado.json")
        except (OSError, ValueError):
            value = None
        self.state = (
            value if isinstance(value, dict) and value.get("formato") == FORMAT else empty()
        )

    def _save(self) -> None:
        durable.write_json(self.private / "estado.json", self.state, self.ops)

    def _publish(self, name: str, value: Any) -> None:
        path = self.public / name
        data = (json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode()
        try:
            if path.read_bytes() == data:
                return
        except OSError:
            pass
        durable.write_json(path, value, self.ops)

    def _announce(self) -> None:
        """Publishes what the saved state says, and only that (E4C3-09): each pending
        challenge and no other, the last observation of each disk, and the acknowledgements
        the PC deletes by. Called only with the state as it is on disk."""
        pending = {
            pair["desafio"]["id"]: pair["desafio"]["publico"]
            for pair in self.state["pares"].values()
            if pair.get("desafio")
        }
        for identifier, public in sorted(pending.items()):
            self._publish(f"desafio_{identifier}.json", public)
        for path in sorted(self.public.glob("desafio_*.json")):
            if path.name[len("desafio_") : -len(".json")] not in pending:
                path.unlink(missing_ok=True)
        self._publish(
            "discos.json",
            {
                disk: {"observacion": int(record.get("observacion", 0))}
                for disk, record in sorted(self.state["discos"].items())
            },
        )
        self._publish(
            "procesados.json",
            {name: entry.get("sha256") for name, entry in sorted(self.state["procesados"].items())},
        )

    # -- the run --------------------------------------------------------------------------
    def run(self) -> Report:
        report = Report()
        self._load()
        self._announce()  # what an interrupted run could not publish, from the saved state
        self.state["ejecucion"] = int(self.state.get("ejecucion", 0)) + 1
        with self.connect() as conn:
            generations = server_generations(conn)
            states = generation_states(conn)
            self._publish("generaciones.json", index(generations, self._verified_disks()))
            report.published = True
            reads = PER_RUN
            for name in sorted(os.listdir(self.receipts)):
                if reads == 0:
                    break
                if self._unchanged(name):
                    continue  # processed and not touched since: not even read (E4C3-13)
                reads -= 1
                self._one(conn, name, generations, states, report)
        self._prune()
        self._save()
        self._announce()
        return report

    def _unchanged(self, name: str) -> bool:
        entry = self.state["procesados"].get(name)
        if not entry or "sello" not in entry:
            return False
        try:
            return _stamp(os.lstat(self.receipts / name)) == entry["sello"]
        except OSError:
            return True  # gone since the listing: nothing to do

    def _one(
        self,
        conn: psycopg.Connection,
        name: str,
        generations: Mapping[str, tuple[date, Mapping[str, ServerFile]]],
        states: dict[str, set[str]],
        report: Report,
    ) -> None:
        path = self.receipts / name
        limit = POINT_LIMIT if name == "recibo_punto.json" else RECEIPT_LIMIT
        processed = self.state["procesados"]
        previous = processed.get(name, {})
        try:
            with _opened(path, self.owner, limit) as (content, seen):
                digest = hashlib.sha256(content).hexdigest()
                processed[name] = {"sha256": digest, "sello": seen, "fecha": self._today()}
                if previous.get("sha256") == digest:
                    if previous.get("por_discos"):
                        processed[name]["por_discos"] = True
                    return  # the same bytes written again: processed already
                document = json.loads(content)
                if not isinstance(document, dict):
                    raise Rejected("not a JSON object")
                kind = document.get("tipo")
                if kind == "generacion":
                    self._receipt(conn, document, digest, generations, states, report)
                elif kind == "respuesta":
                    self._answer(conn, document, states, report)
                elif kind == "punto":
                    self._point(document, report)
                else:
                    raise Rejected(f"unknown kind {kind!r}")
        except Ignored as error:
            try:  # recorded by its stamp, so that it is not looked at again until it changes
                seen = _stamp(os.lstat(path))
            except OSError:
                seen = None
            if seen is not None:
                processed[name] = {"sha256": None, "sello": seen, "fecha": self._today()}
            report.rejected.append(f"{name}: {error}")
        except NotAdmitted as error:
            processed[name]["por_discos"] = True
            report.rejected.append(f"{name}: {error}")
        except Rejected as error:
            report.rejected.append(f"{name}: {error}")
        except (ValueError, KeyError, TypeError) as error:
            report.rejected.append(f"{name}: malformed: {error}")

    def _today(self) -> str:
        return self.clock().astimezone(UTC).date().isoformat()

    def _prune(self) -> None:
        horizon = (self.clock().astimezone(UTC).date() - timedelta(days=PROCESSED_DAYS)).isoformat()
        processed = self.state["procesados"]
        for name in [n for n, e in processed.items() if e.get("fecha", "") < horizon]:
            del processed[name]

    def _verified_disks(self) -> dict[str, list[str]]:
        """The disks on which each generation is verified and still holds: after a failed
        receipt the PC checks that generation again in every visit, not only monthly."""
        found: dict[str, list[str]] = {}
        for key, pair in self.state["pares"].items():
            if pair.get("verificado") and pair.get("valido"):
                generation, disk = key.split("|")
                found.setdefault(generation, []).append(disk)
        return found

    # -- disks ----------------------------------------------------------------------------
    def _admit(self, disk: str) -> dict[str, Any]:
        disks: dict[str, Any] = self.state["discos"]
        if disk not in disks:
            if len(disks) >= MAX_DISKS:
                raise NotAdmitted(f"disk {disk} not admitted: already {MAX_DISKS} disks")
            disks[disk] = {
                "admitido": self.clock().isoformat(),
                "observacion": 0,
                "ultima_visita": None,
                "ultima_sin_errores": None,
                "punto": None,
            }
            self._disks_changed()
        return disks[disk]

    def _disks_changed(self) -> None:
        """What was refused only because of the admitted disks is read again (E4C3-12)."""
        processed = self.state["procesados"]
        for name in [n for n, e in processed.items() if e.get("por_discos")]:
            del processed[name]

    def retire_disk(self, disk: str) -> None:
        self._load()
        self.state["discos"].pop(_disk(disk), None)
        for key in [k for k in self.state["pares"] if k.endswith(f"|{disk}")]:
            del self.state["pares"][key]
        self._disks_changed()
        self._save()
        self._announce()  # its challenges are withdrawn only once that is saved

    # -- generation receipts (E4C3-01 and its residual) ------------------------------------
    def _receipt(
        self,
        conn: psycopg.Connection,
        document: Mapping[str, Any],
        digest: str,
        generations: Mapping[str, tuple[date, Mapping[str, ServerFile]]],
        states: dict[str, set[str]],
        report: Report,
    ) -> None:
        disk = _disk(document["disco"])
        generation = str(uuid.UUID(str(document["generacion"])))
        known = generations.get(generation)
        if known is None:
            raise Rejected(f"generation {generation} does not exist")
        day, files = known
        sent = {str(f["ruta"]): (int(f["bytes"]), str(f["sha256"])) for f in document["archivos"]}
        valid = document.get("dia") == day.isoformat() and sent == {
            p: (f.size, f.sha256) for p, f in files.items()
        }
        if not valid and disk not in self.state["discos"]:
            raise Rejected("an invalid receipt from a disk that is not admitted")
        self._admit(disk)
        key = f"{generation}|{disk}"
        pair = self.state["pares"].setdefault(
            key, {"recibo": None, "valido": False, "desafio": None, "verificado": False}
        )
        if pair["recibo"] == digest:
            return  # the pair is already there: repeating it changes nothing
        pair["recibo"] = digest
        if not valid:
            pair["valido"] = False
            pair["desafio"] = None  # annulled; withdrawn once that is saved
            raise Rejected(f"generation {generation}: its files are not the server's")
        pair["valido"] = True
        if "replicado_fuera" not in states.get(generation, set()):
            insert_state(conn, generation, "replicado_fuera")
            states.setdefault(generation, set()).add("replicado_fuera")
        report.accepted.append(generation)
        if pair["verificado"]:
            return  # a later check that still holds: nothing new to prove
        pair["desafio"] = self._challenge(generation, disk, digest, files)  # replaces the old

    def _challenge(
        self, generation: str, disk: str, receipt: str, files: Mapping[str, ServerFile]
    ) -> dict[str, Any]:
        identifier = uuid.uuid4().hex
        items, expected = [], []
        for path, f in sorted(files.items()):
            length = min(CHALLENGE_BYTES, f.size)
            offset = secrets.randbelow(f.size - length + 1)
            nonce = self.nonce(32)
            with (self.archive_root / path).open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read(length)
            items.append({"ruta": path, "desde": offset, "bytes": length, "nonce": nonce.hex()})
            expected.append(hashlib.sha256(nonce + chunk).hexdigest())
        public = {"id": identifier, "generacion": generation, "disco": disk, "pruebas": items}
        return {  # published only once the state that holds it is saved (E4C3-09)
            "id": identifier,
            "recibo": receipt,
            "ejecucion": self.state["ejecucion"],
            "esperadas": expected,
            "publico": public,
        }

    # -- answers ------------------------------------------------------------------------
    def _answer(
        self,
        conn: psycopg.Connection,
        document: Mapping[str, Any],
        states: dict[str, set[str]],
        report: Report,
    ) -> None:
        disk = _disk(document["disco"])
        generation = str(uuid.UUID(str(document["generacion"])))
        pair = self.state["pares"].get(f"{generation}|{disk}")
        challenge = pair.get("desafio") if pair else None
        if not challenge or challenge["id"] != document.get("desafio"):
            raise Rejected("no such pending challenge for this generation and disk")
        if not pair["valido"] or challenge["recibo"] != pair["recibo"]:
            raise Rejected("the challenge is not bound to the last valid receipt")
        if challenge["ejecucion"] >= self.state["ejecucion"]:
            raise Rejected("answered in the same run that issued the challenge")
        if [str(h) for h in document["sha256"]] != challenge["esperadas"]:
            raise Rejected("the bytes read are not the server's")
        for state in ("replicado_fuera", "verificado_fuera"):
            if state not in states.get(generation, set()):
                insert_state(conn, generation, state)
                states.setdefault(generation, set()).add(state)
        pair["verificado"] = True
        pair["desafio"] = None  # withdrawn once that is saved
        report.verified.append(generation)

    # -- the recoverable point (E4C3-02, E4C3-03, E4C3-08, E4C3-10 and E4C3-14) --------------
    def _point(self, document: Mapping[str, Any], report: Report) -> None:
        """The disk's observation of its recoverable point in one visit. The PC numbers its
        observations (``secuencia``), above the last one the server accepted; a newer one
        replaces the point, even with an older hot copy or with none (``copia`` null), and
        an older one is refused. Nothing of the disk changes unless it is accepted whole."""
        disk = _disk(document["disco"])
        record = self.state["discos"].get(disk)
        if record is None:
            raise NotAdmitted(f"disk {disk} not admitted")
        sequence = document["secuencia"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise Rejected("the observation has no valid sequence number")
        if sequence <= int(record.get("observacion", 0)):
            raise Rejected(
                f"an observation ({sequence}) not newer than the last accepted one"
                f" ({record.get('observacion', 0)})"
            )
        name = document["copia"]
        point = None if name is None else self._hot_point(str(name), document)
        now = self.clock().isoformat()
        record["observacion"] = sequence
        record["punto"] = point
        record["ultima_visita"] = now
        if document.get("sin_errores") is True:
            record["ultima_sin_errores"] = now
        if point is not None:
            report.points.append(point["copia"])

    def _hot_point(self, name: str, document: Mapping[str, Any]) -> dict[str, str]:
        entry = register_entries(self.copies).get(name)
        manifest_bytes = base64.b64decode(str(document["manifiesto_base64"]), validate=True)
        if entry is None or not name.startswith(HOT_PREFIX):
            raise Rejected(f"{name} is not a hot copy of the register")
        if (str(document["sha256"]), str(document["sha256_manifiesto"])) != (
            entry.get("sha256_cifrado"),
            entry.get("sha256_manifiesto"),
        ):
            raise Rejected(f"{name}: its SHA-256 are not the ones of the register")
        if hashlib.sha256(manifest_bytes).hexdigest() != entry.get("sha256_manifiesto"):
            raise Rejected(f"{name}: the manifest is not the one of the register")
        manifest = json.loads(manifest_bytes)
        if manifest.get("nombre") != name:
            raise Rejected(f"{name}: the manifest names another copy")
        fixed = {
            (
                str(item["copia_de_dia"]["copia"]),
                str(item["copia_de_dia"]["sha256"]),
                str(item["copia_de_dia"]["sha256_manifiesto"]),
            )
            for item in manifest.get("dias_excluidos", [])
            if item.get("copia_de_dia") is not None
        }
        held = {
            (str(d["copia"]), str(d["sha256"]), str(d["sha256_manifiesto"]))
            for d in document.get("dependencias", [])
        }
        if held != fixed:
            raise Rejected(f"{name}: the disk does not hold exactly the dependencies it fixes")
        return {"copia": name, "hora": copy_time(name).isoformat()}


@contextmanager
def exclusive(directory: Path) -> Iterator[None]:
    """A run and a retirement never write the state at once: the second one waits (a lock
    the system releases when the process ends)."""
    import fcntl  # only on the server, which is Linux

    descriptor = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import pwd  # only on the server, which is Linux

    import structlog

    from kbot.db.roles import ARCHIVE, DATABASE, SOCKET_DIR

    log = structlog.get_logger()
    parser = argparse.ArgumentParser(description="the external disk (stage E4c-3)")
    parser.add_argument("command", choices=("run", "retirar-disco"))
    parser.add_argument("disk", nargs="?")
    arguments = parser.parse_args(argv)
    dsn = os.environ.get("KBOT_ARCHIVE_DSN", f"host={SOCKET_DIR} dbname={DATABASE} user={ARCHIVE}")

    def connect() -> psycopg.Connection:
        return psycopg.connect(dsn, autocommit=True)

    verifier = Verifier(connect, owner=pwd.getpwnam("kbot_sync").pw_uid)
    if arguments.command == "retirar-disco":
        if not arguments.disk:
            parser.error("retirar-disco needs the identifier of the disk")
        with exclusive(BASE / "privado"):
            verifier.retire_disk(arguments.disk)
        log.info("disk_retired", disk=arguments.disk)
        return 0
    with exclusive(BASE / "privado"):
        report = verifier.run()
    for generation in report.accepted:
        log.info("receipt_accepted", generation=generation)
    for generation in report.verified:
        log.info("generation_verified_outside", generation=generation)
    for copy in report.points:
        log.info("recoverable_point", copy=copy)
    for problem in report.rejected:
        log.warning("rejected", problem=problem)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
