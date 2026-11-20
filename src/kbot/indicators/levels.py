"""Supports and resistances from pivots (``02`` §12.2, points 2 and 4).

* **A high pivot** at candle ``j``: its high strictly above the highs of the ``k`` candles
  before and of the ``k`` after. It is known when candle ``j+k`` closes, never before.
* **It expires** ``m`` candles after its own: it counts at ``t`` only if ``t - j < m``.
* **The resistance** at ``t`` is the latest high pivot, confirmed and not expired, that no
  close after ``j`` has gone above. **The support** is the same with low pivots (a low
  strictly below) and closes below. Without such a pivot there is no value.

The inputs of a value are its last ``m + k`` candles: the ``m`` where its pivots can be,
and the ``k`` before them that confirm them. Its first value can come at candle ``2k+1``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from kbot.indicators.series import Indicator, Kind, Point


@dataclass
class _Pivot:
    index: int  # j, counted from the first candle of the run
    level: float
    broken: bool = False


class _Pivots(Indicator):
    kind = Kind.WINDOW
    high: bool  # True for resistances (high pivots), False for supports

    def __init__(self, k: int = 5, m: int = 100) -> None:
        if k < 1 or m < 1:
            raise ValueError("pivots need k and m of at least 1")
        self.k, self.m = k, m
        self.inputs = m + k
        self.warmup = 2 * k + 1
        self.reset()

    def reset(self) -> None:
        self._levels: deque[float] = deque(maxlen=2 * self.k + 1)
        self._count = 0
        self._pivots: list[_Pivot] = []

    def update(self, point: Point) -> tuple[float, ...] | None:
        self._count += 1
        t = self._count
        for pivot in self._pivots:
            if not pivot.broken and self._breaks(point.close, pivot.level):
                pivot.broken = True
        self._levels.append(point.high if self.high else point.low)
        if len(self._levels) == 2 * self.k + 1:
            levels = list(self._levels)
            centre = levels[self.k]
            others = levels[: self.k] + levels[self.k + 1 :]
            if all(self._beyond(centre, other) for other in others):
                self._pivots.append(_Pivot(t - self.k, centre))
        self._pivots = [p for p in self._pivots if t - p.index < self.m]
        for pivot in reversed(self._pivots):
            if not pivot.broken:
                return (pivot.level,)
        return None

    def _beyond(self, centre: float, other: float) -> bool:
        return centre > other if self.high else centre < other

    def _breaks(self, close: float, level: float) -> bool:
        return close > level if self.high else close < level


class Resistance(_Pivots):
    high = True

    def __init__(self, k: int = 5, m: int = 100) -> None:
        super().__init__(k, m)
        self.name = f"Resistencia({k},{m})"


class Support(_Pivots):
    high = False

    def __init__(self, k: int = 5, m: int = 100) -> None:
        super().__init__(k, m)
        self.name = f"Soporte({k},{m})"
