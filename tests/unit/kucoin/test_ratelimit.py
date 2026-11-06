"""Acceptance A2 (07 §2 E1): at most half of the pool, waits on 429, never fast retries."""

from __future__ import annotations

import asyncio
import bisect
import itertools
import random

import httpx
import pytest

from kbot.kucoin.ratelimit import (
    MAX_BACKOFF_SECONDS,
    MIN_BACKOFF_SECONDS,
    POOL_WEIGHT,
    RateLimitHeaders,
    RestRateLimiter,
    shared_limiter,
)
from kbot.kucoin.rest import KucoinApiError, KucoinRestClient

KLINE = {"tradeType": "SPOT", "symbol": "BTC-USDT", "interval": "1min"}


class FakeTime:
    """A monotonic clock whose sleeps advance time instantly and are recorded."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds


def limiter(fake: FakeTime, seed: int = 7) -> RestRateLimiter:
    return RestRateLimiter(clock=fake.clock, sleep=fake.sleep, rng=random.Random(seed))


def test_the_bot_uses_half_of_the_pool() -> None:
    fake = FakeTime()
    bucket = limiter(fake)
    assert bucket.capacity == POOL_WEIGHT / 2

    async def spend(total: int) -> None:
        for _ in range(total):
            await bucket.acquire(5)

    asyncio.run(spend(200))  # 1,000 weight: the whole share, no wait
    assert fake.sleeps == []
    start = fake.now
    asyncio.run(spend(400))  # another 2,000 weight must take two more windows
    assert fake.now - start == pytest.approx(60.0)


def test_any_30_second_window_stays_within_the_share() -> None:
    fake = FakeTime()
    bucket = limiter(fake)
    spent: list[tuple[float, int]] = []

    async def spend() -> None:
        rng = random.Random(3)
        for _ in range(3000):
            weight = rng.choice((2, 3, 4, 5, 15))
            await bucket.acquire(weight)
            spent.append((fake.now, weight))
            fake.now += rng.choice((0.0, 0.0, 0.01, 0.2))

    asyncio.run(spend())
    assert fake.now - spent[0][0] > 300  # long enough to cross many windows
    times = [t for t, _ in spent]
    prefix = list(itertools.accumulate((w for _, w in spent), initial=0))
    worst = max(
        prefix[bisect.bisect_left(times, start + 30.0)] - prefix[i] for i, start in enumerate(times)
    )
    assert POOL_WEIGHT / 2 - 15 <= worst <= POOL_WEIGHT / 2


def test_weights_outside_the_share_are_rejected() -> None:
    bucket = limiter(FakeTime())
    for weight in (0, -1, 1001):
        with pytest.raises(ValueError, match="weight"):
            asyncio.run(bucket.acquire(weight))


def test_server_headers_showing_the_share_used_pause_everything() -> None:
    fake = FakeTime()
    bucket = limiter(fake)
    bucket.observe(RateLimitHeaders(limit=2000, remaining=1500, reset_ms=9000))
    assert bucket.blocked_until == 0.0
    bucket.observe(RateLimitHeaders(limit=2000, remaining=1000, reset_ms=9000))
    assert bucket.blocked_until == pytest.approx(fake.now + 9.5)
    asyncio.run(bucket.acquire(3))
    assert fake.sleeps == [pytest.approx(9.5)]


def test_overload_backoff_is_exponential_random_and_bounded() -> None:
    fake = FakeTime()
    bucket = limiter(fake)
    delays = [bucket.overload() for _ in range(12)]
    caps = [min(MAX_BACKOFF_SECONDS, MIN_BACKOFF_SECONDS * 2**n) for n in range(12)]
    for delay, cap in zip(delays, caps, strict=True):
        assert max(MIN_BACKOFF_SECONDS, cap / 2) <= delay <= cap
    assert min(delays) >= MIN_BACKOFF_SECONDS
    assert max(delays) <= MAX_BACKOFF_SECONDS
    assert delays[-1] >= MAX_BACKOFF_SECONDS / 2
    bucket.success()
    assert bucket.overload() <= MIN_BACKOFF_SECONDS


class Scripted(httpx.AsyncBaseTransport):
    def __init__(self, fake: FakeTime, responses: list[httpx.Response]) -> None:
        self.fake = fake
        self.responses = responses
        self.sent_at: list[float] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.sent_at.append(self.fake.now)
        return self.responses.pop(0)


def ok() -> httpx.Response:
    headers = {
        "gw-ratelimit-limit": "2000",
        "gw-ratelimit-remaining": "1997",
        "gw-ratelimit-reset": "29000",
    }
    return httpx.Response(200, json={"code": "200000", "data": []}, headers=headers)


def limited(code: str, *, status: int = 429, reset_ms: int | None = None) -> httpx.Response:
    headers = {}
    if reset_ms is not None:
        headers = {
            "gw-ratelimit-limit": "2000",
            "gw-ratelimit-remaining": "0",
            "gw-ratelimit-reset": str(reset_ms),
        }
    return httpx.Response(status, json={"code": code, "msg": "Too Many Requests"}, headers=headers)


def run_client(fake: FakeTime, transport: Scripted, max_attempts: int = 4) -> object:
    async def go() -> object:
        async with KucoinRestClient(
            limiter=limiter(fake), transport=transport, max_attempts=max_attempts
        ) as client:
            return await client.get("uta.v2.kline", params=KLINE)

    return asyncio.run(go())


def test_quota_exhausted_waits_until_the_reset() -> None:
    fake = FakeTime()
    transport = Scripted(fake, [limited("429000", reset_ms=12000), ok()])
    run_client(fake, transport)
    assert transport.sent_at[1] - transport.sent_at[0] == pytest.approx(12.5)


def test_overload_without_headers_backs_off_at_least_five_seconds() -> None:
    fake = FakeTime()
    responses = [limited("429000"), limited("429001", status=200), limited("429000"), ok()]
    transport = Scripted(fake, responses)
    run_client(fake, transport)
    gaps = [b - a for a, b in zip(transport.sent_at, transport.sent_at[1:], strict=False)]
    assert len(gaps) == 3
    assert all(gap >= MIN_BACKOFF_SECONDS for gap in gaps)
    assert gaps[2] >= 10.0  # third consecutive overload: cap 20 s, at least half


def test_http_429_without_a_kucoin_code_is_an_overload() -> None:
    fake = FakeTime()
    transport = Scripted(fake, [httpx.Response(429, text="<html>busy</html>"), ok()])
    run_client(fake, transport)
    assert transport.sent_at[1] - transport.sent_at[0] >= MIN_BACKOFF_SECONDS


def test_the_client_gives_up_after_max_attempts() -> None:
    fake = FakeTime()
    transport = Scripted(fake, [limited("429001") for _ in range(3)])
    with pytest.raises(KucoinApiError, match="limited 3 times"):
        run_client(fake, transport, max_attempts=3)
    assert len(transport.sent_at) == 3


def test_other_errors_are_raised_without_retry() -> None:
    fake = FakeTime()
    body = {"code": "400100", "msg": "Parameter error"}
    transport = Scripted(fake, [httpx.Response(400, json=body), ok()])
    with pytest.raises(KucoinApiError, match="400100"):
        run_client(fake, transport)
    assert len(transport.sent_at) == 1


def test_success_returns_data_and_headers() -> None:
    fake = FakeTime()
    transport = Scripted(fake, [ok()])
    response = run_client(fake, transport)
    assert response.data == []  # type: ignore[attr-defined]
    assert response.ratelimit == RateLimitHeaders(2000, 1997, 29000)  # type: ignore[attr-defined]


class SharedTime:
    """Fake time for concurrent tasks: a sleep moves time forward and yields."""

    def __init__(self) -> None:
        self.now = 1000.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.now = max(self.now, self.now + seconds)
        await asyncio.sleep(0)


class Stamped(httpx.AsyncBaseTransport):
    """Records when each request reaches the network, and its weight."""

    def __init__(self, fake: SharedTime, log: list[tuple[float, int]]) -> None:
        self.fake = fake
        self.log = log

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.log.append((self.fake.now, 15))
        await asyncio.sleep(0)
        return httpx.Response(200, json={"code": "200000", "data": []})


def worst_window(log: list[tuple[float, int]]) -> int:
    log = sorted(log)
    times = [t for t, _ in log]
    prefix = list(itertools.accumulate((w for _, w in log), initial=0))
    return max(prefix[bisect.bisect_left(times, t + 30.0)] - prefix[i] for i, t in enumerate(times))


def two_clients(shared: bool) -> int:
    """Two clients sending ticker requests (weight 15) concurrently; worst 30 s window."""
    fake = SharedTime()
    log: list[tuple[float, int]] = []
    common = RestRateLimiter(clock=fake.clock, sleep=fake.sleep)

    def budget() -> RestRateLimiter:
        return common if shared else RestRateLimiter(clock=fake.clock, sleep=fake.sleep)

    async def client_load(client: KucoinRestClient) -> None:
        for _ in range(150):
            await client.get("uta.v2.ticker", params={"tradeType": "SPOT", "symbol": "BTC-USDT"})

    async def go() -> None:
        async with (
            KucoinRestClient(limiter=budget(), transport=Stamped(fake, log)) as first,
            KucoinRestClient(limiter=budget(), transport=Stamped(fake, log)) as second,
        ):
            await asyncio.gather(client_load(first), client_load(second))

    asyncio.run(go())
    assert len(log) == 300
    return worst_window(log)


def test_a2_01_clients_of_one_process_share_one_budget() -> None:
    """A2-01: two clients together never exceed 1,000 weight in any 30 s window."""
    assert two_clients(shared=True) <= POOL_WEIGHT / 2
    # The same load with a budget per client would reach the whole pool: the test above
    # would catch it.
    assert two_clients(shared=False) > POOL_WEIGHT / 2


def test_a2_01_clients_use_the_shared_budget_by_default() -> None:
    async def go() -> None:
        async with KucoinRestClient() as first, KucoinRestClient() as second:
            assert first.limiter is second.limiter is shared_limiter()

    asyncio.run(go())
