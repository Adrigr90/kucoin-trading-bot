"""The only KuCoin REST routes the bot may call: public market data, GET only.

Spec 02 §2 and §3.2. Every weight was measured on 6 November 2026 with the
``gw-ratelimit-remaining`` header of real responses; the contract tests check them against
the recorded fixtures (docs/kucoin/matriz_v1_v2.md). D04 (approved on 7 November 2026): UTA V2
plus Classic Futures; the V1 routes are not listed. Order-book REST endpoints are left out
on purpose: depth comes from the WebSocket snapshots (02 §3.1).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

UTA_HOST = "api.kucoin.com"
FUTURES_HOST = "api-futures.kucoin.com"

# Query values: symbols, enum names and integer timestamps only.
SAFE_VALUE = re.compile(r"[A-Za-z0-9._\-]{1,40}")
# Path values are contract symbols; no dots, so that "." or ".." can never become a segment.
PATH_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{0,39}")
_PLACEHOLDER = re.compile(r"\{([a-z]+)\}")


@dataclass(frozen=True)
class Route:
    name: str
    host: str
    path: str
    weight: int
    params: frozenset[str] = field(default_factory=frozenset)

    @property
    def path_params(self) -> frozenset[str]:
        return frozenset(_PLACEHOLDER.findall(self.path))

    def build_path(self, values: Mapping[str, str]) -> str:
        if set(values) != self.path_params:
            raise ValueError(f"{self.name}: path parameters must be {sorted(self.path_params)}")
        path = self.path
        for name, value in values.items():
            if not PATH_VALUE.fullmatch(value):
                raise ValueError(f"{self.name}: unsafe path value for {name}: {value!r}")
            path = path.replace("{" + name + "}", value)
        return path

    def matches(self, host: str, path: str) -> bool:
        if host != self.host:
            return False
        pattern = re.escape(self.path)
        for name in self.path_params:
            pattern = pattern.replace(re.escape("{" + name + "}"), PATH_VALUE.pattern)
        return re.fullmatch(pattern, path) is not None


def _uta(name: str, version: str, endpoint: str, weight: int, *params: str) -> Route:
    path = f"/api/ua/{version}/market/{endpoint}"
    return Route(f"uta.{version}.{name}", UTA_HOST, path, weight, frozenset(params))


_KLINE = ("tradeType", "symbol", "interval", "startAt", "endAt")

_ROUTES = (
    _uta("instrument", "v2", "instrument", 4, "tradeType", "symbol"),
    _uta("ticker", "v2", "ticker", 15, "tradeType", "symbol"),
    _uta("kline", "v2", "kline", 3, *_KLINE, "klineType"),
    _uta("trades", "v2", "trade", 3, "tradeType", "symbol"),
    _uta("funding", "v2", "funding-rate", 2, "symbol", "productType"),
    _uta("funding_history", "v2", "funding-rate-history", 5, "symbol", "startAt", "endAt"),
    Route("futures.contracts", FUTURES_HOST, "/api/v1/contracts/active", 3),
    Route("futures.risk_limit", FUTURES_HOST, "/api/v1/contracts/risk-limit/{symbol}", 5),
    Route("futures.mark_price", FUTURES_HOST, "/api/v1/mark-price/{symbol}/current", 3),
    Route(
        "futures.index",
        FUTURES_HOST,
        "/api/v1/index/query",
        2,
        frozenset({"symbol", "startAt", "endAt", "reverse", "offset", "forward", "maxCount"}),
    ),
)

ROUTES: Mapping[str, Route] = {route.name: route for route in _ROUTES}


def find_route(host: str, path: str) -> Route | None:
    """The whitelisted route that a request targets, or None."""
    for route in _ROUTES:
        if route.matches(host, path):
            return route
    return None
