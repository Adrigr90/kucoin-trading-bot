"""The reservation of the validation: atomic, persistent, before reading, and never deleted
(``02`` §12.5.1 and §12.5.6; F3E5-01)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kbot.backtest.reservation import (
    COMPLETED,
    COPY_FORMAT,
    FAILED,
    STARTED,
    AlreadyReserved,
    finish,
    reservation_path,
    reserve,
    validation_query,
)

AT = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
VERSION = "a" * 40 + "+" + "b" * 64


def clock() -> datetime:
    return AT


def test_a_reservation_is_created_once(tmp_path: Path) -> None:
    path = reserve(tmp_path / "validacion", "tendencia", VERSION, AT)
    assert path == reservation_path(tmp_path / "validacion", "tendencia", VERSION)
    content = json.loads(path.read_text(encoding="utf-8"))
    assert (content["estado"], content["iniciada"]) == (STARTED, AT.isoformat())
    with pytest.raises(AlreadyReserved):
        reserve(tmp_path / "validacion", "tendencia", VERSION, AT)
    # Another family or another version is another reservation.
    reserve(tmp_path / "validacion", "breakout", VERSION, AT)
    reserve(tmp_path / "validacion", "tendencia", "c" * 40 + "+" + "b" * 64, AT)


def test_any_state_prevents_another_query_even_a_half_written_one(tmp_path: Path) -> None:
    folder = tmp_path / "validacion"
    path = reserve(folder, "tendencia", VERSION, AT)
    finish(path, COMPLETED, AT + timedelta(hours=1), result={"operaciones": 3})
    failed = reserve(folder, "breakout", VERSION, AT)
    finish(failed, FAILED, AT, error="RuntimeError: disco")
    folder.joinpath(reservation_path(folder, "reversion_media", VERSION).name).write_bytes(b"")
    for family in ("tendencia", "breakout", "reversion_media"):
        with pytest.raises(AlreadyReserved):
            reserve(folder, family, VERSION, AT)
    with pytest.raises(ValueError, match="already ended"):
        finish(path, FAILED, AT)


def test_f3e5_01_a_failure_after_reading_leaves_it_failed_and_blocks_another(
    tmp_path: Path,
) -> None:
    folder, copy = tmp_path / "validacion", tmp_path / "docs" / "consultas.json"
    read: list[str] = []
    with (
        pytest.raises(RuntimeError, match="disco"),
        validation_query(folder, "tendencia", VERSION, clock, copy),
    ):
        read.append("primer minuto")
        raise RuntimeError("disco")
    path = reservation_path(folder, "tendencia", VERSION)
    content = json.loads(path.read_text(encoding="utf-8"))
    assert (content["estado"], content["error"]) == (FAILED, "RuntimeError: disco")
    held = json.loads(copy.read_text(encoding="utf-8"))
    assert held["formato"] == COPY_FORMAT
    assert [(e["familia"], e["estado"]) for e in held["consultas"]] == [("tendencia", FAILED)]
    with (
        pytest.raises(AlreadyReserved),
        validation_query(folder, "tendencia", VERSION, clock, copy),
    ):
        read.append("otra vez")
    assert read == ["primer minuto"]
    assert path.exists()


def test_a_completed_query_keeps_its_result_and_its_copy(tmp_path: Path) -> None:
    folder, copy = tmp_path / "validacion", tmp_path / "consultas.json"
    with validation_query(folder, "breakout", VERSION, clock, copy) as query:
        query.result = {"operaciones": 2, "suma": 1.5}
    assert query.record["estado"] == COMPLETED
    assert query.record["resultado"] == {"operaciones": 2, "suma": 1.5}
    with validation_query(folder, "tendencia", VERSION, clock, copy) as query:
        query.result = {"operaciones": 0}
    entries = json.loads(copy.read_text(encoding="utf-8"))["consultas"]
    assert [(e["familia"], e["estado"]) for e in entries] == [
        ("breakout", COMPLETED),
        ("tendencia", COMPLETED),
    ]
    assert copy.read_bytes().endswith(b"}\n") and b"\r\n" not in copy.read_bytes()


GO = """
import sys, time
from pathlib import Path
go, ready = Path(sys.argv[1]), Path(sys.argv[2])
ready.touch()
deadline = time.monotonic() + 60
while not go.exists():
    if time.monotonic() > deadline:
        sys.exit(3)
    time.sleep(0.0005)
"""

RACER = """
from datetime import UTC, datetime
from kbot.backtest.reservation import AlreadyReserved, reserve
try:
    reserve(Path(sys.argv[3]), "tendencia", sys.argv[4], datetime.now(UTC))
except AlreadyReserved:
    print("ocupada")
else:
    print("reservada")
"""

WRITER = """
from kbot.backtest.reservation import write_copy
copy, lock, family = Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5]
for i in range(25):
    entry = {
        "familia": family,
        "version": f"v{i:02d}",
        "estado": "completada",
        "iniciada": f"2026-11-23T10:{i:02d}:00+00:00",
    }
    write_copy(copy, entry, lock)
print("escrita")
"""


def race(tmp_path: Path, code: str, arguments: list[list[str]]) -> list[str]:
    """Start one process per list of ``arguments``, all running ``code`` at once, and
    return what each printed."""
    go = tmp_path / "ya"
    readies = [tmp_path / f"lista_{i}" for i in range(len(arguments))]
    racers = [
        subprocess.Popen(
            [sys.executable, "-c", GO + code, str(go), str(ready), *extra],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for ready, extra in zip(readies, arguments, strict=True)
    ]
    try:
        deadline = time.monotonic() + 60
        while not all(r.exists() for r in readies):
            assert time.monotonic() < deadline, "the processes did not start"
            time.sleep(0.01)
        go.touch()
        outputs = [racer.communicate(timeout=120) for racer in racers]
    finally:
        for racer in racers:
            racer.kill()
    assert all(racer.returncode == 0 for racer in racers), outputs
    return sorted(out.strip() for out, _err in outputs)


def test_f3e5_01_of_processes_that_reserve_at_once_only_one_gets_it(tmp_path: Path) -> None:
    folder = tmp_path / "validacion"
    folder.mkdir()
    said = race(tmp_path, RACER, [[str(folder), VERSION]] * 4)
    assert said == ["ocupada", "ocupada", "ocupada", "reservada"]
    assert len(list(folder.iterdir())) == 1


def test_f3e5_06_copies_written_at_once_keep_every_entry(tmp_path: Path) -> None:
    """Four processes of four families write 25 entries each into the same copy at once:
    the copy keeps all 100, and it is valid JSON at the end."""
    copy, lock = tmp_path / "docs" / "consultas.json", tmp_path / "validacion" / "copia.lock"
    families = ["tendencia", "breakout", "reversion_media", "otra"]
    said = race(tmp_path, WRITER, [[str(copy), str(lock), f] for f in families])
    assert said == ["escrita"] * 4
    held = json.loads(copy.read_text(encoding="utf-8"))
    assert held["formato"] == COPY_FORMAT
    found = sorted((e["familia"], e["version"]) for e in held["consultas"])
    assert found == sorted((f, f"v{i:02d}") for f in families for i in range(25))
    assert not [p for p in copy.parent.iterdir() if p.name.endswith(".tmp")]
