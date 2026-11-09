"""CI workflow rules and acceptance test F1-A10.

F1-A10: the repository is public (P2) and GitHub's standard hosted runners are free in public
repositories (spec 02 §16, GitHub billing documentation). The CI checks both conditions:
the repository visibility that GitHub reports in the event of the run, and that only
standard runners are used. Every action is pinned to a full commit SHA (supply chain, 02 §9).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

STANDARD_RUNNERS = {"ubuntu-24.04", "ubuntu-22.04", "ubuntu-latest"}
VISIBILITY_WIRING = "KBOT_REPO_VISIBILITY: ${{ github.event.repository.visibility }}"


def repository_visibility_problem(visibility: str | None) -> str | None:
    """F1-A10 holds only for a public repository: standard runners are free only there."""
    if visibility == "public":
        return None
    return f"F1-A10 needs a public repository; GitHub reports {visibility!r}"


@pytest.mark.parametrize(
    ("visibility", "passes"),
    [("public", True), ("private", False), ("internal", False), ("", False), (None, False)],
)
def test_f1_a10_visibility_rule(visibility: str | None, passes: bool) -> None:
    assert (repository_visibility_problem(visibility) is None) is passes


def test_f1_a10_the_repository_is_public(ci_environment: Callable[[str], str]) -> None:
    visibility = ci_environment("KBOT_REPO_VISIBILITY")
    assert repository_visibility_problem(visibility) is None, visibility


def test_f1_a10_the_workflow_reports_the_visibility(workflows: dict[str, str]) -> None:
    assert any(VISIBILITY_WIRING in text for text in workflows.values())


@pytest.fixture(scope="module")
def workflows(repo_root: Path) -> dict[str, str]:
    folder = repo_root / ".github" / "workflows"
    files = sorted(folder.glob("*.yml")) + sorted(folder.glob("*.yaml"))
    assert files, "no workflow files found"
    return {path.name: path.read_text(encoding="utf-8") for path in files}


def test_f1_a10_only_standard_runners(workflows: dict[str, str]) -> None:
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
