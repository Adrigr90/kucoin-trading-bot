"""Secret scanning (spec 02 §9) and the second case of acceptance test A8.

A8 (case 2): the CI rejects a test secret. The fake token is generated at run time inside
a temporary folder, so no secret-looking string is ever committed.
"""

from __future__ import annotations

import secrets
import string
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.tools


@pytest.fixture(scope="module")
def gitleaks(find_tool: Callable[[str, str | None], str]) -> str:
    return find_tool("gitleaks", "KBOT_GITLEAKS")


def _scan(binary: str, folder: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [binary, "dir", str(folder), "--no-banner", "--redact", "--exit-code", "1"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_a8_a_planted_token_is_detected(gitleaks: str, tmp_path: Path) -> None:
    alphabet = string.ascii_letters + string.digits
    fake_token = "ghp_" + "".join(secrets.choice(alphabet) for _ in range(36))
    (tmp_path / "settings.py").write_text(f'GITHUB_TOKEN = "{fake_token}"\n', encoding="utf-8")
    result = _scan(gitleaks, tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr


def test_a_clean_folder_passes(gitleaks: str, tmp_path: Path) -> None:
    (tmp_path / "settings.py").write_text('MODE = "paper"\n', encoding="utf-8")
    result = _scan(gitleaks, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
