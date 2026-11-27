"""tools/pc/sincronizar.ps1 against the real verifier, through a fake sftp (fake_sftp.py),
without a server and without root: with Windows PowerShell 5.1 on the owner's PC, the one the
owner runs, and with pwsh elsewhere, as in the CI. The real sshd and the chroot are tried by
tests/db/test_externo_e2e.py (02 §13.1, point 4, E4c-3, point 7)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from kbot.backup import local
from tests.unit.archive.test_externo import GENERATION, Setup
from tests.unit.upload.support import publish

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "pc" / "sincronizar.ps1"
FAKE = Path(__file__).with_name("fake_sftp.py")


def powershell() -> str:
    found = shutil.which("powershell.exe" if sys.platform == "win32" else "pwsh")
    if found is None:
        if os.environ.get("KBOT_CI"):
            pytest.fail("pwsh is required in the CI")
        pytest.skip("no PowerShell on this machine")
    return found


def fake_sftp(folder: Path) -> Path:
    if sys.platform == "win32":
        wrapper = folder / "sftp.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{FAKE}" %*\r\n', encoding="utf-8")
    else:
        wrapper = folder / "sftp"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return wrapper


def test_three_visits_through_a_fake_sftp_verify_and_clean_up(tmp_path: Path) -> None:
    shell = powershell()
    s = Setup(tmp_path / "servidor")
    day = publish(s.copies, "dia-2026-11-22", b"day copy")
    identity = {
        "copia": day.path.name,
        "sha256": day.manifest["volcado"]["sha256_cifrado"],
        "sha256_manifiesto": local.sha256_file(day.path / local.MANIFEST),
    }
    hot = publish(
        s.copies,
        "caliente-20261127T004000Z",
        b"hot copy",
        dias_excluidos=[{"dia": "2026-11-22", "copia_de_dia": identity, "generacion": GENERATION}],
    )
    disk = tmp_path / "disco"
    sftp = fake_sftp(tmp_path)
    environment = dict(
        os.environ,
        FAKE_SFTP_COPIAS=str(s.copies),
        FAKE_SFTP_ARCHIVO=str(s.root),
        FAKE_SFTP_DESAFIOS=str(s.base / "publico"),
        FAKE_SFTP_RECIBOS=str(s.receipts),
    )

    def visit(*extra: str, cut: str = "0", cut_put: str = "0") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                shell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPT),
                "-Destino",
                str(disk),
                "-Sftp",
                str(sftp),
                *extra,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=300,
            env=dict(environment, FAKE_SFTP_CUT_AT_RM=cut, FAKE_SFTP_CUT_AT_PUT=cut_put),
        )

    s.run()
    first = visit("-Inicializar")
    assert first.returncode == 0, first.stdout + first.stderr
    report = s.run()
    assert report.accepted == [GENERATION] and report.points == [hot.path.name], report

    # E4C3-11: the cache and the state of the disk cut, as by an unplugged disk.
    (disk / ".kbot-huellas.json").write_text('{"archivo/x": {"sel', encoding="utf-8")
    (disk / ".kbot-estado.json").write_text("", encoding="utf-8")
    second = visit()
    assert second.returncode == 0, second.stdout + second.stderr
    assert "respondidos: 1." in second.stdout, second.stdout
    assert second.stdout.count("no se puede leer") == 2, second.stdout
    report = s.run()
    assert report.verified == [GENERATION], report

    marker = disk.joinpath(".kbot-disco").read_text(encoding="utf-8").strip()
    observation = s.receipts / f"recibo_punto_{marker}.json"

    def receipts() -> list[str]:
        return sorted(p.name for p in s.receipts.iterdir())

    def point() -> object:
        return s.state()["discos"][marker]["punto"]

    # E4C3-09 and E4C3-17: the PC deletes what the server processed exactly as this disk sent
    # it, also when the connection is cut before the first deletion and after it.
    pending = receipts()
    assert len(pending) == 3, pending  # the receipt, the answer and the point
    before = visit(cut="1")
    assert before.returncode != 0 and receipts() == pending, before.stdout + before.stderr
    after = visit(cut="2")
    assert after.returncode != 0 and len(receipts()) == 2, after.stdout + after.stderr
    third = visit()
    assert third.returncode == 0, third.stdout + third.stderr
    assert receipts() == [observation.name]
    s.run()
    assert point() == {"copia": hot.path.name, "hora": "2026-11-27T00:40:00+00:00"}

    # E4C3-16: the server accepted that observation, but its discos.json fell behind. The next
    # visit is cut inside the put of its observation, after the target is emptied: the
    # observation file keeps the last one whole, because it goes up under a temporary name.
    old = observation.read_bytes()
    (s.base / "publico" / "discos.json").write_text("{}", encoding="utf-8")
    cut_put = visit(cut_put="1")
    assert cut_put.returncode != 0, cut_put.stdout + cut_put.stderr
    assert observation.read_bytes() == old
    assert [p.name for p in s.receipts.glob(".subiendo-*")] == [f".subiendo-{observation.name}"]
    # Then the disk loses its state and its only complete hot copy, gone from the server
    # too. The number comes back from the disk's own file on the server, not from the clock:
    # the old one plus one. The point goes, and the old observation sent again is refused.
    (disk / ".kbot-estado.json").write_text("", encoding="utf-8")
    shutil.rmtree(disk / "copias" / hot.path.name)
    shutil.rmtree(hot.path)  # local retention took it; its register stays
    fourth = visit()
    assert fourth.returncode == 0, fourth.stdout + fourth.stderr
    assert "ninguna copia caliente completa" in fourth.stdout, fourth.stdout
    assert not list(s.receipts.glob(".subiendo-*"))  # the half-sent file is cleaned up
    number = json.loads(old)["secuencia"] + 1
    assert json.loads(observation.read_text("utf-8"))["secuencia"] == number
    s.run()
    assert point() is None
    observation.write_bytes(old)
    assert any("not newer" in r for r in s.run().rejected)
    assert point() is None

    # Without a reliable number the visit sends no observation and says so; once the server
    # has processed the disk's file, discos.json is recent enough and the next visit goes on.
    observation.write_text('{"tipo": "punto", "secu', encoding="utf-8")
    (disk / ".kbot-estado.json").write_text("", encoding="utf-8")
    stopped = visit()
    assert stopped.returncode == 1 and "no se sabe con seguridad" in stopped.stdout, stopped.stdout
    assert observation.read_text("utf-8") == '{"tipo": "punto", "secu'
    s.run()  # processes it: a malformed file, acknowledged
    resumed = visit()
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    # Above the last accepted one; the stopped visit kept a number it never used.
    assert json.loads(observation.read_text("utf-8"))["secuencia"] > number
    report = s.run()
    assert report.rejected == [] and point() is None
