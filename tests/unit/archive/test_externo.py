"""The verification of the external disk without a database (02 §13.1, point 4, E4c-3,
points 2, 4 and 7): a fake catalog stands for the three queries of the verifier."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from kbot.archive import externo
from kbot.upload import durable

GENERATION = str(uuid.UUID("1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed"))
DAY = date(2026, 11, 22)
DISK = "a" * 32
OTHER = "b" * 32
THIRD = "c" * 32
NOW = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)
POINT = f"recibo_punto_{DISK}.json"  # the observation of the disk, in its own file


class FakeConn:
    """The catalog of the archive, as the verifier queries it."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.states: dict[str, set[str]] = {GENERATION: {"archivado_local"}}
        self.inserted: list[tuple[str, str]] = []

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        if query.startswith("SELECT g.generation_id, g.day"):
            return _Rows(
                [
                    (GENERATION, DAY, path, len(data), 7, hashlib.sha256(data).hexdigest())
                    for path, data in sorted(self.files.items())
                ]
            )
        if query.startswith("SELECT generation_id, state"):
            return _Rows([(g, s) for g, states in self.states.items() for s in states])
        if query.startswith("INSERT INTO market.archive_generation_states"):
            generation, state = params
            self.states.setdefault(generation, set()).add(state)
            self.inserted.append((generation, state))
            return _Rows([])
        raise AssertionError(query)

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _Rows(list):  # type: ignore[type-arg]
    def fetchall(self) -> list[Any]:
        return list(self)


class Setup:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "archive"
        self.base = tmp_path / "externo"
        self.receipts = tmp_path / "recibos"
        self.copies = tmp_path / "backups"
        for d in (self.base / "publico", self.base / "privado", self.receipts, self.copies):
            d.mkdir(parents=True)
        self.files = {
            f"market/{DAY}/g{GENERATION}/candles.parquet": bytes(range(256)) * 40,
            f"market/{DAY}/g{GENERATION}/depth_metrics.parquet": b"x" * 100,
        }
        for path, data in self.files.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_bytes(data)
        (self.copies / externo.REGISTER).write_text("", encoding="utf-8")
        self.conn = FakeConn(self.files)
        self.now = NOW
        self.ops: durable.FileOps = durable.REAL

    def verifier(self) -> externo.Verifier:
        return externo.Verifier(
            lambda: self.conn,  # type: ignore[arg-type,return-value]
            base=self.base,
            archive_root=self.root,
            receipts=self.receipts,
            copies=self.copies,
            owner=None,
            clock=lambda: self.now,
            ops=self.ops,
        )

    def run(self) -> externo.Report:
        return self.verifier().run()

    def send(self, name: str, document: dict[str, Any]) -> None:
        (self.receipts / name).write_text(json.dumps(document), encoding="utf-8")

    def receipt(self, disk: str = DISK, files: dict[str, bytes] | None = None) -> dict[str, Any]:
        files = self.files if files is None else files
        return {
            "tipo": "generacion",
            "generacion": GENERATION,
            "dia": DAY.isoformat(),
            "disco": disk,
            "archivos": [
                {"ruta": p, "bytes": len(d), "filas": 7, "sha256": hashlib.sha256(d).hexdigest()}
                for p, d in sorted(files.items())
            ],
        }

    def challenge(self) -> dict[str, Any]:
        [path] = sorted(self.base.joinpath("publico").glob("desafio_*.json"))
        return json.loads(path.read_text(encoding="utf-8"))

    def answer(
        self, challenge: dict[str, Any], files: dict[str, bytes] | None = None
    ) -> dict[str, Any]:
        """What the PC answers, reading its own copy of the files."""
        files = self.files if files is None else files
        hashes = []
        for item in challenge["pruebas"]:
            data = files[item["ruta"]][item["desde"] : item["desde"] + item["bytes"]]
            hashes.append(hashlib.sha256(bytes.fromhex(item["nonce"]) + data).hexdigest())
        return {
            "tipo": "respuesta",
            "desafio": challenge["id"],
            "generacion": challenge["generacion"],
            "disco": challenge["disco"],
            "sha256": hashes,
        }

    def state(self) -> dict[str, Any]:
        return json.loads((self.base / "privado" / "estado.json").read_text(encoding="utf-8"))


def test_the_index_holds_every_generation_with_its_exact_files(tmp_path: Path) -> None:
    s = Setup(tmp_path)
    s.run()
    published = json.loads((s.base / "publico" / "generaciones.json").read_text(encoding="utf-8"))
    assert published == [
        {
            "generacion": GENERATION,
            "dia": DAY.isoformat(),
            "verificada_en_discos": [],
            "archivos": [
                {"ruta": p, "bytes": len(d), "filas": 7, "sha256": hashlib.sha256(d).hexdigest()}
                for p, d in sorted(s.files.items())
            ],
        }
    ]


def test_a_receipt_then_an_answer_in_a_later_run_verify_the_generation(tmp_path: Path) -> None:
    s = Setup(tmp_path)
    s.send(f"recibo_{GENERATION}.json", s.receipt())
    report = s.run()
    assert report.accepted == [GENERATION] and s.conn.inserted == [(GENERATION, "replicado_fuera")]
    challenge = s.challenge()
    assert challenge["disco"] == DISK and len(challenge["pruebas"]) == 2
    assert "esperadas" not in json.dumps(challenge)  # the expected answers stay private
    private = s.state()["pares"][f"{GENERATION}|{DISK}"]["desafio"]
    assert len(private["esperadas"]) == 2
    s.send(f"respuesta_{challenge['id']}.json", s.answer(challenge))
    assert s.run().verified == [GENERATION]
    assert s.conn.inserted[-1] == (GENERATION, "verificado_fuera")
    assert not list((s.base / "publico").glob("desafio_*.json"))  # done: withdrawn


def test_an_answer_in_the_run_that_issued_the_challenge_does_not_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two barriers: the listing of recibos/ is taken once, at the start, so an answer that
    arrives while a run issues its challenge is not even seen; and an answer processed in the
    run that issued its challenge is refused."""
    s = Setup(tmp_path)
    s.send(f"recibo_{GENERATION}.json", s.receipt())
    verifier = s.verifier()
    original = verifier._challenge

    def answer_at_once(*args: Any) -> dict[str, Any]:
        issued = original(*args)
        s.send("respuesta_ya.json", s.answer(issued["publico"]))
        return issued

    monkeypatch.setattr(verifier, "_challenge", answer_at_once)
    report = verifier.run()
    assert report.rejected == [] and (GENERATION, "verificado_fuera") not in s.conn.inserted
    # The second barrier: the challenge counted as issued in the run that reads the answer.
    state = s.state()
    state["pares"][f"{GENERATION}|{DISK}"]["desafio"]["ejecucion"] = state["ejecucion"] + 1
    (s.base / "privado" / "estado.json").write_text(json.dumps(state), encoding="utf-8")
    assert any("same run" in r for r in s.run().rejected)
    assert (GENERATION, "verificado_fuera") not in s.conn.inserted


def test_an_answer_with_the_bytes_of_another_position_is_rejected(tmp_path: Path) -> None:
    """The answer must hash the bytes at the very offset the challenge names: the bytes of
    another position of the same file are refused, and the challenge stays pending."""
    s = Setup(tmp_path)
    s.send(f"recibo_{GENERATION}.json", s.receipt())
    s.run()
    challenge = s.challenge()
    shifted = json.loads(json.dumps(challenge))
    [item] = [i for i in shifted["pruebas"] if i["ruta"].endswith("candles.parquet")]
    item["desde"] = item["desde"] + 1 if item["desde"] == 0 else item["desde"] - 1
    s.send(f"respuesta_{challenge['id']}.json", s.answer(shifted))
    assert any("not the server's" in r for r in s.run().rejected)
    assert (GENERATION, "verificado_fuera") not in s.conn.inserted
    assert s.challenge()["id"] == challenge["id"]  # still pending, still the same
    s.send(f"respuesta_{challenge['id']}.json", s.answer(challenge))
    assert s.run().verified == [GENERATION]


def test_a_verified_generation_whose_last_receipt_fails_is_checked_again_in_each_visit(
    tmp_path: Path,
) -> None:
    """The index lists a disk under ``verificada_en_discos`` only while its last receipt
    holds, so after a failure the PC proves that generation again in every visit."""
    s = Setup(tmp_path)
    s.send(f"recibo_{GENERATION}.json", s.receipt())
    s.run()
    challenge = s.challenge()
    s.send(f"respuesta_{challenge['id']}.json", s.answer(challenge))
    s.run()

    def listed() -> list[str]:
        [entry] = json.loads((s.base / "publico" / "generaciones.json").read_text("utf-8"))
        return list(entry["verificada_en_discos"])

    s.run()
    assert listed() == [DISK]
    corrupted = dict(s.files)
    path = f"market/{DAY}/g{GENERATION}/depth_metrics.parquet"
    corrupted[path] = b"y" + s.files[path][1:]
    s.send(f"recibo_{GENERATION}.json", s.receipt(files=corrupted))
    s.run()
    s.run()  # the index of the next run
    assert listed() == []
    s.send(f"recibo_{GENERATION}.json", s.receipt())  # repaired
    s.run()
    s.run()
    assert listed() == [DISK]
    assert not list((s.base / "publico").glob("desafio_*.json"))  # nothing new to prove


def test_a_receipt_with_other_bytes_or_missing_a_file_is_rejected(tmp_path: Path) -> None:
    s = Setup(tmp_path)
    changed = dict(s.files)
    changed[next(iter(changed))] = b"other"
    s.send("recibo_cambiado.json", s.receipt(files=changed))
    report = s.run()
    assert report.rejected and s.conn.inserted == []
    missing = dict(list(s.files.items())[:1])
    s.send("recibo_cambiado.json", s.receipt(files=missing))
    assert s.run().rejected and s.conn.inserted == []


def test_corruption_after_a_valid_receipt_annuls_the_challenge_until_it_is_repaired(
    tmp_path: Path,
) -> None:
    """E4C3-01 and its residual: A valid, then B with a byte changed outside the challenged
    range, then A again after the repair, with restarts between the steps. The old answer
    never verifies; after the repair, one new challenge and its answer do."""
    s = Setup(tmp_path)
    s.send(f"recibo_{GENERATION}.json", s.receipt())
    s.run()
    first = s.challenge()
    old_answer = s.answer(first)  # the bytes of the challenged range are still right
    corrupted = dict(s.files)
    path = f"market/{DAY}/g{GENERATION}/depth_metrics.parquet"
    corrupted[path] = b"y" + s.files[path][1:]
    s.send(f"recibo_{GENERATION}.json", s.receipt(files=corrupted))
    assert s.run().rejected
    assert not list((s.base / "publico").glob("desafio_*.json"))
    s.send(f"respuesta_{first['id']}.json", old_answer)
    assert any("no such pending challenge" in r for r in s.run().rejected)
    assert (GENERATION, "verificado_fuera") not in s.conn.inserted
    # Repaired: the receipt is A again, already seen, and it still counts.
    s.send(f"recibo_{GENERATION}.json", s.receipt())
    s.run()
    second = s.challenge()
    assert second["id"] != first["id"]
    # A → A: the same receipt again changes nothing, not even the challenge.
    s.send(f"recibo_{GENERATION}.json", s.receipt())
    (s.base / "privado" / "estado.json").touch()
    s.run()
    assert s.challenge()["id"] == second["id"]
    s.send(f"respuesta_{second['id']}.json", s.answer(second))
    assert s.run().verified == [GENERATION]


def test_at_most_two_disks_and_bounded_output_for_repeated_receipts(tmp_path: Path) -> None:
    """E4C3-04: repeating a receipt issues nothing new; a third disk is refused until one is
    retired; what the server keeps grows with the pairs, not with the receipts."""
    s = Setup(tmp_path)
    for disk in (DISK, OTHER):
        s.send(f"recibo_{disk}.json", s.receipt(disk=disk))
    s.run()
    assert len(list((s.base / "publico").glob("desafio_*.json"))) == 2
    for _ in range(5):  # retransmissions and restarts
        for disk in (DISK, OTHER):
            s.send(f"recibo_{disk}.json", s.receipt(disk=disk))
        s.run()
    assert len(list((s.base / "publico").glob("desafio_*.json"))) == 2
    assert len(s.state()["pares"]) == 2
    s.send("recibo_tercero.json", s.receipt(disk=THIRD))
    assert any("not admitted" in r for r in s.run().rejected)
    assert s.run().rejected == []  # the same file, untouched: not read again
    s.verifier().retire_disk(OTHER)
    assert len(list((s.base / "publico").glob("desafio_*.json"))) == 1
    # E4C3-12: the very same file of the third disk, not even sent again, is admitted now.
    assert s.run().accepted == [GENERATION]
    assert set(s.state()["discos"]) == {DISK, THIRD}
    assert len(list((s.base / "publico").glob("desafio_*.json"))) == 2
    s.send("recibo_tercero.json", s.receipt(disk=THIRD))  # the same bytes written again
    assert s.run().accepted == []  # an accepted receipt repeated is still idempotent
    assert len(list((s.base / "publico").glob("desafio_*.json"))) == 2


def manifest_of(name: str, dependencies: list[dict[str, str]]) -> bytes:
    return json.dumps(
        {
            "nombre": name,
            "dias_excluidos": [
                {"dia": "2026-11-22", "copia_de_dia": d, "generacion": GENERATION}
                for d in dependencies
            ],
        },
        indent=1,
    ).encode()


def register(s: Setup, name: str, dump: bytes, manifest: bytes) -> None:
    entry = {
        "copia": name,
        "sha256_claro": "c" * 64,
        "sha256_cifrado": hashlib.sha256(dump).hexdigest(),
        "sha256_manifiesto": hashlib.sha256(manifest).hexdigest(),
    }
    with (s.copies / externo.REGISTER).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def point(
    name: str, dump: bytes, manifest: bytes, held: list[dict[str, str]], sequence: int = 1
) -> dict[str, Any]:
    return {
        "tipo": "punto",
        "disco": DISK,
        "secuencia": sequence,
        "copia": name,
        "sha256": hashlib.sha256(dump).hexdigest(),
        "sha256_manifiesto": hashlib.sha256(manifest).hexdigest(),
        "manifiesto_base64": base64.b64encode(manifest).decode(),
        "dependencias": held,
        "sin_errores": True,
    }


def test_the_recoverable_point_is_authenticated_by_the_register(tmp_path: Path) -> None:
    """E4C3-02, E4C3-08 and E4C3-14: the manifest comes in the receipt and is authenticated
    by the register, so the copy may already be gone from the server; the disk must hold
    exactly the dependencies; a refused observation changes nothing of the disk, not even
    its last sync without errors."""
    s = Setup(tmp_path)
    s.send(f"recibo_{GENERATION}.json", s.receipt())  # admits the disk
    s.run()
    s1 = {"copia": "dia-2026-11-22", "sha256": "1" * 64, "sha256_manifiesto": "2" * 64}
    h = "caliente-20261127T004139Z"
    dump, manifest = b"hot", manifest_of(h, [s1])
    register(s, h, dump, manifest)  # the copy itself is not in the backups: retention took it
    s.send(POINT, point(h, dump, manifest, [s1]))
    assert s.run().points == [h]
    record = s.state()["discos"][DISK]
    assert record["punto"] == {"copia": h, "hora": "2026-11-27T00:41:39+00:00"}
    assert record["ultima_sin_errores"] == NOW.isoformat()
    # An altered manifest, other dependencies, other SHA-256 or an unknown copy are refused,
    # and leave the disk as it was (E4C3-14).
    s.now = NOW + timedelta(hours=1)
    altered = manifest.replace(b"2026-11-22", b"2026-11-23")
    s.send(
        POINT,
        point(h, dump, altered, [s1], 2)
        | {"sha256_manifiesto": hashlib.sha256(manifest).hexdigest()},
    )
    assert any("not the one of the register" in r for r in s.run().rejected)
    s2 = dict(s1, sha256="3" * 64)  # the same day with another identity (S1 / S2)
    s.send(POINT, point(h, dump, manifest, [s2], 3))
    assert any("dependencies" in r for r in s.run().rejected)
    s.send(POINT, point(h, b"other", manifest, [s1], 4))
    assert any("not the ones of the register" in r for r in s.run().rejected)
    s.send(POINT, point("caliente-20261128T004000Z", dump, manifest, [s1], 5))
    assert any("not a hot copy of the register" in r for r in s.run().rejected)
    assert s.state()["discos"][DISK] == record


def test_the_point_follows_the_disk_and_never_an_older_observation(tmp_path: Path) -> None:
    """E4C3-10: H27 complete, then its dependency lost and H20 the newest complete one, then
    none complete: the server keeps each situation, also going back or to none. An older
    observation sent again is refused, and changes nothing."""
    s = Setup(tmp_path)
    s.send(f"recibo_{GENERATION}.json", s.receipt())  # admits the disk
    s.run()
    d27 = {"copia": "dia-2026-11-22", "sha256": "1" * 64, "sha256_manifiesto": "2" * 64}
    h27, h20 = "caliente-20261127T004000Z", "caliente-20261120T004000Z"
    dump27, manifest27 = b"hot 27", manifest_of(h27, [d27])
    dump20, manifest20 = b"hot 20", manifest_of(h20, [])
    register(s, h27, dump27, manifest27)
    register(s, h20, dump20, manifest20)

    def observed() -> dict[str, Any] | None:
        return s.state()["discos"][DISK]["punto"]  # type: ignore[no-any-return]

    first = point(h27, dump27, manifest27, [d27], 1)
    s.send(POINT, first)
    s.run()
    assert observed() == {"copia": h27, "hora": "2026-11-27T00:40:00+00:00"}
    s.send(POINT, point(h20, dump20, manifest20, [], 2))  # d27 lost
    s.run()
    assert observed() == {"copia": h20, "hora": "2026-11-20T00:40:00+00:00"}
    s.send(POINT, point(h20, dump20, manifest20, [], 3) | {"copia": None})
    s.run()
    assert observed() is None  # nothing complete on the disk
    public = json.loads((s.base / "publico" / "discos.json").read_text(encoding="utf-8"))
    sent = hashlib.sha256((s.receipts / POINT).read_bytes()).hexdigest()
    assert public == {DISK: {"observacion": 3, "sha256": sent}}  # E4C3-16: what was accepted
    s.send(POINT, first)  # an old observation, byte for byte
    assert any("not newer" in r for r in s.run().rejected)
    assert observed() is None
    # Each disk sends its observation in its own file, which the PC reads back (E4C3-16).
    s.send("recibo_punto_" + OTHER + ".json", point(h20, dump20, manifest20, [], 9))
    assert any("goes in" in r for r in s.run().rejected)
    assert observed() is None


def test_the_rpo_of_the_disk_is_the_age_of_its_point_and_not_of_the_visit(
    tmp_path: Path,
) -> None:
    """E4C3-03: the hot copy of 00:40 copied to the disk at 23:00 gives 22 h 20 min, not
    zero; a later visit without a newer complete hot copy moves the last visit and the last
    sync without errors, never the point."""
    s = Setup(tmp_path)
    s.now = datetime(2026, 11, 27, 23, 0, tzinfo=UTC)
    s.send(f"recibo_{GENERATION}.json", s.receipt())  # admits the disk
    s.run()
    h = "caliente-20261127T004000Z"
    dump, manifest = b"hot", manifest_of(h, [])
    register(s, h, dump, manifest)
    s.send(POINT, point(h, dump, manifest, []))
    s.run()
    record = s.state()["discos"][DISK]
    assert s.now - datetime.fromisoformat(record["punto"]["hora"]) == timedelta(
        hours=22, minutes=20
    )
    assert record["ultima_visita"] == record["ultima_sin_errores"] == s.now.isoformat()
    s.now += timedelta(days=1)
    s.send(POINT, point(h, dump, manifest, [], 2) | {"sin_errores": False})
    s.run()
    later = s.state()["discos"][DISK]
    assert later["punto"] == record["punto"]
    assert later["ultima_visita"] == s.now.isoformat()
    assert later["ultima_sin_errores"] == record["ultima_sin_errores"]  # that visit had errors


class StateFails(durable.FileOps):
    """The private state cannot be written; everything else can."""

    def write(self, path: Path, data: bytes) -> None:
        if path.name == ".estado.json.tmp":
            raise OSError(28, "No space left on device")
        super().write(path, data)


def published(s: Setup) -> tuple[set[str], dict[str, Any]]:
    challenges = {p.name for p in (s.base / "publico").glob("desafio_*.json")}
    acknowledged = json.loads((s.base / "publico" / "procesados.json").read_text("utf-8"))
    return challenges, acknowledged


def test_nothing_is_announced_before_the_state_that_holds_it_is_saved(tmp_path: Path) -> None:
    """E4C3-09: while the state cannot be saved, no challenge, withdrawal or acknowledgement
    is published, however many runs; once it can, the runs go on from the saved state, and
    an invalidation is never lost: the old answer is refused."""
    s = Setup(tmp_path)
    s.send(f"recibo_{GENERATION}.json", s.receipt())
    s.run()
    challenge = s.challenge()
    before = published(s)
    s.ops = StateFails()
    for n in range(3):  # three runs with new receipts of another disk
        s.send("recibo_otro.json", s.receipt(disk=OTHER) | {"visita": n})
        with pytest.raises(durable.PersistenceError):
            s.run()
        assert published(s) == before  # no orphan challenge, no acknowledgement
    # The disk goes bad: its new receipt annuls the challenge, but the state is not saved.
    corrupted = dict(s.files)
    path = f"market/{DAY}/g{GENERATION}/depth_metrics.parquet"
    corrupted[path] = b"y" + s.files[path][1:]
    s.send(f"recibo_{GENERATION}.json", s.receipt(files=corrupted))
    old_answer = s.answer(challenge)
    s.send(f"respuesta_{challenge['id']}.json", old_answer)
    with pytest.raises(durable.PersistenceError):
        s.run()
    challenges, acknowledged = published(s)
    assert challenges == before[0]
    receipt = (s.receipts / f"recibo_{GENERATION}.json").read_bytes()
    assert acknowledged.get(f"recibo_{GENERATION}.json") != hashlib.sha256(receipt).hexdigest()
    assert f"respuesta_{challenge['id']}.json" not in acknowledged  # the PC keeps both
    # The state can be saved again (a restart): the invalidation comes first, and the old
    # answer is refused.
    s.ops = durable.REAL
    report = s.run()
    assert any("no such pending challenge" in r for r in report.rejected), report
    assert (GENERATION, "verificado_fuera") not in s.conn.inserted
    challenges, acknowledged = published(s)
    disks = [json.loads((s.base / "publico" / c).read_text("utf-8"))["disco"] for c in challenges]
    assert disks == [OTHER]  # the annulled challenge withdrawn, the other disk's published
    assert acknowledged[f"recibo_{GENERATION}.json"] == hashlib.sha256(receipt).hexdigest()


class PrivateStepFails(durable.FileOps):
    """One step of the durable write of the private state fails, every time."""

    def __init__(self, step: str) -> None:
        self.step = step

    def _private(self, path: Path) -> bool:
        return "privado" in path.parts

    def write(self, path: Path, data: bytes) -> None:
        if self.step == "write" and self._private(path):
            raise OSError(5, "Input/output error")
        super().write(path, data)

    def fsync_file(self, path: Path) -> None:
        if self.step == "fsync_file" and self._private(path):
            raise OSError(5, "Input/output error")
        super().fsync_file(path)

    def rename(self, source: Path, target: Path) -> None:
        if self.step == "rename" and self._private(target):
            raise OSError(5, "Input/output error")
        super().rename(source, target)

    def fsync_directory(self, directory: Path) -> None:
        if self.step == "fsync_directory" and self._private(directory / "x"):
            raise OSError(5, "Input/output error")
        super().fsync_directory(directory)


@pytest.mark.parametrize("step", ["write", "fsync_file", "rename", "fsync_directory"])
def test_no_effect_comes_from_a_state_whose_persistence_is_not_confirmed(
    tmp_path: Path, step: str
) -> None:
    """Residual of E4C3-09: whichever step of the durable write fails, the last fsync of
    the directory included (the new state then visible but not durable), no run and no
    restart announces anything from that state. Once the disk works, the next run confirms
    the state and only then announces it."""
    s = Setup(tmp_path)
    s.ops = PrivateStepFails(step)
    s.send(f"recibo_{GENERATION}.json", s.receipt())
    for _ in range(3):  # the run, then restarts
        with pytest.raises(durable.PersistenceError):
            s.run()
        assert not list((s.base / "publico").glob("desafio_*.json"))
        acknowledged = s.base / "publico" / "procesados.json"
        assert not acknowledged.exists() or json.loads(acknowledged.read_text("utf-8")) == {}
    s.ops = durable.REAL
    s.run()
    assert len(list((s.base / "publico").glob("desafio_*.json"))) == 1
    assert f"recibo_{GENERATION}.json" in published(s)[1]


def test_a_challenge_that_could_not_be_published_is_published_by_the_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E4C3-09: a failure while publishing, after the state was saved, is mended at the start
    of the next run, from the saved state."""
    s = Setup(tmp_path)
    s.send(f"recibo_{GENERATION}.json", s.receipt())
    original = externo.Verifier._publish
    failed = []

    def fails_once(self: externo.Verifier, name: str, value: Any) -> None:
        if name.startswith("desafio_") and not failed:
            failed.append(name)
            raise durable.PersistenceError(name)
        original(self, name, value)

    monkeypatch.setattr(externo.Verifier, "_publish", fails_once)
    with pytest.raises(durable.PersistenceError):
        s.run()
    assert not list((s.base / "publico").glob("desafio_*.json"))
    pending = s.state()["pares"][f"{GENERATION}|{DISK}"]["desafio"]  # saved before publishing
    s.run()
    assert s.challenge()["id"] == pending["id"]
    assert f"recibo_{GENERATION}.json" in published(s)[1]


def test_a_queue_longer_than_the_reads_of_one_run_moves_on_without_the_pc(
    tmp_path: Path,
) -> None:
    """E4C3-13: 1,000 receipts and the receipt of the point. The first run reads the 1,000;
    the next one skips them by their stamp, without reading them, and reads the point. No
    file is deleted in between."""
    s = Setup(tmp_path)
    receipt = s.receipt()
    for n in range(externo.PER_RUN):
        s.send(f"recibo_{n:04d}.json", receipt)
    h = "caliente-20261127T004000Z"
    dump, manifest = b"hot", manifest_of(h, [])
    register(s, h, dump, manifest)
    s.send(POINT, point(h, dump, manifest, []))
    first = s.run()
    assert first.accepted == [GENERATION] and first.points == []
    second = s.run()
    assert second.points == [h] and second.rejected == []
    assert len(json.loads((s.base / "publico" / "procesados.json").read_text("utf-8"))) == (
        externo.PER_RUN + 1
    )
    assert s.run() == externo.Report(published=True)  # nothing left to read


def test_processed_files_are_published_for_the_pc_and_forgotten_after_30_days(
    tmp_path: Path,
) -> None:
    s = Setup(tmp_path)
    s.send(f"recibo_{GENERATION}.json", s.receipt())
    s.run()
    processed = json.loads((s.base / "publico" / "procesados.json").read_text(encoding="utf-8"))
    content = (s.receipts / f"recibo_{GENERATION}.json").read_bytes()
    assert processed == {f"recibo_{GENERATION}.json": hashlib.sha256(content).hexdigest()}
    (s.receipts / f"recibo_{GENERATION}.json").unlink()  # the PC deletes it
    s.now = NOW + timedelta(days=31)
    s.run()
    assert json.loads((s.base / "publico" / "procesados.json").read_text(encoding="utf-8")) == {}


def test_sizes_kinds_and_the_eligibility_that_is_never_inserted(tmp_path: Path) -> None:
    s = Setup(tmp_path)
    (s.receipts / "grande.json").write_bytes(b" " * (externo.RECEIPT_LIMIT + 1))
    s.send("raro.json", {"tipo": "otro"})
    (s.receipts / "roto.json").write_text("{no", encoding="utf-8")
    report = s.run()
    assert len(report.rejected) == 3
    with pytest.raises(ValueError, match="never inserts"):
        externo.insert_state(s.conn, GENERATION, "elegible_para_exclusion_backup")  # type: ignore[arg-type]
    assert all(state != "elegible_para_exclusion_backup" for _, state in s.conn.inserted)


@pytest.mark.skipif(sys.platform == "win32", reason="links and owners as on the server (Linux)")
def test_links_directories_and_files_of_another_owner_are_never_read(tmp_path: Path) -> None:
    s = Setup(tmp_path)
    secret = s.base / "privado" / "secreto.json"
    secret.write_text(json.dumps(s.receipt()), encoding="utf-8")
    (s.receipts / "enlace.json").symlink_to(secret)
    (s.receipts / "carpeta.json").mkdir()
    report = s.run()
    assert len(report.rejected) == 2 and s.conn.inserted == []
    s.send(f"recibo_{GENERATION}.json", s.receipt())
    verifier = s.verifier()
    verifier.owner = os.getuid() + 1  # type: ignore[attr-defined]
    assert any("not a file of kbot_sync" in r for r in verifier.run().rejected)


@pytest.mark.skipif(sys.platform == "win32", reason="the lock is the server's, Linux")
def test_a_run_and_a_retirement_never_write_the_state_at_once(tmp_path: Path) -> None:
    import threading

    order: list[str] = []
    inside = threading.Event()
    release = threading.Event()

    def first() -> None:
        with externo.exclusive(tmp_path):
            order.append("run begins")
            inside.set()
            release.wait(5)
            order.append("run ends")

    thread = threading.Thread(target=first)
    thread.start()
    assert inside.wait(5)

    def second() -> None:
        with externo.exclusive(tmp_path):
            order.append("retirement")

    waiting = threading.Thread(target=second)
    waiting.start()
    waiting.join(0.3)
    assert waiting.is_alive()  # it waits for the run
    release.set()
    thread.join(5)
    waiting.join(5)
    assert order == ["run begins", "run ends", "retirement"]
