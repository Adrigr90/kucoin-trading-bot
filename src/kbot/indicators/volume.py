"""The two uses of volume allowed while B8 is open: VWAP of the UTC day and relative volume
(``02`` §12.1, point 7, and §12.2, points 2 to 4). Both weigh volumes of one instrument
against each other, so the unit of the volume cancels out.

They follow ``VWAP`` and ``RVOL`` of TA-Lib 0.8.3, with three declared differences: without
volume yet there is no value (TA-Lib gives 0), a candle that is not usable leaves the VWAP
without value for the rest of its day, and a baseline of 0 gives no relative volume
(TA-Lib gives an infinity, or NaN for 0/0).
"""

from __future__ import annotations

from collections import deque
from datetime import date, time, timedelta

from kbot.indicators.series import PRICE_VOLUME, Indicator, Kind, Point, VolumeUse


class VWAP(Indicator):
    """``sum(p V) / sum(V)`` over the candles of the UTC day up to t, with the typical
    price ``p = (H + L + C) / 3``. A day starts at its 00:00 UTC candle: after any reset
    (a gap, a candle not usable, a new multiplier) there is no value until the next day."""

    name = "VWAP"
    dims = PRICE_VOLUME
    kind = Kind.SESSION
    volume_use = VolumeUse.SCALE_INVARIANT
    warmup = 1

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._day: date | None = None  # the session being accumulated, if any
        self._weighted = 0.0
        self._volume = 0.0

    def check_step(self, step: timedelta) -> None:
        if step >= timedelta(days=1):
            raise ValueError("VWAP: only in timeframes of less than 1 d")

    def update(self, point: Point) -> tuple[float, ...] | None:
        day = point.open_time.date()
        if day != self._day:
            if point.open_time.time() != time(0, 0):
                self._day = None  # mid-day: this day is lost, wait for the next one
                return None
            self._day = day
            self._weighted = 0.0
            self._volume = 0.0
        typical = (point.high + point.low + point.close) / 3.0
        self._weighted += typical * point.volume
        self._volume += point.volume
        return (self._weighted / self._volume,) if self._volume > 0.0 else None


class RelativeVolume(Indicator):
    """``V_t`` over the mean of the ``w`` volumes before it, as ``RVOL`` of TA-Lib: the
    running total drops the oldest volume after the division, and a window of ``w`` zero
    volumes resets it to exactly 0. No value when that mean is not positive.

    Declared limitation (F3E2-07), the same as TA-Lib's: the running total rounds. A
    volume some 10^16 times the others swallows the ones added while it is in the total,
    and the error shows once it leaves: with 1e12, 1e-6, 1e-6, 1e-6 and 1e-6 the fifth
    value of RVOL(3) is 3, not 1."""

    kind = Kind.WINDOW
    dims = PRICE_VOLUME
    volume_use = VolumeUse.SCALE_INVARIANT

    def __init__(self, w: int = 20) -> None:
        if w < 1:
            raise ValueError("relative volume needs a window of at least 1")
        self.w = w
        self.name = f"RVOL({w})"
        self.inputs = self.warmup = w + 1
        self.reset()

    def reset(self) -> None:
        self._volumes: deque[float] = deque()
        self._total = 0.0
        self._zeros = 0  # consecutive zero volumes, ending at the last one added

    def _add(self, volume: float) -> None:
        self._volumes.append(volume)
        self._total += volume
        self._zeros = self._zeros + 1 if volume == 0.0 else 0
        if self._zeros >= self.w:
            self._total = 0.0

    def update(self, point: Point) -> tuple[float, ...] | None:
        volume = point.volume
        if len(self._volumes) < self.w:
            self._add(volume)
            return None
        baseline = self._total / self.w
        self._total -= self._volumes.popleft()
        self._add(volume)
        return (volume / baseline,) if baseline > 0.0 else None
