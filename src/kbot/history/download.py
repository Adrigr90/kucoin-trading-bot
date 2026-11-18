"""The download of the 1-minute candles of the history (``02`` §12.1, point 1; F3-E1).

* **Only the read-only client of E1:** ``GET`` of its whitelist, the single REST budget of
  the machine, no keys. The route is that of the recorder's REST candles (``uta.v2.kline``),
  asked the same way: one minute more on each side, and only the minutes asked are kept.
* **Requests within KuCoin's limits** [H, 02 §3.2]: at most 1500 candles per request in
  Spot and 200 in Futures, whose candles under 8 h exist only from 1 January 2025.
* **Each request is an answer or a failure.** An answer, also an empty one, settles its
  minutes: present or absent. A failure (network, a KuCoin error, an unreadable row, which
  spoils the whole answer) is asked again a few times; if it still fails, its minutes stay
  ``no_descargado``, and the next run asks for them again.
* **Day by day:** a closed day is skipped, any other is downloaded whole and written
  (``store``). Repeating the download changes no closed file.
* **Only closed minutes:** the window ends at the last minute whose stabilization margin has
  passed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import structlog

from kbot.history.coverage import Request, Window
from kbot.history.store import DayRecord, read_record, write_day
from kbot.kucoin.rest import KucoinApiError, KucoinRestClient
from kbot.market.last_candles import STABILIZATION, Ohlcv, rest_kline
from kbot.market.timeutil import MINUTE, as_utc, floor_to, to_milliseconds

log = structlog.get_logger("kbot.history")

PER_REQUEST = {"SPOT": 1500, "FUTURES": 200}  # candles per request [H, 02 §3.2]
# Futures candles under 8 h exist only from this day on [H, 02 §3.2].
FUTURES_MINUTES_SINCE = datetime(2025, 1, 1, tzinfo=UTC)
ATTEMPTS = 3
RETRY_SECONDS = 5.0
DAY = timedelta(days=1)


@dataclass(frozen=True)
class Instrument:
    market: str
    symbol: str


def closed_until(now: datetime) -> datetime:
    """End (exclusive) of the last minute closed at ``now``: ended and past its margin."""
    return floor_to(as_utc(now, "now") - STABILIZATION, MINUTE)


def window_for(market: str, days: int, now: datetime) -> Window:
    """The window of an instrument: the last ``days`` whole UTC days up to the last closed
    minute; in Futures, never before the first day with minute candles."""
    end = closed_until(now)
    start = floor_to(end, DAY) - days * DAY
    if market == "FUTURES":
        start = max(start, FUTURES_MINUTES_SINCE)
    return Window(start, max(start, end))


def chunks(window: Window, size: int) -> Iterator[tuple[datetime, datetime]]:
    """``[first, last]`` stretches of at most ``size`` minutes that cover the window."""
    first = window.start
    while first < window.end:
        last = min(first + (size - 1) * MINUTE, window.end - MINUTE)
        yield first, last
        first = last + MINUTE


def day_windows(window: Window) -> Iterator[tuple[date, Window]]:
    """The window split by UTC day."""
    start = window.start
    while start < window.end:
        end = min(floor_to(start, DAY) + DAY, window.end)
        yield start.date(), Window(start, end)
        start = end


Sleep = Callable[[float], Awaitable[None]]


class Downloader:
    def __init__(
        self,
        client: KucoinRestClient,
        root: Path,
        *,
        sleep: Sleep = asyncio.sleep,
        retry_seconds: float = RETRY_SECONDS,
    ) -> None:
        self.client = client
        self.root = root
        self._sleep = sleep
        self._retry = retry_seconds

    async def instrument(
        self, instrument: Instrument, window: Window, now: datetime
    ) -> list[DayRecord]:
        """Download every day of the window that is not closed yet."""
        records = []
        for day, part in day_windows(window):
            held = read_record(self.root, instrument.market, instrument.symbol, day)
            if held is not None and held.closed:
                records.append(held)
                continue
            records.append(await self.day(instrument, day, part, now))
        return records

    async def day(
        self, instrument: Instrument, day: date, window: Window, now: datetime
    ) -> DayRecord:
        size = PER_REQUEST[instrument.market] - 2  # one minute more on each side
        requests: list[Request] = []
        candles: dict[datetime, Ohlcv] = {}
        for first, last in chunks(window, size):
            got = await self._ask(instrument, first, last)
            requests.append(Request(first, last, got is not None))
            for candle in got or []:
                held = candles.setdefault(candle.open_time, candle)
                if held != candle:
                    raise ValueError(f"{instrument}: two different candles at {candle.open_time}")
        record = write_day(
            self.root,
            instrument.market,
            instrument.symbol,
            day,
            window,
            list(candles.values()),
            requests,
            now,
        )
        failed = sum(1 for r in requests if not r.succeeded)
        log.info(
            "history_day",
            market=instrument.market,
            symbol=instrument.symbol,
            day=day.isoformat(),
            candles=record.rows,
            failed_requests=failed,
            closed=record.closed,
        )
        return record

    async def _ask(
        self, instrument: Instrument, first: datetime, last: datetime
    ) -> list[Ohlcv] | None:
        for attempt in range(ATTEMPTS):
            got = await self._fetch(instrument, first, last)
            if got is not None:
                return got
            if attempt + 1 < ATTEMPTS:
                await self._sleep(self._retry * (attempt + 1))
        return None

    async def _fetch(
        self, instrument: Instrument, first: datetime, last: datetime
    ) -> list[Ohlcv] | None:
        """The candles of ``[first, last]``, or None if the request failed."""
        first_s = to_milliseconds(first) // 1000
        last_s = to_milliseconds(last) // 1000
        try:
            response = await self.client.get(
                "uta.v2.kline",
                params={
                    "tradeType": instrument.market,
                    "symbol": instrument.symbol,
                    "interval": "1min",
                    "startAt": str(first_s - 60),
                    "endAt": str(last_s + 60),
                },
            )
        except (KucoinApiError, httpx.HTTPError) as exc:
            log.warning(
                "history_request_failed",
                market=instrument.market,
                symbol=instrument.symbol,
                first=first.isoformat(),
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            return None
        return _candles(response.data, instrument.symbol, first, last)


def _candles(data: Any, symbol: str, first: datetime, last: datetime) -> list[Ohlcv] | None:
    """The candles of ``[first, last]`` in an answer, or None if it is not usable."""
    if not isinstance(data, dict) or data.get("symbol") != symbol:
        return None
    rows = data.get("list")
    if not isinstance(rows, list):
        return None
    parsed = [rest_kline(row) for row in rows]
    if any(candle is None for candle in parsed):
        return None  # one unreadable row spoils the whole answer: it is asked again
    return [c for c in parsed if c is not None and first <= c.open_time <= last]
