"""Contract tests: real public KuCoin responses recorded by ``tools/record_kucoin.py``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kbot.kucoin.jsonparse import loads

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "kucoin"
# V1 answers recorded for D04 (approved: V2 plus Classic). Evidence only: V1 is no longer
# in the whitelist.
V1_EVIDENCE = FIXTURES / "evidencia_d04_v1"


@pytest.fixture(scope="session")
def recorded() -> Callable[[str], Any]:
    """Load one recorded fixture with exact decimals."""

    def load(name: str) -> Any:
        folder = V1_EVIDENCE if name.startswith("v1_") else FIXTURES
        return loads((folder / f"{name}.json").read_bytes())

    return load


@pytest.fixture(scope="session")
def fixture_names() -> list[str]:
    return sorted(p.stem for p in FIXTURES.glob("*.json") if p.stem != "manifest")
