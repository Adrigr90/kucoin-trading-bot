"""1-minute mark and index candles built from the ``mark-price`` channel (02 §3.7 and §3.8).

The owner decided on 7 November 2026 that KuCoin gives no mark or index candles, so the bot
builds them: one value per second arrives on the ``mark-price`` channel with both the mark
(``mp``) and the index (``ip``) price.

* A minute becomes a candle (evidence ``construida_flujo``) only if the connection was
  healthy during the **whole** minute. Otherwise high and low could miss an extreme, so:
* the mark minute is a gap: KuCoin keeps no mark history, and nothing is invented;
* the index minute is rebuilt from the per-second history of Classic
  (``reconstruida_classic``) by ``index_candle_from_points``, or stays a gap.

The minute a value belongs to comes from its own timestamp (``ts``, ms), never from the
order of arrival.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


class Evidence(StrEnum):
    FLOW = "construida_flujo"
    CLASSIC = "reconstruida_classic"


@dataclass(frozen=True)
class OwnCandle:
    series: str  # "mark" or "index"
    symbol: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    samples: int
    evidence: Evidence


@dataclass(frozen=True)
class MinuteGap:
    series: str
    symbol: str
    minute: datetime


def minute_of(timestamp_ms: int) -> datetime:
    total = timedelta(milliseconds=timestamp_ms)
    return EPOCH + (total // MINUTE) * MINUTE


def _candle(
    series: str,
    symbol: str,
    minute: datetime,
    points: list[tuple[int, Decimal]],
    evidence: Evidence,
) -> OwnCandle:
    ordered = [value for _, value in sorted(points)]
    return OwnCandle(
        series=series,
        symbol=symbol,
        open_time=minute,
        open=ordered[0],
        high=max(ordered),
        low=min(ordered),
        close=ordered[-1],
        samples=len(ordered),
        evidence=evidence,
    )


class MarkCandleBuilder:
    """Mark and index candles of one contract. One asyncio task feeds it."""

    def __init__(self, symbol: str, *, started_at: datetime) -> None:
        self.symbol = symbol
        self._down: list[tuple[datetime, datetime | None]] = [(started_at, None)]
        self._points: dict[datetime, dict[int, tuple[Decimal, Decimal]]] = {}
        self._next_minute = minute_of((started_at - EPOCH) // timedelta(milliseconds=1))

    def connection_up(self, at: datetime) -> None:
        if self._down and self._down[-1][1] is None:
            self._down[-1] = (self._down[-1][0], at)

    def connection_down(self, at: datetime) -> None:
        if not self._down or self._down[-1][1] is not None:
            self._down.append((at, None))

    def on_message(self, message: dict[str, Any]) -> None:
        data = message.get("d") or {}
        if data.get("s") != self.symbol:
            return
        timestamp = data.get("ts")
        mark, index = data.get("mp"), data.get("ip")
        if not (isinstance(timestamp, int) and isinstance(mark, str) and isinstance(index, str)):
            raise ValueError("mark-price message without integer ts and text mp and ip")
        minute = minute_of(timestamp)
        if minute < self._next_minute:
            return  # its minute is already closed; a late value never changes a candle
        # The same ts twice is a duplicate; the first value is kept.
        self._points.setdefault(minute, {}).setdefault(timestamp, (Decimal(mark), Decimal(index)))

    def close_until(
        self, now: datetime, lateness: timedelta
    ) -> tuple[list[OwnCandle], list[MinuteGap]]:
        """Close every minute whose end plus ``lateness`` is not after ``now``."""
        candles: list[OwnCandle] = []
        gaps: list[MinuteGap] = []
        while self._next_minute + MINUTE + lateness <= now:
            minute = self._next_minute
            points = self._points.pop(minute, {})
            healthy = not self._disconnected_during(minute, minute + MINUTE)
            if healthy and points:
                marks = [(ts, mark) for ts, (mark, _) in points.items()]
                indexes = [(ts, index) for ts, (_, index) in points.items()]
                candles.append(_candle("mark", self.symbol, minute, marks, Evidence.FLOW))
                candles.append(_candle("index", self.symbol, minute, indexes, Evidence.FLOW))
            else:
                gaps.append(MinuteGap("mark", self.symbol, minute))
                gaps.append(MinuteGap("index", self.symbol, minute))
            self._next_minute = minute + MINUTE
            self._down = [(s, e) for s, e in self._down if e is None or e > self._next_minute]
        return candles, gaps

    def _disconnected_during(self, start: datetime, end: datetime) -> bool:
        return any(s < end and (e is None or e > start) for s, e in self._down)


def index_candle_from_points(
    symbol: str, minute: datetime, points: Iterable[tuple[int, Decimal]]
) -> OwnCandle | None:
    """Rebuild an index minute from Classic's per-second history (``timePoint`` in ms).

    The minute is rebuilt only if Classic has every second of it; otherwise it stays a gap.
    """
    start_ms = (minute - EPOCH) // timedelta(milliseconds=1)
    inside = {ts: value for ts, value in points if start_ms <= ts < start_ms + 60_000}
    if set(inside) != {start_ms + 1000 * second for second in range(60)}:
        return None
    return _candle("index", symbol, minute, list(inside.items()), Evidence.CLASSIC)
