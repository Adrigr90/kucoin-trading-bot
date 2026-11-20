"""Minutes for the tests of the fichas: a random walk of one instrument, as the history
holds it (every minute only REST, available at its end plus the margin), and the same walk
poisoned from an instant on."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from kbot.history.timeframes import MinuteCandle, Quality, historical_minute
from kbot.market.last_candles import CandleEvidence, Ohlcv

MINUTE = timedelta(minutes=1)
START = datetime(2026, 1, 5, tzinfo=UTC)  # a Monday, 00:00 UTC
CENT = Decimal("0.01")


def walk_minutes(
    count: int,
    seed: int,
    *,
    market: str = "FUTURES",
    symbol: str = "ETHUSDTM",
    start: datetime = START,
    price: float = 100.0,
    skip: Sequence[int] = (),
) -> list[MinuteCandle]:
    """``count`` minutes of a random walk with trends and volume levels that come and go,
    without the minutes whose index is in ``skip``."""
    rng = random.Random(seed)
    minutes = []
    close, drift, level = price, 0.0, 1.0
    missing = set(skip)
    for index in range(count):
        if index % 240 == 0:
            drift = rng.choice((-0.04, -0.02, 0.0, 0.02, 0.04))
        if index % 30 == 0:
            level = rng.uniform(0.4, 2.5)
        opened = close
        close = max(1.0, close + drift + rng.gauss(0.0, 0.15))
        high = max(opened, close) + rng.uniform(0.0, 0.1)
        low = min(opened, close) - rng.uniform(0.0, 0.1)
        volume = rng.uniform(1.0, 10.0) * level
        if index in missing:
            continue
        minutes.append(
            minute(market, symbol, start + index * MINUTE, opened, high, low, close, volume)
        )
    return minutes


def minute(
    market: str,
    symbol: str,
    at: datetime,
    opened: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> MinuteCandle:
    o, h, lo, c = (Decimal(f"{x:.2f}") for x in (opened, high, low, close))
    h, lo = max(h, o, c), min(lo, o, c)
    v = Decimal(f"{volume:.3f}")
    return historical_minute(market, symbol, Ohlcv(at, o, h, lo, c, v, (v * c).quantize(CENT)))


def poisoned(minutes: Sequence[MinuteCandle], after: datetime, seed: int) -> list[MinuteCandle]:
    """The same minutes, with every one available after ``after`` changed at random: its
    prices and its volume."""
    rng = random.Random(seed)
    result = []
    for m in minutes:
        if m.available_at <= after:
            result.append(m)
            continue
        factor = rng.uniform(0.5, 1.5)
        c = m.candle
        result.append(
            minute(
                m.market,
                m.symbol,
                c.open_time,
                float(c.open) * factor,
                float(c.high) * factor * 1.01,
                float(c.low) * factor * 0.99,
                float(c.close) * factor,
                float(c.volume) * rng.uniform(0.1, 10.0),
            )
        )
    return result


def contrasted(m: MinuteCandle) -> MinuteCandle:
    """The minute as if the WebSocket had confirmed it: not only REST."""
    return MinuteCandle(
        m.market,
        m.symbol,
        m.candle,
        CandleEvidence.CRUZADA_CONSISTENTE,
        Quality.CONTRASTADA,
        Quality.CONTRASTADA,
        Quality.CONTRASTADA,
        m.available_at,
    )
