"""Catalog fingerprint of the SECURITY DEFINER functions (spec 02 §9, CI layer 3).

The expected catalog is versioned in sql/security_definer_catalog.json. Phase 1 creates no
such function, so it is empty, but the control is already active: any function that
appears, disappears or changes owner, settings or body without updating the file fails.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import psycopg
import pytest

from kbot.core.jcs import canonicalize

pytestmark = pytest.mark.db


def test_security_definer_catalog_matches(admin: psycopg.Connection, repo_root: Path) -> None:
    connection_info = admin.info
    assert connection_info.dbname == "kbot"
    rows = admin.execute(
        """
        SELECT p.oid::regprocedure::text,
               pg_get_userbyid(p.proowner)::text,
               COALESCE(p.proconfig, '{}'),
               encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex')
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE p.prosecdef
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY 1
        """
    ).fetchall()
    live = [
        {"signature": signature, "owner": owner, "config": list(config), "body_sha256": body}
        for signature, owner, config, body in rows
    ]
    expected = json.loads(
        (repo_root / "sql" / "security_definer_catalog.json").read_text(encoding="utf-8")
    )
    assert live == expected
    assert hashlib.sha256(canonicalize(live)).hexdigest() == (
        hashlib.sha256(canonicalize(expected)).hexdigest()
    )
