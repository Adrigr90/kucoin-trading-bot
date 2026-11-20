"""The regime of a candle (``02`` §12.3.2; F3-A7).

* **Direction**, from the ADX(14) of the candle: ``tendencia`` at 25 or more, ``rango`` at
  20 or less, and ``transicion`` between them. Without ADX there is no direction.
* **Volatility**, from two measures without units, each compared with its last ``W`` = 100
  values, from t-99 to t, which must all exist on consecutive candles:
  - **expansion**: the relative ATR (ATR(14)/C) of t is strictly above at least 80 % of
    its window;
  - **compression**: the Bollinger width ((upper - lower)/middle of Bollinger(20, 2)) of t
    is strictly below at least 80 % of its window.

  The label is ``expansion`` if the first holds, ``compresion`` if only the second does,
  and ``normal`` if neither. Ties never make an extreme, so a flat series is ``normal``.
  The counts are integers and every comparison with a threshold is exact.
* **Deterministic**: it comes only from the indicators of §12.2. Each label is available at
  the latest availability of the values it uses, and its inputs start at the earliest of
  theirs.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from kbot.indicators.price import ADX, ATR, Bollinger
from kbot.indicators.series import Point, Value, compute


class Direction(StrEnum):
    TENDENCIA = "tendencia"
    RANGO = "rango"
    TRANSICION = "transicion"


class Volatility(StrEnum):
    EXPANSION = "expansion"
    COMPRESION = "compresion"
    NORMAL = "normal"


@dataclass(frozen=True)
class RegimeParams:
    """The parameters of the regime, the same in the three fichas (§12.3.3)."""

    adx: int = 14
    trend_from: Decimal = Decimal("25")  # adx_tendencia
    range_up_to: Decimal = Decimal("20")  # adx_rango
    atr: int = 14
    bollinger: int = 20
    bollinger_k: Decimal = Decimal("2")
    window: int = 100  # ventana, W
    expansion_above: Decimal = Decimal("80")  # expansion_mayor_que, in percent of W
    compression_below: Decimal = Decimal("80")  # compresion_menor_que, in percent of W

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError("the regime needs a window of at least 1")
        if self.range_up_to >= self.trend_from:
            raise ValueError("the ADX of a range must be below that of a trend")

    def as_json(self) -> dict[str, object]:
        return {
            "adx": self.adx,
            "adx_tendencia": self.trend_from,
            "adx_rango": self.range_up_to,
            "atr": self.atr,
            "bollinger": self.bollinger,
            "bollinger_k": self.bollinger_k,
            "ventana": self.window,
            "expansion_mayor_que": self.expansion_above,
            "compresion_menor_que": self.compression_below,
        }


def direction(adx: float, params: RegimeParams | None = None) -> Direction:
    """The direction for an ADX value. The comparison is exact: ``Decimal`` of a float is
    its exact value."""
    params = params or RegimeParams()
    exact = Decimal(adx)
    if exact >= params.trend_from:
        return Direction.TENDENCIA
    if exact <= params.range_up_to:
        return Direction.RANGO
    return Direction.TRANSICION


def volatility(
    relative_atr: Sequence[float], width: Sequence[float], params: RegimeParams | None = None
) -> Volatility:
    """The volatility of the last candle of two windows of ``W`` values, oldest first."""
    params = params or RegimeParams()
    w = params.window
    if len(relative_atr) != w or len(width) != w:
        raise ValueError(f"the volatility needs exactly {w} values of each measure")
    below = sum(1 for x in relative_atr if x < relative_atr[-1])
    if Decimal(100 * below) >= params.expansion_above * w:
        return Volatility.EXPANSION
    above = sum(1 for x in width if x > width[-1])
    if Decimal(100 * above) >= params.compression_below * w:
        return Volatility.COMPRESION
    return Volatility.NORMAL


@dataclass(frozen=True)
class Regime:
    """The two labels of the candle that opens at ``open_time``, each with when it is
    available and where its inputs start (None without that label)."""

    open_time: datetime
    direction: Direction | None
    direction_at: datetime | None
    direction_from: datetime | None
    volatility: Volatility | None
    volatility_at: datetime | None
    volatility_from: datetime | None


@dataclass(frozen=True)
class _Measure:
    open_time: datetime
    x: float
    available_at: datetime
    inputs_from: datetime


def regimes(
    points: Sequence[Point], step: timedelta, params: RegimeParams | None = None
) -> dict[datetime, Regime]:
    """The regime of every candle of a series that has at least one label."""
    params = params or RegimeParams()
    adx = {v.open_time: v for v in compute(ADX(params.adx), points, step)}
    closes = {p.open_time: p.close for p in points}
    relative_atr = _windows(
        (
            _Measure(v.open_time, v.outputs[0] / closes[v.open_time], v.available_at, v.inputs_from)
            for v in compute(ATR(params.atr), points, step)
            if closes[v.open_time] > 0.0
        ),
        step,
        params.window,
    )
    width = _windows(
        (
            _Measure(
                v.open_time,
                (v.outputs[0] - v.outputs[2]) / v.outputs[1],
                v.available_at,
                v.inputs_from,
            )
            for v in compute(Bollinger(params.bollinger, float(params.bollinger_k)), points, step)
            if v.outputs[1] > 0.0
        ),
        step,
        params.window,
    )
    result = {}
    for open_time in sorted(adx.keys() | (relative_atr.keys() & width.keys())):
        trend: Value | None = adx.get(open_time)
        label = at = start = None
        if open_time in relative_atr and open_time in width:
            atrs, widths = relative_atr[open_time], width[open_time]
            label = volatility([m.x for m in atrs], [m.x for m in widths], params)
            at = max(m.available_at for m in (*atrs, *widths))
            start = min(m.inputs_from for m in (*atrs, *widths))
        result[open_time] = Regime(
            open_time,
            direction(trend.outputs[0], params) if trend is not None else None,
            trend.available_at if trend is not None else None,
            trend.inputs_from if trend is not None else None,
            label,
            at,
            start,
        )
    return result


def _windows(
    measures: Iterable[_Measure], step: timedelta, w: int
) -> dict[datetime, tuple[_Measure, ...]]:
    """For each measure, its window of the last ``w``, if they are all on consecutive
    candles."""
    windows = {}
    run: deque[_Measure] = deque(maxlen=w)
    for measure in measures:
        if run and measure.open_time - run[-1].open_time != step:
            run.clear()
        run.append(measure)
        if len(run) == w:
            windows[measure.open_time] = tuple(run)
    return windows
