"""Decisions and minutes made by hand for the tests of the combiner and of a position."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kbot.history.timeframes import MinuteCandle
from kbot.strategies.rules import LONG, Decision, Outcome, Signal
from tests.unit.strategies.support import minute

DAY = datetime(2025, 6, 2, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def at(hours: int, minutes: int = 0, seconds: int = 0) -> datetime:
    return DAY + timedelta(hours=hours, minutes=minutes, seconds=seconds)


def decision(
    family: str,
    d: int,
    instant: datetime,
    *,
    opened: datetime | None = None,
    stop: float | None = None,
    symbol: str = "XUSDTM",
    provenance: tuple[tuple[datetime, datetime], ...] = (),
) -> Decision:
    """A decision of ``family`` in the instant ``instant``, of a signal on the 15 m candle
    ``opened`` (by default the one that ends 5 minutes before)."""
    opened = opened or instant.replace(second=0) - timedelta(minutes=20)
    signal = Signal(
        family,
        "FUTURES",
        symbol,
        "15m",
        opened,
        d,
        opened + timedelta(minutes=15, seconds=15),
        opened - timedelta(hours=1),
        (("atr", 1.0),),
        (None, None),
        (None, None),
        provenance,
    )
    level = stop if stop is not None else (99.0 if d == LONG else 101.0)
    return Decision(signal, Outcome.DECISION, instant, opened, 100.0, level, provenance)


def flat(
    start: datetime,
    count: int,
    price: float = 101.0,
    *,
    changes: dict[datetime, tuple[float, float, float, float]] | None = None,
    skip: set[datetime] | None = None,
) -> list[MinuteCandle]:
    """``count`` minutes from ``start`` at ``price``, with the ``(open, high, low, close)``
    of ``changes`` and without the minutes of ``skip``."""
    changes, skip = changes or {}, skip or set()
    result = []
    for i in range(count):
        opened = start + i * MINUTE
        if opened in skip:
            continue
        o, h, low, c = changes.get(opened, (price, price, price, price))
        result.append(minute("FUTURES", "XUSDTM", opened, o, h, low, c, 5.0))
    return result
