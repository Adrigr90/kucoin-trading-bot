"""Migrations already in main never change (02 §3.8 and §3.9; review of the E3 design).

A database that applied a migration would never apply its new version: any change goes in
a new migration. The hashes are those of the files as they entered main; a later
migration adds its own here once it is integrated.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

FROZEN = {
    "0001_esquemas_y_limites.py": (
        "01e683be75f7d8fc8e438daa585ec085ecc3bc3d0221707cbfb43e4f7bd08bba"
    ),
    "0001_esquemas_y_limites.sql": (
        "cb1e83320e98fb78e23138db4d0b62bb5d27946eabbba8916fdec95322347613"
    ),
    "0002_mercado.py": "77c43c8fd5ae789da39e51912da5f03c9538622a6584f1b9795673c31462f4b1",
    "0002_mercado.sql": "dbb6f0eb73db10b75d96388f2238e3f153e77cb668a16566f4d0d25486029958",
}


@pytest.mark.parametrize("name", sorted(FROZEN))
def test_a_migration_in_main_never_changes(repo_root: Path, name: str) -> None:
    content = (repo_root / "migrations" / "versions" / name).read_bytes()
    digest = hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()
    assert digest == FROZEN[name], f"{name} changed: write a new migration instead"
