"""kbot.ops.bootstrap without PostgreSQL: the statements of roles.sql (E4b-03)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kbot.ops import bootstrap

ROOT = Path(__file__).resolve().parents[3]


def test_roles_sql_splits_into_its_statements() -> None:
    statements = bootstrap.statements((ROOT / "sql" / "roles.sql").read_text(encoding="utf-8"))
    created = [bootstrap.created_object(s) for s in statements]
    roles = [
        name for kind_name in created if kind_name for kind, name in [kind_name] if kind == "ROLE"
    ]
    assert "kbot_owner" in roles and "kbot_archive" in roles
    assert ("DATABASE", "kbot") in created
    # The database comes after its owner, so a run cut anywhere resumes in order.
    assert created.index(("DATABASE", "kbot")) > created.index(("ROLE", "kbot_owner"))
    assert any(s.startswith("GRANT CONNECT ON DATABASE kbot") for s in statements)


def test_a_file_it_cannot_split_safely_is_refused() -> None:
    with pytest.raises(ValueError, match="plain statements"):
        bootstrap.statements("DO $$ BEGIN END $$;")
    with pytest.raises(ValueError, match="plain statements"):
        bootstrap.statements("COMMENT ON ROLE x IS 'a; b';")
