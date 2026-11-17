"""The local backups (02 §13.1, point 4; stage E4c-1): what needs no database."""

from __future__ import annotations

from datetime import date
from pathlib import Path

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
