"""The download of the history through the guarded client of E1, against a fake KuCoin:
requests within the limits, answers and failures, retries, and a download that resumes
without touching closed days (02 §12.1, points 1 and 2; F3-A1)."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from kbot.history.coverage import Presence, Window, coverage, presence
from kbot.history.download import (
    FUTURES_MINUTES_SINCE,
    Downloader,
    Instrument,
    chunks,
    closed_until,
    window_for,
)
from kbot.history.store import read_candles, read_record
from kbot.kucoin.ratelimit import RestRateLimiter
from kbot.kucoin.rest import KucoinRestClient

Factory = Callable[..., KucoinRestClient]
DAY = date(2026, 11, 10)
START = datetime(2026, 11, 10, tzinfo=UTC)
NOW = START + timedelta(days=1, hours=3)


def row(moment: datetime) -> list[object]:
    return [int(moment.timestamp()), "100", "101", "99", "100.5", "2", "201"]


@dataclass
class FakeKlines(httpx.AsyncBaseTransport):
    """KuCoin's candles of 1 minute: rows by (market, symbol), newest first. A request can
    be made to fail, a number of times, by (symbol, first second asked)."""

    rows: dict[tuple[str, str], dict[int, list[object]]] = field(default_factory=dict)
    failures: dict[tuple[str, int], int] = field(default_factory=dict)
    unreadable: set[tuple[str, int]] = field(default_factory=set)
    asked: list[tuple[str, int, int]] = field(default_factory=list)

    def add(self, market: str, symbol: str, start: datetime, minutes: int, skip: set[int]) -> None:
        held = self.rows.setdefault((market, symbol), {})
        for i in range(minutes):
            if i not in skip:
                moment = start + timedelta(minutes=i)
                held[int(moment.timestamp())] = row(moment)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        assert request.url.path.endswith("/market/kline"), request.url.path
        start, end = int(q["startAt"]), int(q["endAt"])
        key = (q["symbol"], start)
        self.asked.append((q["symbol"], start, end))
        if self.failures.get(key, 0) > 0:
            self.failures[key] -= 1
            raise httpx.ConnectError("unreachable (simulated)", request=request)
        rows = [
            r
            for s, r in self.rows.get((q["tradeType"], q["symbol"]), {}).items()
            if start <= s <= end
        ]
        rows.sort(key=lambda r: -int(r[0]))  # type: ignore[call-overload]
        if key in self.unreadable:
            rows.append(["not", "a", "row"])
        data = {"tradeType": q["tradeType"], "symbol": q["symbol"], "list": rows}
        return httpx.Response(200, json={"code": "200000", "data": data})


async def no_wait(_seconds: float) -> None:
    return None


def run(
    test_client: Factory, fake: FakeKlines, root: Path, instrument: Instrument, window: Window
) -> list[object]:
    async def go() -> list[object]:
        client = test_client(fake, RestRateLimiter(pool_weight=10**6))
        try:
            downloader = Downloader(client, root, sleep=no_wait)
            return list(await downloader.instrument(instrument, window, NOW))
        finally:
            await client.aclose()

    return asyncio.run(go())


def test_a_spot_day_is_one_request_and_a_futures_day_eight(
    test_client: Factory, tmp_path: Path
) -> None:
    """At most 1500 candles per request in Spot and 200 in Futures, with one minute more on
    each side: 1440 minutes are one request in Spot and eight in Futures."""
    fake = FakeKlines()
    fake.add("SPOT", "BTC-USDT", START, 1440, set())
    fake.add("FUTURES", "XBTUSDTM", START, 1440, set())
    whole = Window(START, START + timedelta(days=1))
    (spot,) = run(test_client, fake, tmp_path, Instrument("SPOT", "BTC-USDT"), whole)
    (futures,) = run(test_client, fake, tmp_path, Instrument("FUTURES", "XBTUSDTM"), whole)
    asked = {
        symbol: [a for a in fake.asked if a[0] == symbol] for symbol in ("BTC-USDT", "XBTUSDTM")
    }
    assert len(asked["BTC-USDT"]) == 1 and len(asked["XBTUSDTM"]) == 8
    for _symbol, start, end in fake.asked:
        assert (end - start) // 60 + 1 <= {"BTC-USDT": 1500, "XBTUSDTM": 200}[_symbol]
    assert spot.closed and futures.closed  # type: ignore[attr-defined]
    assert len(read_candles(tmp_path, "FUTURES", "XBTUSDTM", DAY)) == 1440


def test_minutes_without_a_candle_are_absent_and_the_window_is_whole(
    test_client: Factory, tmp_path: Path
) -> None:
    """CD-13 through the download: the answer lacks the first two and the last minutes."""
    fake = FakeKlines()
    fake.add("SPOT", "BTC-USDT", START, 1440, {0, 1, 1439})
    whole = Window(START, START + timedelta(days=1))
    run(test_client, fake, tmp_path, Instrument("SPOT", "BTC-USDT"), whole)
    record = read_record(tmp_path, "SPOT", "BTC-USDT", DAY)
    assert record is not None and record.closed
    present = [c.open_time for c in read_candles(tmp_path, "SPOT", "BTC-USDT", DAY)]
    states = presence(record.window, record.requests, present)
    report = coverage(states, [])
    assert report.total(Presence.AUSENTE) == 3
    assert (report.numerator, report.denominator) == (1437, 1440)


def test_a_request_that_keeps_failing_leaves_its_minutes_for_the_next_run(
    test_client: Factory, tmp_path: Path
) -> None:
    """CD-11 through the download: the third of the eight requests of a Futures day fails
    three times. Its minutes stay no_descargado and the day open; the next run asks only
    for the open day again and closes it, and never asks for a closed day."""
    fake = FakeKlines()
    fake.add("FUTURES", "XBTUSDTM", START, 2880, set())
    window = Window(START - timedelta(days=1), START + timedelta(days=1))
    fake.add("FUTURES", "XBTUSDTM", START - timedelta(days=1), 1440, set())
    third = int((START + timedelta(minutes=2 * 198) - timedelta(minutes=1)).timestamp())
    fake.failures[("XBTUSDTM", third)] = 3
    first, second = run(test_client, fake, tmp_path, Instrument("FUTURES", "XBTUSDTM"), window)
    assert first.closed and not second.closed  # type: ignore[attr-defined]
    states = presence(
        second.window,
        second.requests,
        [c.open_time for c in read_candles(tmp_path, "FUTURES", "XBTUSDTM", DAY)],
    )  # type: ignore[attr-defined]
    assert list(states.values()).count(Presence.NO_DESCARGADO) == 198
    asked_before = len(fake.asked)
    first_again, second_again = run(
        test_client, fake, tmp_path, Instrument("FUTURES", "XBTUSDTM"), window
    )
    assert second_again.closed  # type: ignore[attr-defined]
    assert first_again == first, "the closed day is not touched"
    new = fake.asked[asked_before:]
    assert all(start >= int(START.timestamp()) - 60 for _s, start, _e in new), "only the open day"


def test_a_failure_then_an_answer_and_an_unreadable_answer_are_retried(
    test_client: Factory, tmp_path: Path
) -> None:
    fake = FakeKlines()
    fake.add("SPOT", "BTC-USDT", START, 1440, set())
    first_second = int(START.timestamp()) - 60
    fake.failures[("BTC-USDT", first_second)] = 1
    fake.unreadable.add(("ETH-USDT", first_second))
    whole = Window(START, START + timedelta(days=1))
    (day,) = run(test_client, fake, tmp_path, Instrument("SPOT", "BTC-USDT"), whole)
    assert day.closed  # type: ignore[attr-defined]
    assert len([a for a in fake.asked if a[0] == "BTC-USDT"]) == 2
    (eth,) = run(test_client, fake, tmp_path, Instrument("SPOT", "ETH-USDT"), whole)
    assert not eth.closed, "an unreadable row spoils its answer, three times"  # type: ignore[attr-defined]
    assert len([a for a in fake.asked if a[0] == "ETH-USDT"]) == 3


def test_the_window_ends_at_the_last_closed_minute_and_futures_starts_in_2025() -> None:
    now = datetime(2026, 11, 18, 12, 0, 10, tzinfo=UTC)
    assert closed_until(now) == datetime(2026, 11, 18, 11, 59, tzinfo=UTC), "the margin of 15 s"
    spot = window_for("SPOT", 730, now)
    futures = window_for("FUTURES", 730, now)
    assert spot.start == datetime(2024, 11, 18, tzinfo=UTC)
    assert futures.start == FUTURES_MINUTES_SINCE
    assert spot.end == futures.end == closed_until(now)


@pytest.mark.parametrize("size", [1, 198, 1498])
def test_the_stretches_cover_the_window_exactly(size: int) -> None:
    window = Window(START, START + timedelta(minutes=1440))
    parts = list(chunks(window, size))
    assert parts[0][0] == window.start and parts[-1][1] == window.end - timedelta(minutes=1)
    for (_f1, l1), (f2, _l2) in itertools.pairwise(parts):
        assert f2 - l1 == timedelta(minutes=1)
    assert all((last - first) // timedelta(minutes=1) + 1 <= size for first, last in parts)
