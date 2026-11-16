"""kbot.ops.pgcheck, privileges and bootstrap against the PostgreSQL 18 of the CI (E4b).

The CI cluster is the one F1-A6 already trusts, so the server check must find nothing
there, map aside (the CI needs its pg_ident map; the server has none). Each finding of the
review of E4b is then reproduced on the live cluster, checked to fail, and undone.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest

from kbot.ops import bootstrap, pgcheck

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]


@pytest.fixture
def check(connect: Connect, repo_root: Path) -> Iterator[Callable[..., list[str]]]:
    head = pgcheck.expected_head(repo_root / "alembic.ini")
    grants = pgcheck.policy_grants(repo_root)
    catalog = repo_root / "sql" / "security_definer_catalog.json"
    with connect("postgres") as admin:

        def run(
            *, allow_map: bool = True, catalog_path: Path = catalog, at: str = head
        ) -> list[str]:
            return pgcheck.run(
                admin, catalog_path, head=at, allowed_grants=grants, allow_map=allow_map
            )

        run.admin = admin  # type: ignore[attr-defined]
        yield run


def test_the_ci_cluster_passes_the_server_check(check: Callable[..., list[str]]) -> None:
    assert check() == []
    # Without the allowance, the only finding must be the map of the CI.
    problems = check(allow_map=False)
    assert problems and all("map=kbot_ci" in p for p in problems)


def test_a_changed_catalog_is_detected(check: Callable[..., list[str]], tmp_path: Path) -> None:
    tampered = tmp_path / "catalog.json"
    tampered.write_text("[]", encoding="utf-8")
    assert any(p.startswith("unexpected definer function") for p in check(catalog_path=tampered))


def test_a_hidden_membership_fails(check: Callable[..., list[str]]) -> None:
    """E4b-04: INHERIT FALSE, SET TRUE changes no attribute and keeps CREATE refused, but
    lets kbot_ingest SET ROLE kbot_owner."""
    admin = check.admin  # type: ignore[attr-defined]
    admin.execute("GRANT kbot_owner TO kbot_ingest WITH INHERIT FALSE, SET TRUE")
    try:
        problems = check()
    finally:
        admin.execute("REVOKE kbot_owner FROM kbot_ingest")
    assert "unexpected membership: kbot_ingest in kbot_owner" in problems
    assert check() == []


def test_an_undue_execute_fails(check: Callable[..., list[str]]) -> None:
    """E4b-04: nobody may execute market.retire_day(date) in E3 and E4."""
    admin = check.admin  # type: ignore[attr-defined]
    admin.execute("GRANT EXECUTE ON FUNCTION market.retire_day(date) TO kbot_archive")
    try:
        problems = check()
    finally:
        admin.execute("REVOKE EXECUTE ON FUNCTION market.retire_day(date) FROM kbot_archive")
    assert any("function privilege: kbot_archive market.retire_day(date)" in p for p in problems)
    assert check() == []


def test_a_setting_tied_to_the_database_fails(check: Callable[..., list[str]]) -> None:
    """E4b-05: a role setting in the database overrides the global one of the role."""
    admin = check.admin  # type: ignore[attr-defined]
    admin.execute("ALTER ROLE kbot_ingest IN DATABASE kbot SET statement_timeout = 0")
    try:
        problems = check()
    finally:
        admin.execute("ALTER ROLE kbot_ingest IN DATABASE kbot RESET statement_timeout")
    assert any(p.startswith("setting in database kbot for kbot_ingest") for p in problems)
    assert check() == []


@pytest.mark.parametrize("at", ["0002", "not-a-real-migration", ""])
def test_only_the_head_of_the_code_passes(check: Callable[..., list[str]], at: str) -> None:
    """E4b-06: the version must be the head of the code deployed, not just one row."""
    problems = check(at=at)
    assert any(p.startswith("migrations at") for p in problems)


def test_bootstrap_repeated_on_a_complete_cluster_changes_nothing(
    connect: Connect, repo_root: Path
) -> None:
    """E4b-03: every CREATE is skipped because its object exists; the rest is idempotent."""
    text = (repo_root / "sql" / "roles.sql").read_text(encoding="utf-8")
    with connect("postgres", dbname="postgres") as admin:
        log = bootstrap.apply(admin, text)
    created = [line for line in log if line.startswith("applied: CREATE")]
    assert created == []
    assert any(line == "skipped (exists): DATABASE kbot" for line in log)
    assert any(line == "skipped (exists): ROLE kbot_owner" for line in log)
