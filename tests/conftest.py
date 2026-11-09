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
from unittest.mock import patch

import httpx
import pytest

from kbot.kucoin import rest as rest_module
from kbot.kucoin.ratelimit import RestRateLimiter
from kbot.kucoin.rest import KucoinRestClient

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


@pytest.fixture(scope="session")
def ci_environment() -> Callable[[str], str]:
    """Read a value that the CI provides, or skip (fail in the CI) when it is missing."""

    def read(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            _unavailable(f"environment variable not set: {name}")
            raise AssertionError("unreachable")
        return value

    return read


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


# --- test-only construction of REST clients (R-E1-03) -------------------------------------
# KucoinRestClient takes neither a budget nor a transport: production code always gets the
# machine-wide budget and the real network. Tests replace both module functions with
# unittest.mock.patch, only while the client is being built.

ClientFactory = Callable[..., KucoinRestClient]


@pytest.fixture
def test_client() -> ClientFactory:
    def build(
        transport: httpx.AsyncBaseTransport,
        limiter: RestRateLimiter | None = None,
        **options: object,
    ) -> KucoinRestClient:
        budget = limiter if limiter is not None else rest_module.shared_limiter()
        with (
            patch.object(rest_module, "_network_transport", lambda: transport),
            patch.object(rest_module, "shared_limiter", lambda: budget),
        ):
            return KucoinRestClient(**options)  # type: ignore[arg-type]

    return build
