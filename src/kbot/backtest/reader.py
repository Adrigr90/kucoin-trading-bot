"""The minutes the evaluation reads from the history on the PC (``02`` §12.5.5).

* **Only from F3-E1** (``kbot.history.store``): the day files of one instrument, as the
  history holds them. Each minute is only REST and available at its end plus the margin.
* **Never a minute of the test:** a window that ends after the start of the test is refused
  with an error, before any file is opened. So no file of a day of the test is ever opened,
  and every minute returned opens before the end of the window.
* **A day whose files are not a valid pair** gives no minute: it is named, with why.
* **The presence of each minute** asked for the instrument (F3E5-05), as F3-E1 counts it
  (§12.1, point 2): ``presente``, ``ausente``, ``en_conflicto`` or ``no_descargado``, from
  what was asked (``solicitado.json``) and the requests of each day. A day without a valid
  pair of files leaves its minutes ``no_descargado``. Minutes never asked have no presence:
  they are outside the history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from kbot.backtest.partitions import TEST_START, SealedPartition
from kbot.history import store
from kbot.history.coverage import Presence, Window, presence
from kbot.history.timeframes import MinuteCandle, historical_minute

DAY = timedelta(days=1)


@dataclass(frozen=True)
class Read:
    minutes: list[MinuteCandle]
    invalid_days: tuple[tuple[date, str], ...]
    # The presence of every minute of [start, end) that was asked for the instrument.
    states: dict[datetime, Presence] = field(default_factory=dict)


def read_minutes(root: Path, market: str, symbol: str, start: datetime, end: datetime) -> Read:
    """The minutes of the instrument in [start, end), in order, and their presence."""
    if end > TEST_START:
        raise SealedPartition(f"[{start}, {end}) reaches the test partition, sealed until the F8")
    if not start < end:
        raise ValueError("a reading needs its start before its end")
    asked = store.requested(root, market, symbol)
    minutes: list[MinuteCandle] = []
    invalid: list[tuple[date, str]] = []
    states: dict[datetime, Presence] = {}
    day = start.astimezone(UTC).date()
    while (opening := datetime.combine(day, time(), UTC)) < end:
        part = Window(max(opening, start), min(opening + DAY, end))
        record, reason = store.read_day(root, market, symbol, day)
        if record is None:
            if reason:
                invalid.append((day, reason))
            candles = []
        else:
            candles = [
                c
                for c in store.read_candles(root, market, symbol, day)
                if part.start <= c.open_time < part.end
            ]
            minutes.extend(historical_minute(market, symbol, c) for c in candles)
        for window in asked:
            piece = Window(max(part.start, window.start), min(part.end, window.end))
            if piece.start >= piece.end:
                continue
            if record is None:
                states.update(presence(piece, [], []))
            else:
                present = [c.open_time for c in candles if piece.start <= c.open_time < piece.end]
                conflicts = [m for m in record.in_conflict if piece.start <= m < piece.end]
                states.update(presence(piece, record.requests, present, conflicts))
        day += DAY
    return Read(minutes, tuple(invalid), states)
