"""Acceptance A2 (07 §2 E1): only whitelisted public GETs ever reach the network."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from kbot.kucoin.rest import (
    ForbiddenRequest,
    GuardTransport,
    KucoinApiError,
    KucoinRestClient,
    check_request,
)
from kbot.kucoin.routes import FUTURES_HOST, ROUTES, UTA_HOST, Route, find_route

Factory = Callable[..., KucoinRestClient]

OK_BODY = {"code": "200000", "data": {"ok": True}}


class Recorder(httpx.AsyncBaseTransport):
    """Stands in for the network and remembers every request that got through."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=OK_BODY, headers={"set-cookie": "track=1; Path=/"})


def send(request: httpx.Request) -> Recorder:
    inner = Recorder()

    async def go() -> None:
        await GuardTransport(inner).handle_async_request(request)

    asyncio.run(go())
    return inner


KLINE = f"https://{UTA_HOST}/api/ua/v2/market/kline"
KLINE_QUERY = {"tradeType": "SPOT", "symbol": "BTC-USDT", "interval": "1min"}


def test_whitelisted_get_passes() -> None:
    inner = send(httpx.Request("GET", KLINE, params=KLINE_QUERY))
    assert len(inner.requests) == 1


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
def test_other_methods_never_reach_the_network(method: str) -> None:
    with pytest.raises(ForbiddenRequest, match="method"):
        send(httpx.Request(method, KLINE, params=KLINE_QUERY))


@pytest.mark.parametrize(
    "url",
    [
        f"https://{UTA_HOST}/api/v1/orders",
        f"https://{UTA_HOST}/api/v1/accounts",
        f"https://{UTA_HOST}/api/v1/withdrawals",
        f"https://{UTA_HOST}/api/v1/bullet-public",
        f"https://{UTA_HOST}/api/ua/v1/market/orderbook",
        f"https://{UTA_HOST}/api/ua/v1/market/kline",
        f"https://{UTA_HOST}/api/ua/v1/market/funding-rate",
        f"https://{UTA_HOST}/api/ua/v3/market/kline",
        f"https://{FUTURES_HOST}/api/v1/orders",
        f"https://{FUTURES_HOST}/api/v1/contracts/risk-limit/XBTUSDTM/extra",
        f"https://{FUTURES_HOST}/api/v1/contracts/risk-limit/..",
        f"https://{FUTURES_HOST}/api/v1/contracts/risk-limit/%2E%2E",
        f"https://{FUTURES_HOST}/api/ua/v2/market/kline",
        "https://api.kucoin.example/api/ua/v2/market/kline",
        f"https://{UTA_HOST}.evil.example/api/ua/v2/market/kline",
    ],
)
def test_routes_outside_the_whitelist_are_blocked(url: str) -> None:
    with pytest.raises(ForbiddenRequest, match="whitelist"):
        send(httpx.Request("GET", url))


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        (f"http://{UTA_HOST}/api/ua/v2/market/kline", "scheme"),
        (f"wss://{UTA_HOST}/api/ua/v2/market/kline", "scheme"),
        (f"https://{UTA_HOST}:8443/api/ua/v2/market/kline", "port"),
        (f"https://user:pass@{UTA_HOST}/api/ua/v2/market/kline", "credentials"),
        (f"https://{UTA_HOST}/api/ua/v2/market/kline#x", "fragments"),
    ],
)
def test_scheme_port_and_userinfo_are_checked(url: str, reason: str) -> None:
    with pytest.raises(ForbiddenRequest, match=reason):
        send(httpx.Request("GET", url))


@pytest.mark.parametrize(
    "header",
    [
        "Authorization",
        "Cookie",
        "Proxy-Authorization",
        "KC-API-KEY",
        "KC-API-SIGN",
        "kc-api-passphrase",
    ],
)
def test_credential_headers_are_blocked(header: str) -> None:
    with pytest.raises(ForbiddenRequest, match="credential header"):
        send(httpx.Request("GET", KLINE, params=KLINE_QUERY, headers={header: "x"}))


def test_a_body_is_blocked() -> None:
    with pytest.raises(ForbiddenRequest, match="body"):
        send(httpx.Request("GET", KLINE, params=KLINE_QUERY, content=b'{"side":"buy"}'))


def test_unknown_query_parameters_are_blocked() -> None:
    with pytest.raises(ForbiddenRequest, match="query parameter not allowed: side"):
        send(httpx.Request("GET", KLINE, params={**KLINE_QUERY, "side": "buy"}))


@pytest.mark.parametrize("value", ["BTC USDT", "BTC/USDT", "x" * 41, "", "a&b=c", "BTC-USDT\n"])
def test_unsafe_query_values_are_blocked(value: str) -> None:
    with pytest.raises(ForbiddenRequest, match="unsafe value"):
        send(httpx.Request("GET", KLINE, params={**KLINE_QUERY, "symbol": value}))


def test_path_routes_accept_contract_symbols_only() -> None:
    route = ROUTES["futures.risk_limit"]
    assert route.build_path({"symbol": "XBTUSDTM"}) == "/api/v1/contracts/risk-limit/XBTUSDTM"
    for bad in ("..", ".", "XBT.USDTM", "a/b", ""):
        with pytest.raises(ValueError, match="unsafe path value"):
            route.build_path({"symbol": bad})
    with pytest.raises(ValueError, match="path parameters"):
        route.build_path({})
    with pytest.raises(ForbiddenRequest, match="query parameter not allowed"):
        send(
            httpx.Request("GET", f"https://{FUTURES_HOST}/api/v1/contracts/risk-limit/XBTUSDTM?a=1")
        )


def test_every_route_is_public_market_data() -> None:
    assert len(ROUTES) == len({(r.host, r.path) for r in ROUTES.values()})
    for route in ROUTES.values():
        assert route.host in (UTA_HOST, FUTURES_HOST)
        assert route.path.startswith(("/api/ua/v2/market/",)) or route.path in (
            "/api/v1/contracts/active",
            "/api/v1/contracts/risk-limit/{symbol}",
            "/api/v1/mark-price/{symbol}/current",
            "/api/v1/index/query",
        )
        assert 0 < route.weight <= 15
        assert find_route(route.host, route.build_path(_sample_path(route))) is route


def _sample_path(route: Route) -> dict[str, str]:
    return {name: "XBTUSDTM" for name in route.path_params}


def test_check_request_returns_the_route() -> None:
    route = check_request(httpx.Request("GET", KLINE, params=KLINE_QUERY))
    assert route is ROUTES["uta.v2.kline"]


def test_client_blocks_before_the_network_and_keeps_no_cookies(test_client: Factory) -> None:
    inner = Recorder()

    async def go() -> None:
        async with test_client(inner) as client:
            await client.get("uta.v2.kline", params=KLINE_QUERY)
            await client.get("uta.v2.kline", params=KLINE_QUERY)
            with pytest.raises(ForbiddenRequest, match="unknown route"):
                await client.get("orders")
            with pytest.raises(ForbiddenRequest, match="query parameter"):
                await client.get("uta.v2.kline", params={**KLINE_QUERY, "clientOid": "1"})

    asyncio.run(go())
    assert len(inner.requests) == 2
    for request in inner.requests:
        assert "cookie" not in request.headers
        assert request.headers["user-agent"].startswith("kucoin-trading-bot")
        assert request.method == "GET"


def test_redirects_are_not_followed(test_client: Factory) -> None:
    class Redirect(Recorder):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(302, headers={"location": f"https://{UTA_HOST}/api/v1/orders"})

    inner = Redirect()

    async def go() -> None:
        async with test_client(inner) as client:
            await client.get("uta.v2.kline", params=KLINE_QUERY)

    with pytest.raises(KucoinApiError, match="HTTP 302"):
        asyncio.run(go())
    assert len(inner.requests) == 1


def test_numbers_are_parsed_exactly(test_client: Factory) -> None:
    class Funding(Recorder):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            body = '{"code":"200000","data":{"value":1.0E-4,"granularity":28800000}}'
            return httpx.Response(200, content=body.encode())

    async def go() -> object:
        async with test_client(Funding()) as client:
            response = await client.get("uta.v2.funding", params={"symbol": "XBTUSDTM"})
            return response.data

    data = asyncio.run(go())
    assert json.dumps(str(data["value"])) == '"0.00010"'  # type: ignore[index]
    assert data["granularity"] == 28800000  # type: ignore[index]
