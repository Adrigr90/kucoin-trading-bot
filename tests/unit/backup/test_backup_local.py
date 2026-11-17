"""The local backups (02 §13.1, point 4; stage E4c-1): what needs no database."""

from __future__ import annotations

import errno
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from kbot.backup import local

PRINCIPAL = "age1qtkaygfztawje3lye07w82gd0m2vqc6z5ymfpqnszpj04x4d296qsp2vfv"
RECOVERY = "age1pwcwfacsagfjpqydkcxpc6lzzzed7fhqsjxdj6unzv0t3q3pwazqeql06x"


def copies(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).mkdir()
        (directory / name / local.DUMP).write_bytes(b"x")


def test_the_local_retention_keeps_three_hourly_copies_and_the_last_daily_one(
    tmp_path: Path,
) -> None:
    names = [
        "caliente-20261117T104000Z",
        "caliente-20261117T114000Z",
        "caliente-20261118T004000Z",  # the first of the last day: the daily copy
        "caliente-20261118T014000Z",
        "caliente-20261118T024000Z",
        "caliente-20261118T034000Z",
    ]
    assert local.hot_to_delete(names) == names[:2]
    assert local.hot_to_delete(list(reversed(names))) == names[:2]  # names sort by time
    assert local.hot_to_delete(names[:2]) == []
    assert local.hot_to_delete([]) == []
    copies(tmp_path, *names)
    deleted = local.retention(tmp_path, eligible=set())
    assert sorted(p.name for p in deleted) == names[:2]
    assert sorted(p.name for p in tmp_path.iterdir()) == names[2:]


def test_a_sealed_day_copy_stays_until_its_day_is_eligible(tmp_path: Path) -> None:
    copies(tmp_path, "dia-2026-11-20", "dia-2026-11-21", "caliente-20261124T004000Z")
    assert local.retention(tmp_path, eligible=set()) == []
    deleted = local.retention(tmp_path, eligible={date(2026, 11, 20)})
    assert [p.name for p in deleted] == ["dia-2026-11-20"]
    assert (tmp_path / "dia-2026-11-21").is_dir()


def test_the_weekly_global_objects_keep_the_last_two(tmp_path: Path) -> None:
    names = [f"globales-202611{d}T034000Z" for d in ("08", "15", "22")]
    copies(tmp_path, *names)
    assert [p.name for p in local.globals_retention(tmp_path)] == names[:1]
    assert sorted(p.name for p in tmp_path.iterdir()) == names[1:]


def test_the_attempts_of_a_stopped_run_are_removed_and_nothing_else(tmp_path: Path) -> None:
    copies(tmp_path, ".caliente-20261117T104000Z.tmp", "caliente-20261117T114000Z")
    (tmp_path / local.REGISTER).write_text("{}\n", encoding="utf-8")
    removed = local.remove_attempts(tmp_path)
    assert [p.name for p in removed] == [".caliente-20261117T104000Z.tmp"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "caliente-20261117T114000Z",
        local.REGISTER,
    ]


def test_the_recipients_are_the_two_public_keys_and_never_a_private_one(tmp_path: Path) -> None:
    file = tmp_path / "age-recipients.txt"
    file.write_text(f"# principal\n{PRINCIPAL}\n# recovery\n{RECOVERY}\n", encoding="utf-8")
    assert local.recipients(file) == [PRINCIPAL, RECOVERY]
    file.write_text(f"{PRINCIPAL}\n", encoding="utf-8")
    with pytest.raises(local.BackupError, match="principal and the recovery"):
        local.recipients(file)
    file.write_text(f"{PRINCIPAL}\n{RECOVERY}\nAGE-SECRET-KEY-1QQQ\n", encoding="utf-8")
    with pytest.raises(local.BackupError, match="private key"):
        local.recipients(file)
    # The file of the repository is the one the server installs.
    repository = Path(__file__).resolve().parents[3] / "deploy" / "etc" / "kbot"
    assert local.recipients(repository / "age-recipients.txt") == [PRINCIPAL, RECOVERY]


def finished(directory: Path, name: str, content: bytes = b"cipher", **extra: Any) -> local.Copy:
    """A copy taken through the real protocol of the module: attempt, then finish."""
    with local._attempt(directory, name) as (temporary, final):
        (temporary / local.DUMP).write_bytes(content)
        manifest: dict[str, Any] = {
            "nombre": name,
            "clase": "caliente",
            "creada": "2026-11-18T10:40:00+00:00",
            "volcado": {
                "sha256_claro": "a" * 64,
                "sha256_cifrado": local.sha256_file(temporary / local.DUMP),
            },
            **extra,
        }
        return local._finish(directory, temporary, final, manifest)


def test_a_copy_is_registered_before_it_is_published(tmp_path: Path) -> None:
    """Review of E4c-1, E4c1-04: a published copy always has its register entry."""
    copy = finished(tmp_path, "caliente-20261118T104000Z")
    entries = local.register_entries(tmp_path)
    assert [e["copia"] for e in entries] == [copy.path.name]
    assert [c.path for c in local.published(tmp_path, local.HOT_PREFIX)] == [copy.path]
    assert local.reconcile(tmp_path) == []


def test_a_register_that_fails_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full_disk(*_: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(local, "_register", full_disk)
    with pytest.raises(OSError, match="No space"):
        finished(tmp_path, "dia-2026-11-20")
    assert list(tmp_path.iterdir()) == []  # neither the copy nor its attempt


def test_a_rename_that_fails_leaves_only_an_entry_that_authenticates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(self: Path, target: Path) -> Path:
        raise OSError(errno.EIO, "rename failed")

    monkeypatch.setattr(Path, "rename", refused)
    with pytest.raises(OSError, match="rename failed"):
        finished(tmp_path, "dia-2026-11-20")
    monkeypatch.undo()
    assert sorted(p.name for p in tmp_path.iterdir()) == [local.REGISTER]
    assert local.published(tmp_path, local.DAY_PREFIX) == []
    # The day is still pending, and the next copy of it is published normally.
    copy = finished(tmp_path, "dia-2026-11-20")
    assert [c.path for c in local.published(tmp_path, local.DAY_PREFIX)] == [copy.path]


def test_a_published_copy_without_its_entry_is_removed_and_goes_back_to_pending(
    tmp_path: Path,
) -> None:
    kept = finished(tmp_path, "dia-2026-11-20")
    lost = finished(tmp_path, "dia-2026-11-21", content=b"other")
    register = tmp_path / local.REGISTER
    lines = register.read_text(encoding="utf-8").splitlines()
    register.write_text(lines[0] + "\n", encoding="utf-8")  # the entry of 21 Nov is lost
    assert local.reconcile(tmp_path) == [lost.path]
    assert [c.path for c in local.published(tmp_path, local.DAY_PREFIX)] == [kept.path]
    # A manifest that no longer matches its entry is not vouched for either.
    manifest = json.loads((kept.path / local.MANIFEST).read_text(encoding="utf-8"))
    manifest["volcado"]["sha256_claro"] = "b" * 64
    (kept.path / local.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    assert local.reconcile(tmp_path) == [kept.path]


def test_a_line_cut_by_a_full_disk_never_breaks_the_register(tmp_path: Path) -> None:
    first = finished(tmp_path, "caliente-20261118T104000Z")
    with (tmp_path / local.REGISTER).open("a", encoding="utf-8") as handle:
        handle.write('{"copia": "caliente-2026')  # what a full disk can leave
    second = finished(tmp_path, "caliente-20261118T114000Z")
    names = [e["copia"] for e in local.register_entries(tmp_path)]
    assert names == [first.path.name, second.path.name]
    assert local.reconcile(tmp_path) == []


def test_a_manifest_changed_in_one_fingerprint_is_no_longer_vouched_for(tmp_path: Path) -> None:
    """Residual of E4c1-02: the register binds the bytes of the whole manifest, so one hex
    digit changed in a fingerprint, with the rows and the dump kept, makes the copy unusable
    as a source of fingerprints; the next run removes it and makes it again."""
    partitions = [{"particion": "market.candles_p20261120", "filas": 7, "huella": "1" * 64}]
    copy = finished(tmp_path, "dia-2026-11-20", particiones=partitions)
    assert [c.path for c in local.published(tmp_path, local.DAY_PREFIX)] == [copy.path]
    text = (copy.path / local.MANIFEST).read_text(encoding="utf-8")
    altered = text.replace("1" * 64, "1" * 63 + "2")  # one hex digit; rows and dump kept
    assert altered != text
    (copy.path / local.MANIFEST).write_text(altered, encoding="utf-8", newline="\n")
    assert local.published(tmp_path, local.DAY_PREFIX) == []  # never a source of reuse
    assert local.reconcile(tmp_path) == [copy.path]
    # Made again, it is vouched for and its fingerprints can be reused normally.
    again = finished(tmp_path, "dia-2026-11-20", particiones=partitions)
    assert [c.path for c in local.published(tmp_path, local.DAY_PREFIX)] == [again.path]
