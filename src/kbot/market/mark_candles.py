"""1-minute mark and index candles built from the ``mark-price`` channel (02 §3.7 and §3.8).

The owner decided on 7 November 2026 that KuCoin gives no mark or index candles, so the bot
builds them from the ``mark-price`` channel, a periodic channel with one value per second
carrying both the mark (``mp``) and the index (``ip``) price.

A minute becomes a pair of candles (evidence ``construida_flujo``) **only if the channel
was continuous during the whole minute**: the connection was healthy, and every one of the
60 whole seconds of the minute brought exactly one valid value. A socket that stays open
proves nothing about the values that did not arrive, so anything less is not a candle:

* the mark minute is a gap: KuCoin keeps no mark history, and nothing is invented;
* the index minute waits for its rebuild from Classic's per-second history
  (``reconstruida_classic``, ``index_candle_from_points``), which also needs all 60
  seconds; otherwise it stays a gap.

The flow state of the minute (02 §3.4) follows the same evidence: S1 continuous, S2 some
data but not continuous, S3 no valid data. Invalid values (not numeric, not finite, not
positive, or not on a whole second) are counted as anomalies and never enter a candle.
The minute of a value comes from its own timestamp (``ts``, ms), not from the order of
arrival; values of a minute already closed are ignored.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from kbot.market.timeutil import (
    MINUTE,
    as_utc,
    floor_to,
    from_milliseconds,
    is_int,
    to_milliseconds,
)

SECONDS = 60


class Evidence(StrEnum):
    FLOW = "construida_flujo"
    CLASSIC = "reconstruida_classic"


class FlowState(StrEnum):
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"


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
class MinuteResult:
    """Everything decided about one minute of one contract, in one place."""

    symbol: str
    minute: datetime
    flow_state: FlowState
    samples: int  # valid values on whole seconds of the minute
    anomalies: int
    disconnected: bool
    mark: OwnCandle | None  # None: the mark minute is a gap
    index: OwnCandle | None  # None: rebuild it from Classic, or it stays a gap

    @property
    def index_needs_rebuild(self) -> bool:
        return self.index is None


@dataclass
class _Minute:
    values: dict[int, tuple[Decimal, Decimal]] = field(default_factory=dict)
    anomalies: int = 0


def positive_price(raw: object) -> Decimal | None:
    """A finite, positive decimal from KuCoin text, or None."""
    if not isinstance(raw, str):
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def minute_of(timestamp_ms: int) -> datetime:
    return floor_to(from_milliseconds(timestamp_ms), MINUTE)


def _candle(
    series: str,
    symbol: str,
    minute: datetime,
    points: dict[int, Decimal],
    evidence: Evidence,
) -> OwnCandle:
    ordered = [points[ts] for ts in sorted(points)]
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


def _seconds_of(minute: datetime) -> set[int]:
    start = to_milliseconds(minute)
    return {start + 1000 * second for second in range(SECONDS)}


class MarkCandleBuilder:
    """Mark and index candles of one contract. One asyncio task feeds it."""

    def __init__(self, symbol: str, *, started_at: datetime) -> None:
        started_at = as_utc(started_at, "started_at")
        self.symbol = symbol
        self._down: list[tuple[datetime, datetime | None]] = [(started_at, None)]
        self._minutes: dict[datetime, _Minute] = {}
        self._next_minute = floor_to(started_at, MINUTE)

    def connection_up(self, at: datetime) -> None:
        at = as_utc(at, "at")
        if self._down and self._down[-1][1] is None:
            self._down[-1] = (self._down[-1][0], at)

    def connection_down(self, at: datetime) -> None:
        at = as_utc(at, "at")
        if not self._down or self._down[-1][1] is not None:
            self._down.append((at, None))

    def on_message(self, message: dict[str, Any]) -> None:
        data = message.get("d") or {}
        if data.get("s") != self.symbol:
            return
        timestamp = data.get("ts")
        if not is_int(timestamp) or timestamp % 1000:
            # Without a whole-second timestamp the value has no place in any minute.
            self._minute(self._next_minute).anomalies += 1
            return
        minute = minute_of(timestamp)
        if minute < self._next_minute:
            return  # its minute is closed: a late value never changes a result
        mark, index = positive_price(data.get("mp")), positive_price(data.get("ip"))
        slot = self._minute(minute)
        if mark is None or index is None:
            slot.anomalies += 1
            return
        slot.values.setdefault(timestamp, (mark, index))  # a repeated ts keeps the first value

    def close_until(self, now: datetime, lateness: timedelta) -> list[MinuteResult]:
        """Close every minute whose end plus ``lateness`` is not after ``now``."""
        now = as_utc(now, "now")
        results: list[MinuteResult] = []
        while self._next_minute + MINUTE + lateness <= now:
            minute = self._next_minute
            results.append(self._close(minute, self._minutes.pop(minute, _Minute())))
            self._next_minute = minute + MINUTE
            self._down = [(s, e) for s, e in self._down if e is None or e > self._next_minute]
        return results

    def _close(self, minute: datetime, data: _Minute) -> MinuteResult:
        disconnected = any(s < minute + MINUTE and (e is None or e > minute) for s, e in self._down)
        complete = set(data.values) == _seconds_of(minute)
        if not disconnected and complete and data.anomalies == 0:
            flow = FlowState.S1
            mark = _candle(
                "mark",
                self.symbol,
                minute,
                {t: v[0] for t, v in data.values.items()},
                Evidence.FLOW,
            )
            index = _candle(
                "index",
                self.symbol,
                minute,
                {t: v[1] for t, v in data.values.items()},
                Evidence.FLOW,
            )
        else:
            flow = FlowState.S2 if data.values else FlowState.S3
            mark = index = None
        return MinuteResult(
            symbol=self.symbol,
            minute=minute,
            flow_state=flow,
            samples=len(data.values),
            anomalies=data.anomalies,
            disconnected=disconnected,
            mark=mark,
            index=index,
        )

    def _minute(self, minute: datetime) -> _Minute:
        return self._minutes.setdefault(minute, _Minute())


def index_candle_from_points(
    symbol: str, minute: datetime, points: Iterable[tuple[int, object]]
) -> OwnCandle | None:
    """Rebuild an index minute from Classic's per-second history (``timePoint`` in ms).

    Only with exactly one finite, positive value on each of the 60 seconds of the minute;
    otherwise the minute stays a gap. Values outside the minute are ignored.
    """
    minute = as_utc(minute, "minute")
    expected = _seconds_of(minute)
    inside: dict[int, Decimal] = {}
    for timestamp, raw in points:
        if timestamp not in expected:
            if is_int(timestamp) and minute_of(timestamp) == minute:
                return None  # a value of this minute off the whole second
            continue
        value = raw if isinstance(raw, Decimal) else None
        if value is None or not value.is_finite() or value <= 0 or timestamp in inside:
            return None
        inside[timestamp] = value
    if set(inside) != expected:
        return None
    return _candle("index", symbol, minute, inside, Evidence.CLASSIC)
