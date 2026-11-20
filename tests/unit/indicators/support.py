"""Series of points for the indicator tests, the real candles of the fixtures, and the
tolerances of the comparison with TA-Lib (02 §12.2, point 5)."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kbot.history.timeframes import Quality
from kbot.indicators.series import Point
from kbot.market.last_candles import STABILIZATION

MINUTE = timedelta(minutes=1)
START = datetime(2026, 1, 5, tzinfo=UTC)  # a Monday, 00:00 UTC
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "indicators"


def make_points(
    rows: list[tuple[float, float, float, float, float]],
    *,
    step: timedelta = MINUTE,
    start: datetime = START,
    offsets: list[int] | None = None,
) -> list[Point]:
    """Points of a series from ``(open, high, low, close, volume)`` rows: consecutive, or
    at the given ``offsets`` (in steps) to leave gaps. Every dimension only REST, as the
    history, and each available at its end plus the stabilization margin."""
    offsets = offsets if offsets is not None else list(range(len(rows)))
    minutes = int(step / MINUTE)
    points = []
    for offset, (o, h, low, c, v) in zip(offsets, rows, strict=True):
        opened = start + offset * step
        points.append(
            Point(
                opened,
                o,
                h,
                low,
                c,
                v,
                True,
                Quality.SOLO_REST,
                Quality.SOLO_REST,
                Quality.SOLO_REST,
                opened + step + STABILIZATION,
                minutes,
                minutes,
            )
        )
    return points


def walk(n: int, seed: int, scale: float = 1.0) -> list[tuple[float, float, float, float, float]]:
    """A random walk of ``n`` candles around 100 times ``scale``, with positive volumes."""
    rng = random.Random(seed)
    rows = []
    close = 100.0
    for _ in range(n):
        o = close
        close = max(1.0, close + rng.gauss(0.0, 1.0))
        h = max(o, close) + rng.uniform(0.0, 1.0)
        low = min(o, close) - rng.uniform(0.0, 1.0)
        v = rng.uniform(1.0, 10.0)
        rows.append((o * scale, h * scale, low * scale, close * scale, v * scale))
    return rows


def fixture(name: str) -> tuple[list[tuple[float, float, float, float, float]], list[int]]:
    """The rows of a real day and the minute of the day of each (its offset)."""
    rows, offsets = [], []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or line.startswith("apertura"):
            continue
        hhmm, *values = line.split(",")
        hours, minutes = (int(x) for x in hhmm.split(":"))
        offsets.append(hours * 60 + minutes)
        o, h, low, c, v = (float(x) for x in values)
        rows.append((o, h, low, c, v))
    return rows, offsets


def with_quality(point: Point, **qualities: Quality) -> Point:
    return replace(point, **qualities)


# --- tolerances (02 §12.2, point 5) ------------------------------------------------------

PRICE_UNITS = "precio"
PERCENT = "0-100"
RATIO = "cociente"


def close_enough(ours: float, reference: float, unit: str, scale: float) -> bool:
    """Whether ``ours`` matches TA-Lib's ``reference`` for an output of ``unit``; ``scale``
    is the price scale S of the series compared: its largest |H|, |L| or |C|."""
    if unit == PRICE_UNITS:
        return abs(ours - reference) <= 1e-9 * abs(reference) + 1e-12 * scale
    if unit == PERCENT:
        return abs(ours - reference) <= 1e-9
    if unit == RATIO:
        return abs(ours - reference) <= 1e-9 * abs(reference) + 1e-12
    raise ValueError(f"unknown unit {unit!r}")


def price_scale(rows: list[tuple[float, float, float, float, float]]) -> float:
    return max(max(abs(h), abs(low), abs(c)) for _o, h, low, c, _v in rows)
