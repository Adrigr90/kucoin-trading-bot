"""The searches of tools/kucoin_history.py (07 §2, stage E3; 04, B3 and B13), against a
fake KuCoin: no network."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from kbot.kucoin.rest import KucoinApiError
from tools.kucoin_history import (
    DAY_S,
    HOUR_MS,
    candle_range,
    first_window,
    last_window,
    oldest_index_value,
)

NOW = datetime(2026, 11, 9, 12, 54, tzinfo=UTC)


def seconds(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp())


@dataclass
class Answer:
    data: Any
    text: str = ""


class FakeKucoin:
    """Index values every second from ``index_from`` to now; one-minute candles of each
    listed pair between its two moments; any other pair is unsupported, as KuCoin says."""

    def __init__(self, index_from: int, pairs: dict[str, tuple[int, int]]) -> None:
        self.index_from_ms = index_from * 1000
        self.pairs = pairs
        self.requests = 0

    async def get(self, route: str, *, params: dict[str, Any]) -> Answer:
        self.requests += 1
        start, end = int(params["startAt"]), int(params["endAt"])
        if route == "futures.index":
            first = max(start, self.index_from_ms)
            if first > min(end, int(NOW.timestamp() * 1000)):
                return Answer({"dataList": [], "hasMore": False})
            point = {"symbol": params["symbol"], "timePoint": first, "value": 1}
            return Answer({"dataList": [point], "hasMore": True})
        listed = self.pairs.get(params["symbol"])
        if listed is None:
            raise KucoinApiError(route, 200, "138004", "Unsupported trading pair.")
        first, last = max(start, listed[0]), min(end, listed[1])
        moments = [] if first > last else [first - first % 60, last - last % 60]
        return Answer({"symbol": params["symbol"], "list": [[str(m), "1"] for m in moments]})


def test_the_first_and_the_last_window_with_data_are_found_by_bisection() -> None:
    begins, ends = 1_000_123, 1_500_000

    async def probe(start: int) -> list[int]:
        calls.append(start)
        return [start] if begins < start + 100 and start <= ends else []

    calls: list[int] = []
    assert asyncio.run(first_window(probe, 0, 1_400_000, 100)) == 1_000_100
    assert len(calls) <= 15, "about log2(14,000) probes"
    calls.clear()
    assert asyncio.run(last_window(probe, 1_100_000, 2_000_000, 100)) == 1_500_000
    assert len(calls) <= 14


def test_the_oldest_index_value_is_found_to_the_second() -> None:
    """B3: Classic's index history began on 31 July 2026 at 16:00:00 UTC when measured."""
    kucoin = FakeKucoin(seconds(2026, 7, 31, 16), {})
    answer = asyncio.run(oldest_index_value(kucoin, ".KXBTUSDT", int(NOW.timestamp() * 1000)))  # type: ignore[arg-type]
    assert answer == ".KXBTUSDT: oldest value at 2026-07-31 16:00:00 UTC"
    assert kucoin.requests <= 25, "a bisection over hours since 2017"
    assert HOUR_MS == 3_600_000


def test_the_candles_of_a_delisted_pair_are_found_from_listing_to_delisting() -> None:
    """B13: a pair delisted three weeks ago still has its whole history; one that KuCoin
    no longer supports has none."""
    kucoin = FakeKucoin(0, {"BLAST-USDT": (seconds(2024, 6, 26, 15), seconds(2026, 10, 16, 8, 2))})
    now = int(NOW.timestamp())
    answer = asyncio.run(candle_range(kucoin, "SPOT", "BLAST-USDT", now))  # type: ignore[arg-type]
    assert (
        answer == "SPOT BLAST-USDT: candles from 2024-06-26 15:00:00 UTC to 2026-10-16 08:02:00 UTC"
    )
    gone = asyncio.run(candle_range(kucoin, "SPOT", "REN-USDT", now))  # type: ignore[arg-type]
    assert gone.startswith("SPOT REN-USDT: no candles:") and "138004" in gone


def test_a_pair_listed_for_a_few_weeks_long_ago_is_found() -> None:
    """The search back from now steps 30 days at a time, so a pair listed for 40 days two
    years ago is not skipped."""
    listed = (seconds(2024, 3, 1), seconds(2024, 4, 10))
    kucoin = FakeKucoin(0, {"SHORT-USDT": listed})
    answer = asyncio.run(candle_range(kucoin, "SPOT", "SHORT-USDT", int(NOW.timestamp())))  # type: ignore[arg-type]
    assert (
        answer == "SPOT SHORT-USDT: candles from 2024-03-01 00:00:00 UTC to 2024-04-10 00:00:00 UTC"
    )
    assert DAY_S == 86_400
