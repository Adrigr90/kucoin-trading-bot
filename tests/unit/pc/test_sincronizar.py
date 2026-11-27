"""tools/pc/sincronizar.ps1 against the real verifier, through a fake sftp (fake_sftp.py),
without a server and without root: with Windows PowerShell 5.1 on the owner's PC, the one the
owner runs, and with pwsh elsewhere, as in the CI. The real sshd and the chroot are tried by
tests/db/test_externo_e2e.py (02 §13.1, point 4, E4c-3, point 7)."""

from __future__ import annotations

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

    def visit(*extra: str) -> subprocess.CompletedProcess[str]:
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
            env=environment,
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

    # E4C3-09: the PC deletes what the server processed exactly as this disk sent it.
    third = visit()
    assert third.returncode == 0, third.stdout + third.stderr
    assert sorted(p.name for p in s.receipts.iterdir()) == ["recibo_punto.json"]
    s.run()
    assert (
        s.state()["discos"][disk.joinpath(".kbot-disco").read_text().strip()]["punto"]["copia"]
        == hot.path.name
    )
