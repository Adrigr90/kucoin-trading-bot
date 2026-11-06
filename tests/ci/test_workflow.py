"""CI workflow rules and acceptance test A10.

A10: the repository is public (P2) and GitHub's standard hosted runners are free in public
repositories (spec 02 §16, GitHub billing documentation). The CI must therefore use only
standard runners. Every action is pinned to a full commit SHA (supply chain, 02 §9).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STANDARD_RUNNERS = {"ubuntu-24.04", "ubuntu-22.04", "ubuntu-latest"}


@pytest.fixture(scope="module")
def workflows(repo_root: Path) -> dict[str, str]:
    folder = repo_root / ".github" / "workflows"
    files = sorted(folder.glob("*.yml")) + sorted(folder.glob("*.yaml"))
    assert files, "no workflow files found"
    return {path.name: path.read_text(encoding="utf-8") for path in files}


def test_a10_only_standard_runners(workflows: dict[str, str]) -> None:
    for name, text in workflows.items():
        runners = re.findall(r"^\s*runs-on:\s*(\S+)\s*$", text, re.MULTILINE)
        assert runners, f"{name}: no runs-on found"
        assert set(runners) <= STANDARD_RUNNERS, f"{name}: {runners}"


def test_actions_are_pinned_to_commit_shas(workflows: dict[str, str]) -> None:
    for name, text in workflows.items():
        for action in re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)", text, re.MULTILINE):
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), f"{name}: {action}"


def test_workflow_token_is_read_only(workflows: dict[str, str]) -> None:
    for name, text in workflows.items():
        assert re.search(r"^permissions:\s*\n\s+contents:\s*read\s*$", text, re.MULTILINE), name
