"""Weight budget for KuCoin's public REST pool (spec 02 §3.1).

The public pool is 2,000 weight every 30 s per IP [verified with the gw-ratelimit-*
headers]. The bot uses at most half of it. The server headers are the source of truth:
when they show that the IP has already used its share, every request waits for the reset.

Error 429000 has two causes that are handled differently:

* quota exhausted: the headers say when the pool resets, and all requests wait until then;
* server overload: the headers carry no rate-limit information, and all requests wait
  with exponential, randomized backoff from 5 s to 5 min. There are never fast retries.
"""

from __future__ import annotations

import asyncio
import functools
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

POOL_WEIGHT = 2000
WINDOW_SECONDS = 30.0
SHARE = 0.5
MIN_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 300.0
RESET_MARGIN_SECONDS = 0.5


@dataclass(frozen=True)
class RateLimitHeaders:
    limit: int
    remaining: int
    reset_ms: int


class RestRateLimiter:
    def __init__(
        self,
        *,
        pool_weight: int = POOL_WEIGHT,
        window_seconds: float = WINDOW_SECONDS,
        share: float = SHARE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if not 0 < share <= 1:
            raise ValueError("share must be in (0, 1]")
        self.capacity = pool_weight * share
        self.share = share
        self._window = window_seconds
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.SystemRandom()
        self._spent: deque[tuple[float, int]] = deque()
        self._blocked_until = 0.0
        self._overloads = 0

    @property
    def blocked_until(self) -> float:
        return self._blocked_until

    async def acquire(self, weight: int) -> None:
        """Wait until ``weight`` fits in the share over the last ``window`` seconds.

        A sliding window, not a token bucket: a full bucket plus its refill would allow
        twice the share inside one window.
        """
        if not 0 < weight <= self.capacity:
            raise ValueError(f"weight {weight} outside (0, {self.capacity}]")
        while True:
            now = self._clock()
            if now < self._blocked_until:
                await self._sleep(self._blocked_until - now)
                continue
            while self._spent and now - self._spent[0][0] >= self._window:
                self._spent.popleft()
            excess = sum(w for _, w in self._spent) + weight - self.capacity
            if excess <= 0:
                self._spent.append((now, weight))
                return
            freed = 0
            for spent_at, spent in self._spent:
                freed += spent
                if freed >= excess:
                    await self._sleep(spent_at + self._window - now)
                    break

    def observe(self, headers: RateLimitHeaders) -> None:
        """Pause until the reset when the server shows that the share is already used."""
        used = headers.limit - headers.remaining
        if used >= headers.limit * self.share:
            self._block_for(headers.reset_ms / 1000 + RESET_MARGIN_SECONDS)

    def quota_exhausted(self, headers: RateLimitHeaders) -> float:
        """429000 with rate-limit headers: wait until the pool resets."""
        delay = headers.reset_ms / 1000 + RESET_MARGIN_SECONDS
        self._block_for(delay)
        return delay

    def overload(self) -> float:
        """429000 without rate-limit headers, or 429001: exponential randomized backoff."""
        cap = min(MAX_BACKOFF_SECONDS, MIN_BACKOFF_SECONDS * 2**self._overloads)
        delay = self._rng.uniform(max(MIN_BACKOFF_SECONDS, cap / 2), cap)
        self._overloads += 1
        self._block_for(delay)
        return delay

    def success(self) -> None:
        self._overloads = 0

    def _block_for(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, self._clock() + seconds)


@functools.cache
def shared_limiter() -> RestRateLimiter:
    """The one budget of this process. Every REST client uses it unless a test injects
    its own, so several clients can never add up more than the share of the IP.

    Between processes on the same IP, the server headers are the shared source of truth:
    ``observe`` stops every client as soon as the IP as a whole has used the share.
    """
    return RestRateLimiter()
