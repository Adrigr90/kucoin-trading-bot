"""What an indicator sees of a series, and the one way every indicator is run (``02``
§12.2, point 1; F3-E2).

* **A point** is one candle of one instrument and timeframe: its prices and volume as 64-bit
  floats converted from their exact decimals (the declared rule of §12.1, point 6), whether
  it is present or complete, its dimensions P, V and T, its availability, and how many of
  its original minutes it holds and how many of them are only REST.
* **Usable** for an indicator: complete, available, valid in every dimension the indicator
  needs (§3.4: P + T for prices, P + V + T for VWAP and relative volume) and consecutive
  with the usable one before it. A dimension the indicator does not need never affects it,
  and being next in time never replaces T.
* **A value** at candle *t* exists only if every candle it needs since the indicator's last
  start is usable and consecutive. Otherwise there is no value at *t* and the indicator goes
  back to its initial state (§12.1, point 6).
* **Its inputs** are the candles it depends on, by its kind: the window, everything since
  the last start (recursive) or the UTC day (session). Its provenance is their original
  minutes, each counted once, and it is available at the latest of their availabilities.
* **Volume**: an indicator declares its use. Comparing volumes across instruments or
  against an absolute threshold is refused while B8 is open (CD-05), and in Futures a
  change of the contract multiplier breaks a volume window like a gap (CD-12).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from kbot.history.timeframes import Bar, MinuteCandle, Quality
from kbot.market.last_candles import CandleEvidence
from kbot.market.timeutil import as_utc


class Dimension(StrEnum):
    P = "P"
    V = "V"
    T = "T"


PRICE = frozenset({Dimension.P, Dimension.T})  # §3.4: RSI, MACD, EMA/SMA, Bollinger, ...
PRICE_VOLUME = frozenset({Dimension.P, Dimension.V, Dimension.T})  # VWAP, relative volume


class Kind(StrEnum):
    WINDOW = "ventana"  # its last ``inputs`` candles
    RECURSIVE = "recursivo"  # everything since its last start, the seed included
    SESSION = "sesion"  # every candle of the UTC day up to t


class VolumeUse(StrEnum):
    NONE = "ninguno"
    SCALE_INVARIANT = "invariante_a_escala"  # within one instrument: allowed with B8 open
    CROSS_INSTRUMENT = "entre_instrumentos"  # forbidden until E5 closes B8
    ABSOLUTE_THRESHOLD = "umbral_absoluto"  # forbidden until E5 closes B8


B8_OPEN = True  # 02 §12.1, point 7: closed by E5


class VolumeUseForbidden(ValueError):
    """A use of volume that 02 §12.1, point 7 forbids while B8 is open (CD-05)."""


def require_volume_use(use: VolumeUse) -> None:
    if B8_OPEN and use in (VolumeUse.CROSS_INSTRUMENT, VolumeUse.ABSOLUTE_THRESHOLD):
        raise VolumeUseForbidden(f"{use.value}: forbidden while B8 is open (02 §12.1, point 7)")


@dataclass(frozen=True)
class Point:
    """One candle of a series as an indicator sees it."""

    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    complete: bool  # present (1 minute) or complete (higher timeframe)
    price: Quality
    volume_quality: Quality
    time: Quality
    available_at: datetime | None
    minutes: int  # its original minutes
    rest_only_minutes: int  # of them, only REST (vela_rest_suficiente)
    multiplier: float | None = None  # Futures: the contract multiplier, when known

    def valid(self, dims: frozenset[Dimension]) -> bool:
        """Every dimension in ``dims`` contrasted or only REST, never discrepant."""
        quality = {
            Dimension.P: self.price,
            Dimension.V: self.volume_quality,
            Dimension.T: self.time,
        }
        return all(quality[d] is not Quality.DISCREPANTE for d in dims)


def from_minute(minute: MinuteCandle, multiplier: float | None = None) -> Point:
    candle = minute.candle
    return Point(
        as_utc(candle.open_time, "open_time"),
        float(candle.open),
        float(candle.high),
        float(candle.low),
        float(candle.close),
        float(candle.volume),
        True,
        minute.price,
        minute.volume,
        minute.time,
        minute.available_at,
        1,
        1 if minute.evidence is CandleEvidence.REST_SUFICIENTE else 0,
        multiplier,
    )


def from_bar(bar: Bar, multiplier: float | None = None) -> Point:
    return Point(
        as_utc(bar.open_time, "open_time"),
        float(bar.open),
        float(bar.high),
        float(bar.low),
        float(bar.close),
        float(bar.volume),
        bar.complete,
        bar.price,
        bar.volume_quality,
        bar.time,
        bar.available_at,
        bar.minutes,
        bar.rest_only_minutes,
        multiplier,
    )


@dataclass(frozen=True)
class Value:
    """The value of an indicator at the candle that opens at ``open_time``."""

    open_time: datetime
    outputs: tuple[float, ...]
    available_at: datetime  # the latest availability of its inputs
    inputs_from: datetime  # the opening of its earliest input
    minutes: int  # original minutes of its inputs, each once
    rest_only_minutes: int

    @property
    def rest_only_share(self) -> float:
        """The share of the provenance only REST, between 0 and 1 (§12.1, point 5)."""
        return self.rest_only_minutes / self.minutes


class Indicator:
    """What every indicator declares, and the state it keeps between candles."""

    name: str
    dims: frozenset[Dimension] = PRICE
    kind: Kind = Kind.RECURSIVE
    volume_use: VolumeUse = VolumeUse.NONE
    inputs: int | None = None  # a window's candles; None for recursive and session
    warmup: int = 1  # usable consecutive candles up to the first value

    def reset(self) -> None:
        raise NotImplementedError

    def update(self, point: Point) -> tuple[float, ...] | None:
        """Take the next usable point of a run; return the outputs at it, or None."""
        raise NotImplementedError

    def check_step(self, step: timedelta) -> None:
        """Refuse a timeframe in which the indicator has no meaning (none by default)."""


class _Inputs:
    """The inputs of the current value of an indicator: provenance and availability."""

    def __init__(self, indicator: Indicator) -> None:
        self.window = indicator.inputs if indicator.kind is Kind.WINDOW else None
        self.points: deque[Point] = deque()
        self.reset()

    def reset(self) -> None:
        self.points.clear()
        self.first: datetime | None = None
        self.minutes = 0
        self.rest_only = 0
        self.available: datetime | None = None

    def add(self, point: Point) -> None:
        if self.window is not None:
            self.points.append(point)
            if len(self.points) > self.window:
                self.points.popleft()
            return
        self.first = self.first or point.open_time
        self.minutes += point.minutes
        self.rest_only += point.rest_only_minutes
        available = point.available_at
        if available is not None and (self.available is None or available > self.available):
            self.available = available

    def value(self, open_time: datetime, outputs: tuple[float, ...]) -> Value:
        if self.window is not None:
            points = list(self.points)
            return Value(
                open_time,
                outputs,
                max(p.available_at for p in points if p.available_at is not None),
                points[0].open_time,
                sum(p.minutes for p in points),
                sum(p.rest_only_minutes for p in points),
            )
        if self.first is None or self.available is None:
            raise RuntimeError("a value without inputs")
        return Value(open_time, outputs, self.available, self.first, self.minutes, self.rest_only)


def compute(indicator: Indicator, points: Sequence[Point], step: timedelta) -> list[Value]:
    """The values of ``indicator`` over ``points``, the candles of one instrument and
    timeframe of length ``step``, in order of opening. Absent candles are simply missing:
    a jump in the openings is a gap."""
    require_volume_use(indicator.volume_use)
    indicator.check_step(step)
    indicator.reset()
    inputs = _Inputs(indicator)
    uses_volume = indicator.volume_use is not VolumeUse.NONE
    values: list[Value] = []
    seen: datetime | None = None
    last: Point | None = None  # the last usable point of the current run
    for point in points:
        if seen is not None and point.open_time <= seen:
            raise ValueError(f"points out of order or repeated at {point.open_time}")
        seen = point.open_time
        usable = point.complete and point.available_at is not None and point.valid(indicator.dims)
        if not usable:
            if last is not None:
                indicator.reset()
                inputs.reset()
            last = None
            continue
        if last is not None:
            joined = point.open_time - last.open_time == step
            if joined and uses_volume and point.multiplier != last.multiplier:
                joined = False  # CD-12: a new multiplier breaks a volume window
            if not joined:
                indicator.reset()
                inputs.reset()
            elif indicator.kind is Kind.SESSION and _day(point) != _day(last):
                inputs.reset()  # a new session: the inputs start at its first candle
        inputs.add(point)
        last = point
        outputs = indicator.update(point)
        if outputs is not None:
            values.append(inputs.value(point.open_time, outputs))
    return values


def _day(point: Point) -> datetime:
    return datetime.combine(point.open_time.date(), datetime.min.time(), UTC)
