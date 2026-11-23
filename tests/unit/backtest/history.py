"""A small history on disk, written with the store of F3-E1, for the tests of the
evaluation: every day whole, asked in one request, and closed."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from kbot.backtest.fees import Fee
from kbot.history import store
from kbot.history.coverage import Request
from kbot.history.coverage import Window as Span
from kbot.history.timeframes import MinuteCandle
from kbot.market.last_candles import Ohlcv

DAY = timedelta(days=1)
MINUTE = timedelta(minutes=1)
READ_AT = datetime(2026, 11, 23, 9, 30, tzinfo=UTC)


def write_history(root: Path, minutes: Iterable[MinuteCandle]) -> None:
    days: dict[tuple[str, str, date], list[Ohlcv]] = defaultdict(list)
    for m in minutes:
        days[(m.market, m.symbol, m.candle.open_time.date())].append(m.candle)
    for (market, symbol, day), candles in sorted(days.items()):
        start = datetime.combine(day, time(), UTC)
        store.write_day(
            root,
            market,
            symbol,
            day,
            Span(start, start + DAY),
            candles,
            [Request(start, start + DAY - MINUTE, True)],
            start + 3 * DAY,
        )


def fee(market: str, symbol: str, taker: str) -> Fee:
    return Fee(market, symbol, Decimal(taker), f"a mano: {taker}", READ_AT)
