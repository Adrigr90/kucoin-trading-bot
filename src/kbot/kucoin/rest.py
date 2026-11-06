"""Read-only REST client for KuCoin public market data (spec 02 §2 and §3.1).

``GuardTransport`` sits under httpx, so every request passes it whatever code builds it:
anything that is not a body-less HTTPS GET to a whitelisted route fails before reaching
the network.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any

import httpx

from kbot.core.clock import Clock, SystemClock
from kbot.kucoin.jsonparse import loads
from kbot.kucoin.ratelimit import RateLimitHeaders, RestRateLimiter, shared_limiter
from kbot.kucoin.routes import ROUTES, SAFE_VALUE, Route, find_route

SUCCESS_CODE = "200000"
QUOTA_OR_OVERLOAD_CODE = "429000"
SYSTEM_LIMIT_CODE = "429001"
USER_AGENT = "kucoin-trading-bot (read-only public market data)"
CREDENTIAL_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})


class ForbiddenRequest(RuntimeError):
    """A request outside the whitelist: blocked before it reaches the network."""


class KucoinApiError(RuntimeError):
    def __init__(self, route: str, status: int, code: str | None, message: str) -> None:
        super().__init__(f"{route}: HTTP {status}, code {code}: {message}")
        self.route = route
        self.status = status
        self.code = code


@dataclass(frozen=True)
class RestResponse:
    route: str
    url: str
    status: int
    data: Any
    ratelimit: RateLimitHeaders | None
    received_at: datetime
    text: str


def check_request(request: httpx.Request) -> Route:
    """Raise ForbiddenRequest unless the request is a whitelisted public GET."""
    url = request.url
    if request.method != "GET":
        raise ForbiddenRequest(f"method not allowed: {request.method}")
    if url.scheme != "https":
        raise ForbiddenRequest(f"scheme not allowed: {url.scheme}")
    if url.port not in (None, 443):
        raise ForbiddenRequest(f"port not allowed: {url.port}")
    if url.userinfo or url.fragment:
        raise ForbiddenRequest("credentials and fragments are not allowed in the URL")
    for header in request.headers:
        if header.lower() in CREDENTIAL_HEADERS or header.lower().startswith("kc-api-"):
            raise ForbiddenRequest(f"credential header not allowed: {header}")
    if request.headers.get("content-length", "0") != "0" or request.headers.get(
        "transfer-encoding"
    ):
        raise ForbiddenRequest("GET requests must not carry a body")
    route = find_route(url.host, url.path)
    if route is None:
        raise ForbiddenRequest(f"route not in the whitelist: {url.host}{url.path}")
    for name, value in url.params.multi_items():
        if name not in route.params:
            raise ForbiddenRequest(f"{route.name}: query parameter not allowed: {name}")
        if not SAFE_VALUE.fullmatch(value):
            raise ForbiddenRequest(f"{route.name}: unsafe value for {name}")
    return route


class GuardTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        check_request(request)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


class KucoinRestClient:
    def __init__(
        self,
        *,
        limiter: RestRateLimiter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
        timeout_seconds: float = 10.0,
        max_attempts: int = 4,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.limiter = limiter or shared_limiter()
        self._clock = clock or SystemClock()
        self._max_attempts = max_attempts
        inner = transport or httpx.AsyncHTTPTransport(retries=0)
        self._http = httpx.AsyncClient(
            transport=GuardTransport(inner),
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": USER_AGENT},
            # A bare CookieJar: httpx would copy an httpx.Cookies into a jar with the default
            # policy, which keeps the cookies the server sets.
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        )

    async def __aenter__(self) -> KucoinRestClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(
        self,
        route_name: str,
        *,
        path: Mapping[str, str] | None = None,
        params: Mapping[str, str | int] | None = None,
    ) -> RestResponse:
        route = ROUTES.get(route_name)
        if route is None:
            raise ForbiddenRequest(f"unknown route: {route_name}")
        query = {name: str(value) for name, value in (params or {}).items()}
        url = f"https://{route.host}{route.build_path(path or {})}"

        code: str | None = None
        for _attempt in range(self._max_attempts):
            await self.limiter.acquire(route.weight)
            response = await self._http.get(url, params=query)
            headers = _ratelimit_headers(response.headers)
            if headers is not None:
                self.limiter.observe(headers)
            body = _json_or_none(response.content)
            code = body.get("code") if isinstance(body, dict) else None

            if response.status_code == 429 or code in (QUOTA_OR_OVERLOAD_CODE, SYSTEM_LIMIT_CODE):
                if code == QUOTA_OR_OVERLOAD_CODE and headers is not None:
                    self.limiter.quota_exhausted(headers)
                else:
                    self.limiter.overload()
                continue
            if response.status_code != 200 or code != SUCCESS_CODE:
                message = body.get("msg", "") if isinstance(body, dict) else response.text[:200]
                raise KucoinApiError(route.name, response.status_code, code, str(message))

            self.limiter.success()
            return RestResponse(
                route=route.name,
                url=str(response.url),
                status=response.status_code,
                data=body.get("data") if isinstance(body, dict) else None,
                ratelimit=headers,
                received_at=self._clock.now(),
                text=response.text,
            )
        raise KucoinApiError(route.name, 429, code, f"limited {self._max_attempts} times in a row")


def _json_or_none(content: bytes) -> Any:
    if not content:
        return None
    try:
        return loads(content)
    except ValueError:
        return None


def _ratelimit_headers(headers: httpx.Headers) -> RateLimitHeaders | None:
    try:
        return RateLimitHeaders(
            limit=int(headers["gw-ratelimit-limit"]),
            remaining=int(headers["gw-ratelimit-remaining"]),
            reset_ms=int(headers["gw-ratelimit-reset"]),
        )
    except (KeyError, ValueError):
        return None
