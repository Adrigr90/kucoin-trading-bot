"""Test-only construction of REST clients.

KucoinRestClient takes neither a budget nor a transport (R-E1-03): production code always
gets the machine-wide budget and the real network. Tests replace both module functions
with unittest.mock.patch, only while the client is being built.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest

from kbot.kucoin import rest as rest_module
from kbot.kucoin.ratelimit import RestRateLimiter
from kbot.kucoin.rest import KucoinRestClient

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
