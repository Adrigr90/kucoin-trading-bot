"""Contract tests: real public KuCoin responses recorded by ``tools/record_kucoin.py``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kbot.kucoin.jsonparse import loads

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "kucoin"


@pytest.fixture(scope="session")
def recorded() -> Callable[[str], Any]:
    """Load one recorded fixture with exact decimals."""

    def load(name: str) -> Any:
        return loads((FIXTURES / f"{name}.json").read_bytes())

    return load


@pytest.fixture(scope="session")
def fixture_names() -> list[str]:
    return sorted(p.stem for p in FIXTURES.glob("*.json") if p.stem != "manifest")
