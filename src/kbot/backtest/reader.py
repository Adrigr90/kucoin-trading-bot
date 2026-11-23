"""The minutes the evaluation reads from the history on the PC (``02`` §12.5.5).

* **Only from F3-E1** (``kbot.history.store``): the day files of one instrument, as the
  history holds them. Each minute is only REST and available at its end plus the margin.
* **Never a minute of the test:** a window that ends after the start of the test is refused
  with an error, before any file is opened. So no file of a day of the test is ever opened,
  and every minute returned opens before the end of the window.
* **A day whose files are not a valid pair** gives no minute: it is named, with why.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from kbot.backtest.partitions import TEST_START, SealedPartition
from kbot.history import store
from kbot.history.timeframes import MinuteCandle, historical_minute

DAY = timedelta(days=1)


@dataclass(frozen=True)
class Read:
    minutes: list[MinuteCandle]
    invalid_days: tuple[tuple[date, str], ...]


def read_minutes(root: Path, market: str, symbol: str, start: datetime, end: datetime) -> Read:
    """The minutes of the instrument in [start, end), in order."""
    if end > TEST_START:
        raise SealedPartition(f"[{start}, {end}) reaches the test partition, sealed until the F8")
    if not start < end:
        raise ValueError("a reading needs its start before its end")
    minutes: list[MinuteCandle] = []
    invalid: list[tuple[date, str]] = []
    day = start.astimezone(UTC).date()
    while datetime.combine(day, time(), UTC) < end:
        record, reason = store.read_day(root, market, symbol, day)
        if record is None:
            if reason:
                invalid.append((day, reason))
        else:
            for candle in store.read_candles(root, market, symbol, day):
                if start <= candle.open_time < end:
                    minutes.append(historical_minute(market, symbol, candle))
        day += DAY
    return Read(minutes, tuple(invalid))
