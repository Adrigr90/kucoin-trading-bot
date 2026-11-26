"""One timeframe of one instrument as the fichas read it (``02`` §12.3.1).

* **A frame** holds the points of a timeframe, the values of the indicators a ficha uses in
  it, and its regime. A value or a point is read at an instant only if it is available then.
* **Execution candles:** one that is not usable resolves as soon as that is known: at the
  earliest instant at which one of its minutes is known to make it so (F3E3-04). An absent
  minute is known at its end plus the margin (the rule of the history, §12.1, point 4); a
  present one without a valid P or T, when it is available.
* **Provenance** of a result: the original minutes of its inputs, as merged intervals.
  Every input candle is complete, so every minute of those intervals is present, and the
  share only REST counts them on the minute frame, each once (§12.1, point 5).
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cached_property
from itertools import accumulate

from kbot.history.timeframes import TIMEFRAMES, MinuteCandle, aggregate, collect
from kbot.indicators.series import (
    PRICE,
    Dimension,
    Indicator,
    Point,
    Value,
    compute,
    from_bar,
    from_minute,
)
from kbot.market.last_candles import STABILIZATION
from kbot.market.timeutil import MINUTE
from kbot.strategies.regime import Regime, RegimeParams, regimes
from kbot.strategies.rules import Interval, Labels, TriggerCandle

ONE_MINUTE = "1m"


def step_of(timeframe: str) -> timedelta:
    return MINUTE if timeframe == ONE_MINUTE else timedelta(minutes=TIMEFRAMES[timeframe])


@dataclass
class Frame:
    market: str
    symbol: str
    timeframe: str
    points: dict[datetime, Point]
    values: dict[str, dict[datetime, Value]] = field(default_factory=dict)
    regimes: dict[datetime, Regime] = field(default_factory=dict)

    @property
    def step(self) -> timedelta:
        return step_of(self.timeframe)

    def value(self, name: str, open_time: datetime, at: datetime | None = None) -> Value | None:
        """The value of ``name`` at the candle ``open_time``; with ``at``, only if it is
        available then."""
        value = self.values[name].get(open_time)
        if value is None or (at is not None and value.available_at > at):
            return None
        return value

    def usable(
        self,
        open_time: datetime,
        at: datetime | None = None,
        dims: frozenset[Dimension] = PRICE,
    ) -> Point | None:
        """The point ``open_time`` if it is complete, available (at ``at``, if given) and
        valid in ``dims``."""
        point = self.points.get(open_time)
        if point is None or not point.complete or point.available_at is None:
            return None
        if (at is not None and point.available_at > at) or not point.valid(dims):
            return None
        return point

    def regime(self, open_time: datetime, at: datetime) -> Labels:
        """The labels of the candle ``open_time`` available at ``at``."""
        found = self.regimes.get(open_time)
        if found is None:
            return (None, None)
        trend = found.direction if found.direction_at and found.direction_at <= at else None
        vol = found.volatility if found.volatility_at and found.volatility_at <= at else None
        return (trend, vol)

    def span(self, start: datetime, end: datetime) -> list[datetime]:
        """The openings of the candles there are in [start, end), in order."""
        return self._opens[bisect_left(self._opens, start) : bisect_left(self._opens, end)]

    @cached_property
    def _opens(self) -> list[datetime]:
        return sorted(self.points)

    @cached_property
    def _rest_only(self) -> list[int]:
        return [0, *accumulate(self.points[o].rest_only_minutes for o in self._opens)]


def build_frames(
    minutes: Iterable[MinuteCandle],
    *,
    downloaded_until: datetime,
    timeframes: Iterable[str],
    indicators: Mapping[str, Mapping[str, Indicator]] | None = None,
    regime: Mapping[str, RegimeParams] | None = None,
) -> dict[str, Frame]:
    """The frames of one instrument: its minutes, always, and each of ``timeframes``,
    aggregated from them (F3-E1), with its ``indicators`` and its ``regime``."""
    by_key = collect(minutes)
    instruments = {(market, symbol) for market, symbol, _ in by_key}
    if len(instruments) != 1:
        raise ValueError("the frames are of exactly one instrument")
    ((market, symbol),) = instruments
    indicators, regime = indicators or {}, regime or {}
    frames = {}
    for timeframe in sorted({ONE_MINUTE, *timeframes, *indicators, *regime}):
        if timeframe == ONE_MINUTE:
            ordered = sorted(by_key.values(), key=lambda m: m.candle.open_time)
            points = [from_minute(m, m.multiplier) for m in ordered]
        else:
            bars = aggregate(by_key.values(), timeframe, downloaded_until=downloaded_until)
            points = [from_bar(bar, bar.multiplier) for bar in bars]
        frame = Frame(market, symbol, timeframe, {p.open_time: p for p in points})
        for name, indicator in indicators.get(timeframe, {}).items():
            frame.values[name] = {v.open_time: v for v in compute(indicator, points, frame.step)}
        if timeframe in regime:
            frame.regimes = regimes(points, frame.step, regime[timeframe])
        frames[timeframe] = frame
    return frames


def trigger_candle(frame: Frame, minutes: Frame, open_time: datetime) -> TriggerCandle:
    """The execution candle ``open_time`` of ``frame`` for the walk of §12.3.1."""
    point = frame.usable(open_time)
    if point is not None and point.available_at is not None:
        return TriggerCandle(open_time, point.available_at, point.close)
    return TriggerCandle(open_time, known_unusable(frame, minutes, open_time), None)


def known_unusable(frame: Frame, minutes: Frame, open_time: datetime) -> datetime:
    """When it is first known that the candle ``open_time`` of ``frame`` will not be
    usable: the earliest instant among its minutes that make it so, never the first of them
    by opening (F3E3-04)."""
    end = open_time + frame.step
    known = []
    minute = open_time
    while minute < end:
        point = minutes.points.get(minute)
        if point is None:
            known.append(minute + MINUTE + STABILIZATION)
        elif point.available_at is not None and not point.valid(PRICE):
            known.append(point.available_at)
        minute += MINUTE
    # With every minute there and valid, only the end of the data can hold the candle back.
    return min(known, default=end + STABILIZATION)


def value_span(value: Value, step: timedelta) -> Interval:
    return (value.inputs_from, value.open_time + step)


def candle_span(open_time: datetime, step: timedelta) -> Interval:
    return (open_time, open_time + step)


def merged(intervals: Iterable[Interval]) -> tuple[Interval, ...]:
    result: list[Interval] = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return tuple(result)


def rest_only(provenance: Sequence[Interval], minutes: Frame) -> tuple[int, int]:
    """The original minutes of a provenance and how many of them are only REST."""
    if minutes.timeframe != ONE_MINUTE:
        raise ValueError("the provenance is counted on the frame of minutes")
    total = rest = 0
    for start, end in provenance:
        first = bisect_left(minutes._opens, start)
        last = bisect_left(minutes._opens, end)
        total += last - first
        rest += minutes._rest_only[last] - minutes._rest_only[first]
    return total, rest
