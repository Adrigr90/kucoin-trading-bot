"""Migration limits against the DM, without a database (spec 02 §6.1).

F1-A8 (case 3): the CI rejects a limit that differs from the DM.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.check_limits import compare, migration_limits


@pytest.fixture(scope="module")
def dm_text(repo_root: Path) -> str:
    return (repo_root / "docs" / "especificacion" / "01_Documento_Maestro.md").read_text(
        encoding="utf-8"
    )


@pytest.fixture(scope="module")
def migration_sql(repo_root: Path) -> list[str]:
    files = sorted((repo_root / "migrations" / "versions").glob("*.sql"))
    return [path.read_text(encoding="utf-8") for path in files]


def test_repository_migration_matches_the_dm(dm_text: str, migration_sql: list[str]) -> None:
    assert compare(dm_text, migration_sql) == []
    version, _limits, stored_hash = migration_limits(migration_sql)
    assert version == 1
    assert len(stored_hash) == 64


@pytest.mark.parametrize(
    ("column_value", "changed"),
    [
        ("    2,\n    10,\n    true,", "    3,\n    10,\n    true,"),
        ("    0.5,\n", "    0.75,\n"),
        ("    10000,\n", "    100000,\n"),
        ("    'isolated',\n    true,\n", "    'isolated',\n    false,\n"),
    ],
)
def test_f1_a8_a_limit_different_from_the_dm_is_rejected(
    dm_text: str, migration_sql: list[str], column_value: str, changed: str
) -> None:
    original = "\n".join(migration_sql)
    assert column_value in original
    problems = compare(dm_text, [original.replace(column_value, changed, 1)])
    assert problems, "a changed limit must be reported"


def test_a_tampered_hash_is_rejected(dm_text: str, migration_sql: list[str]) -> None:
    original = "\n".join(migration_sql)
    stored = migration_limits(migration_sql)[2]
    tampered = original.replace(stored, "0" * 64)
    assert any("limits_hash" in problem for problem in compare(dm_text, [tampered]))


def test_a_changed_dm_is_detected(dm_text: str, migration_sql: list[str]) -> None:
    changed_dm = dm_text.replace("Drawdown máximo: 10 %.", "Drawdown máximo: 15 %.")
    assert compare(changed_dm, migration_sql)
