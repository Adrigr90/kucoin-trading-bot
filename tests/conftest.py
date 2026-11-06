"""Shared test settings.

With ``KBOT_CI=1`` a test that cannot run (no PostgreSQL, no gitleaks, no openssl) fails
instead of being skipped, so the CI can never pass by skipping. On a developer PC those
tests are skipped. Failures are also published as GitHub annotations, which are readable
through the public API.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
IN_CI = os.environ.get("KBOT_CI") == "1"
DB_ENABLED = os.environ.get("KBOT_TEST_DB") == "1"

_GIT_FOR_WINDOWS_TOOLS = (
    Path(r"C:\Program Files\Git\usr\bin"),
    Path(r"C:\Program Files\Git\mingw64\bin"),
)


def _unavailable(reason: str) -> None:
    if IN_CI:
        pytest.fail(f"required in the CI but unavailable: {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO


@pytest.fixture(scope="session")
def find_tool() -> Callable[[str, str | None], str]:
    """Locate an external tool, or skip (fail in the CI) when it is missing."""

    def find(name: str, env_var: str | None = None) -> str:
        if env_var and os.environ.get(env_var):
            return os.environ[env_var]
        found = shutil.which(name)
        if found is None:
            for folder in _GIT_FOR_WINDOWS_TOOLS:
                candidate = folder / f"{name}.exe"
                if candidate.exists():
                    found = str(candidate)
                    break
        if found is None:
            _unavailable(f"tool not found: {name}")
            raise AssertionError("unreachable")
        return found

    return find


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("db") and not DB_ENABLED:
        _unavailable("PostgreSQL tests need KBOT_TEST_DB=1 (they run in the CI)")


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for category in ("failed", "error"):
        for report in terminalreporter.stats.get(category, []):
            message = _short_message(report)
            title = _escape_property(f"{category}: {report.nodeid}")
            terminalreporter.write_line(f"::error title={title}::{_escape_data(message)}")


def _short_message(report: pytest.TestReport) -> str:
    crash = getattr(report.longrepr, "reprcrash", None)
    if crash is not None:
        return f"{crash.path}:{crash.lineno}: {crash.message}"
    return str(report.longrepr)[-3000:]


def _escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")
