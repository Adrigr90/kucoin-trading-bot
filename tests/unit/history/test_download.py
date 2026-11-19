"""The download of the history through the guarded client of E1, against a fake KuCoin:
requests within the limits, answers and failures, retries, and a download that resumes
without touching closed days (02 §12.1, points 1 and 2; F3-A1)."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
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
from kbot.history.report import instrument_report
from kbot.history.store import SETTLING, DayRecord, read_candles, read_record, requested
from kbot.kucoin.ratelimit import RestRateLimiter
from kbot.kucoin.rest import KucoinRestClient

Factory = Callable[..., KucoinRestClient]
DAY = date(2026, 11, 10)
START = datetime(2026, 11, 10, tzinfo=UTC)
END = START + timedelta(days=1)
NOW = END + SETTLING + timedelta(hours=3)  # the days of the tests can close
# What KuCoin answered on 19 November 2026 for a whole day of Spot DOT-USDT: two candles
# for 09:03 UTC, the full one and then a partial one (here, on the day of the tests).
NINE = START + timedelta(hours=9, minutes=3)
FULL = [int(NINE.timestamp()), "0.7317", "0.7317", "0.7309", "0.7314", "5281.1148", "3862.5702601"]
PARTIAL = [int(NINE.timestamp()), "0.7317", "0.7317", "0.7316", "0.7316", "108.8588", "79.6482513"]


def row(moment: datetime) -> list[object]:
    return [int(moment.timestamp()), "100", "101", "99", "100.5", "2", "201"]


class Interrupted(BaseException):
    """The process stops in the middle of a download, as with Ctrl+C or a power cut."""


@dataclass
class FakeKlines(httpx.AsyncBaseTransport):
    """KuCoin's candles of 1 minute: rows by (market, symbol), newest first. A request can
    be made to fail, a number of times, by (symbol, first second asked)."""

    rows: dict[tuple[str, str], dict[int, list[object]]] = field(default_factory=dict)
    # More rows for a minute that already has one, after it, as KuCoin sometimes sends.
    repeated: dict[tuple[str, str], list[list[object]]] = field(default_factory=dict)
    failures: dict[tuple[str, int], int] = field(default_factory=dict)
    unreadable: set[tuple[str, int]] = field(default_factory=set)
    interrupt: set[tuple[str, int]] = field(default_factory=set)  # the process stops there
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
        if key in self.interrupt:
            raise Interrupted
        if self.failures.get(key, 0) > 0:
            self.failures[key] -= 1
            raise httpx.ConnectError("unreachable (simulated)", request=request)
        key_rows = (q["tradeType"], q["symbol"])
        rows = [r for s, r in self.rows.get(key_rows, {}).items() if start <= s <= end]
        rows += [r for r in self.repeated.get(key_rows, []) if start <= int(r[0]) <= end]  # type: ignore[call-overload]
        rows.sort(key=lambda r: -int(r[0]))  # type: ignore[call-overload]  # stable: repeats after
        if key in self.unreadable:
            rows.append(["not", "a", "row"])
        data = {"tradeType": q["tradeType"], "symbol": q["symbol"], "list": rows}
        return httpx.Response(200, json={"code": "200000", "data": data})


async def no_wait(_seconds: float) -> None:
    return None


def run(
    test_client: Factory,
    fake: FakeKlines,
    root: Path,
    instrument: Instrument,
    window: Window,
    now: datetime = NOW,
) -> list[DayRecord]:
    async def go() -> list[DayRecord]:
        client = test_client(fake, RestRateLimiter(pool_weight=10**6))
        try:
            downloader = Downloader(client, root, sleep=no_wait)
            return await downloader.instrument(instrument, window, now)
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
    assert spot.closed and futures.closed
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
    assert first.closed and not second.closed
    states = presence(
        second.window,
        second.requests,
        [c.open_time for c in read_candles(tmp_path, "FUTURES", "XBTUSDTM", DAY)],
    )
    assert list(states.values()).count(Presence.NO_DESCARGADO) == 198
    asked_before = len(fake.asked)
    first_again, second_again = run(
        test_client, fake, tmp_path, Instrument("FUTURES", "XBTUSDTM"), window
    )
    assert second_again.closed
    assert first_again == first, "the closed day is not touched"
    new = fake.asked[asked_before:]
    assert all(start >= int(START.timestamp()) - 60 for _s, start, _e in new), "only the open day"


def test_an_interrupted_download_leaves_its_window_whole_in_the_report(
    test_client: Factory, tmp_path: Path
) -> None:
    """F3E1-02 through the download: the process stops while asking for the second of
    three days. The window was written before asking anything, so the report counts the
    three days: the first present and the other two no_descargado, never 1440/1440. The
    next run fills them."""
    fake = FakeKlines()
    fake.add("SPOT", "BTC-USDT", START, 3 * 1440, set())
    window = Window(START, START + timedelta(days=3))
    fake.interrupt.add(("BTC-USDT", int((START + timedelta(days=1)).timestamp()) - 60))
    with pytest.raises(Interrupted):
        run(test_client, fake, tmp_path, Instrument("SPOT", "BTC-USDT"), window)
    assert requested(tmp_path, "SPOT", "BTC-USDT") == [window]
    report = instrument_report(tmp_path, "SPOT", "BTC-USDT", [])
    assert (report.total.numerator, report.total.denominator) == (1440, 3 * 1440)
    day = timedelta(days=1)
    assert report.total.failed == [(START + day, START + 2 * day), (START + 2 * day, END + 2 * day)]
    assert len(report.days) == 3 and report.invalid == []

    fake.interrupt.clear()
    run(test_client, fake, tmp_path, Instrument("SPOT", "BTC-USDT"), window)
    report = instrument_report(tmp_path, "SPOT", "BTC-USDT", [])
    assert (report.total.numerator, report.total.denominator) == (3 * 1440, 3 * 1440)


def test_two_different_candles_for_a_minute_leave_it_in_conflict(
    test_client: Factory, tmp_path: Path
) -> None:
    """CD-19, with what KuCoin answered on 19 November 2026 for a whole day of Spot
    DOT-USDT: two candles for 09:03 UTC, the full one and then a partial one. Neither is
    used or chosen, both go to the record exactly, the rest of the day counts, the day
    closes (downloaded 48 h after its end) and the download goes on; the next run asks
    nothing. Two identical candles, also sent twice, are one."""
    fake = FakeKlines()
    fake.add("SPOT", "DOT-USDT", START, 1440, set())
    fake.rows[("SPOT", "DOT-USDT")][int(NINE.timestamp())] = FULL
    fake.repeated[("SPOT", "DOT-USDT")] = [PARTIAL, row(START + timedelta(minutes=5))]
    whole = Window(START, START + timedelta(days=1))
    (day,) = run(test_client, fake, tmp_path, Instrument("SPOT", "DOT-USDT"), whole)

    assert day.closed
    (conflict,) = day.conflicts
    assert conflict.minute == NINE
    assert [[c.open, c.high, c.low, c.close, c.volume, c.turnover] for c in conflict.candles] == [
        [Decimal(str(v)) for v in kucoin[1:]] for kucoin in (FULL, PARTIAL)
    ], "both, as KuCoin sent them and in its order"
    held = read_candles(tmp_path, "SPOT", "DOT-USDT", DAY)
    assert len(held) == 1439 and NINE not in {c.open_time for c in held}
    assert read_record(tmp_path, "SPOT", "DOT-USDT", DAY) == day, "exact through the record"
    states = presence(day.window, day.requests, [c.open_time for c in held], day.in_conflict)
    assert states[NINE] is Presence.EN_CONFLICTO
    report = coverage(states, [])
    assert report.in_conflict == [NINE] and report.failed == []
    assert (report.numerator, report.denominator) == (1439, 1440)
    asked = len(fake.asked)
    run(test_client, fake, tmp_path, Instrument("SPOT", "DOT-USDT"), whole)
    assert len(fake.asked) == asked, "settled: the closed day is not asked again"


def test_a_recent_day_stays_open_and_takes_kucoin_s_correction(
    test_client: Factory, tmp_path: Path
) -> None:
    """CD-20, as it happened: the DOT-USDT day of 18 November 2026 was downloaded 8.6 h
    after its end with two candles for 09:03 UTC, and at 22:10 UTC on 19 November KuCoin
    gave only the full one. Downloaded before 48 h, the day stays open with its conflict;
    the next download asks for it again and keeps the new answer; from 48 h on it closes
    and is never asked again."""
    fake = FakeKlines()
    fake.add("SPOT", "DOT-USDT", START, 1440, set())
    fake.rows[("SPOT", "DOT-USDT")][int(NINE.timestamp())] = FULL
    fake.repeated[("SPOT", "DOT-USDT")] = [PARTIAL]
    whole = Window(START, END)
    dot = Instrument("SPOT", "DOT-USDT")

    (early,) = run(test_client, fake, tmp_path, dot, whole, END + timedelta(hours=8, minutes=36))
    assert not early.closed and early.in_conflict == [NINE] and early.rows == 1439
    fake.repeated.clear()  # KuCoin corrects its answer
    (later,) = run(test_client, fake, tmp_path, dot, whole, END + timedelta(hours=22, minutes=10))
    assert not later.closed and later.conflicts == () and later.rows == 1440
    held = {c.open_time: c for c in read_candles(tmp_path, "SPOT", "DOT-USDT", DAY)}
    assert (held[NINE].low, held[NINE].close) == (Decimal("0.7309"), Decimal("0.7314"))
    (settled,) = run(test_client, fake, tmp_path, dot, whole, END + SETTLING)
    assert settled.closed and settled.rows == 1440
    asked = len(fake.asked)
    run(test_client, fake, tmp_path, dot, whole, END + timedelta(days=30))
    assert len(fake.asked) == asked, "closed: never asked again"


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
    assert day.closed
    assert len([a for a in fake.asked if a[0] == "BTC-USDT"]) == 2
    (eth,) = run(test_client, fake, tmp_path, Instrument("SPOT", "ETH-USDT"), whole)
    assert not eth.closed, "an unreadable row spoils its answer, three times"
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
