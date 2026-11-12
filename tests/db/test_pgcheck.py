"""kbot.ops.pgcheck against the PostgreSQL 18 of the CI (stage E4b).

The CI cluster is the one F1-A6 already trusts, so the server check must find nothing
there, map aside (the CI needs its pg_ident map; the server has none).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from kbot.ops import pgcheck

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]


def test_the_ci_cluster_passes_the_server_check(connect: Connect, repo_root: Path) -> None:
    catalog = repo_root / "sql" / "security_definer_catalog.json"
    with connect("postgres") as admin:
        assert pgcheck.run(admin, catalog, allow_map=True) == []
        # Without the allowance, the only finding must be the map of the CI.
        problems = pgcheck.run(admin, catalog, allow_map=False)
    assert problems and all("map=kbot_ci" in p for p in problems)


def test_a_changed_catalog_is_detected(connect: Connect, tmp_path: Path) -> None:
    with connect("postgres") as admin:
        live = pgcheck.live_catalog(admin)
        assert live, "the CI has the definer functions of 0003"
        tampered = tmp_path / "catalog.json"
        tampered.write_text("[]", encoding="utf-8")
        problems = pgcheck.run(admin, tampered, allow_map=True)
    assert any(p.startswith("unexpected definer function") for p in problems)
