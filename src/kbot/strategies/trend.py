"""The trend ficha (``02`` §12.3.4), approved by the owner on 20 November 2026.

* **Context, 4 h:** EMA(20) beyond EMA(50) on the side of the position, direction
  ``tendencia`` and the DI of that side above the other.
* **Signal, 1 h:** (a) the close beyond EMA(50); (b) a pullback: a low at or below EMA(20)
  (a high at or above it when short) in one of the candles t-4 to t; (c) the MACD(12, 26,
  9) histogram goes from <= 0 at t-1 to > 0 at t (from >= 0 to < 0 when short).
* **Trigger, 15 m, K = 4:** a close beyond the extreme of the signal candle.
* **Stop:** 2 ATR(14) of 1 h from the trigger close; **trailing**, at each 1 h candle that
  ends after the entry, to the close minus 3 of its ATR(14), only closer.
* **Invalidation**, at each 4 h candle that ends after the entry: EMA(20) no longer on the
  side of the position, or direction ``rango``. No target and no time exit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from kbot.indicators.price import ADX, ATR, DMI, EMA, MACD
from kbot.indicators.series import Indicator, Value
from kbot.strategies.ficha import Ficha, Frames, check_timeframes
from kbot.strategies.frames import merged, value_span
from kbot.strategies.regime import Direction, RegimeParams, direction
from kbot.strategies.rules import LONG, Check, Signal, Trail, beyond_extreme, context_open

FAMILY = "tendencia"


@dataclass(frozen=True)
class TrendParams:
    regime: RegimeParams = field(default_factory=RegimeParams)
    context: str = "4h"
    signal: str = "1h"
    execution: str = "15m"
    context_fast: int = 20
    context_slow: int = 50
    fast: int = 20
    slow: int = 50
    pullback: int = 5
    macd: tuple[int, int, int] = (12, 26, 9)
    atr: int = 14
    trigger_candles: int = 4
    stop_atr: Decimal = Decimal("2")
    trail_atr: Decimal = Decimal("3")

    def __post_init__(self) -> None:
        check_timeframes(self)
        if self.pullback < 1:
            raise ValueError("the pullback looks at one candle at least")

    def as_json(self) -> dict[str, object]:
        return {
            "familia": FAMILY,
            "temporalidades": {
                "contexto": self.context,
                "senal": self.signal,
                "ejecucion": self.execution,
            },
            "regimen": self.regime.as_json(),
            "contexto": {"ema_rapida": self.context_fast, "ema_lenta": self.context_slow},
            "senal": {
                "ema_rapida": self.fast,
                "ema_lenta": self.slow,
                "retroceso": self.pullback,
                "macd": list(self.macd),
                "atr": self.atr,
            },
            "ejecucion": {"velas": self.trigger_candles},
            "stop": {"multiplo_atr": self.stop_atr},
            "arrastre": {"multiplo_atr": self.trail_atr},
        }


# --- the rules, on plain values ----------------------------------------------------------


def context_holds(
    d: int,
    ema_fast: float,
    ema_slow: float,
    adx: float,
    plus_di: float,
    minus_di: float,
    regime: RegimeParams | None = None,
) -> bool:
    if direction(adx, regime) is not Direction.TENDENCIA:
        return False
    if d == LONG:
        return ema_fast > ema_slow and plus_di > minus_di
    return ema_fast < ema_slow and minus_di > plus_di


def setup_holds(
    d: int,
    close: float,
    ema_slow: float,
    extremes: Sequence[float],
    ema_fast: Sequence[float],
    histogram_before: float,
    histogram: float,
) -> bool:
    """``extremes`` are the lows (highs when short) of the candles t-4 to t, and
    ``ema_fast`` their EMA(20)."""
    if len(extremes) != len(ema_fast) or not extremes:
        raise ValueError("the pullback needs one EMA value for each candle")
    pairs = zip(extremes, ema_fast, strict=True)
    if d == LONG:
        pullback = any(low <= ema for low, ema in pairs)
        return close > ema_slow and pullback and histogram_before <= 0.0 < histogram
    pullback = any(high >= ema for high, ema in pairs)
    return close < ema_slow and pullback and histogram_before >= 0.0 > histogram


def invalidated(
    d: int, ema_fast: float, ema_slow: float, adx: float, regime: RegimeParams | None = None
) -> bool:
    if direction(adx, regime) is Direction.RANGO:
        return True
    return ema_fast <= ema_slow if d == LONG else ema_fast >= ema_slow


# --- the ficha on the frames -------------------------------------------------------------


class Trend(Ficha):
    family = FAMILY
    params: TrendParams

    def __init__(self, params: TrendParams | None = None) -> None:
        super().__init__(params or TrendParams())

    def indicators(self) -> dict[str, dict[str, Indicator]]:
        p = self.params
        return {
            p.context: {
                "ema_rapida": EMA(p.context_fast),
                "ema_lenta": EMA(p.context_slow),
                "adx": ADX(p.regime.adx),
                "dmi": DMI(p.regime.adx),
            },
            p.signal: {
                "ema_rapida": EMA(p.fast),
                "ema_lenta": EMA(p.slow),
                "macd": MACD(*p.macd),
                "atr": ATR(p.atr),
            },
        }

    def signal(self, frames: Frames, t: datetime, d: int) -> Signal | None:
        p = self.params
        frame = frames[p.signal]
        candles = [t - i * frame.step for i in range(p.pullback - 1, -1, -1)]
        fast = [frame.value("ema_rapida", o) for o in candles]
        slow = frame.value("ema_lenta", t)
        before = frame.value("macd", t - frame.step)
        macd = frame.value("macd", t)
        atr = frame.value("atr", t)
        if slow is None or before is None or macd is None or atr is None:
            return None
        values = [v for v in fast if v is not None]
        if len(values) != len(fast):
            return None
        points = [frame.points[o] for o in candles]  # each has an EMA value, so it is there
        extremes = [q.low if d == LONG else q.high for q in points]
        means = [v.outputs[0] for v in values]
        histogram_before, histogram = before.outputs[2], macd.outputs[2]
        last = points[-1]
        if not setup_holds(
            d, last.close, slow.outputs[0], extremes, means, histogram_before, histogram
        ):
            return None
        if atr.outputs[0] <= 0.0:
            return None
        used = [*values, slow, before, macd, atr]
        at = max(v.available_at for v in used)
        context = self.context(frames, d, at)
        if context is None:
            return None
        levels = (("H", last.high), ("L", last.low), ("atr", atr.outputs[0]))
        return self.make_signal(frames, t, d, at, levels, used, context)

    def context(self, frames: Frames, d: int, at: datetime) -> Sequence[Value] | None:
        p = self.params
        frame = frames[p.context]
        opened = context_open(at, frame.step)
        fast = frame.value("ema_rapida", opened, at)
        slow = frame.value("ema_lenta", opened, at)
        adx = frame.value("adx", opened, at)
        dmi = frame.value("dmi", opened, at)
        if fast is None or slow is None or adx is None or dmi is None:
            return None
        plus_di, minus_di = dmi.outputs[0], dmi.outputs[1]
        if not context_holds(
            d, fast.outputs[0], slow.outputs[0], adx.outputs[0], plus_di, minus_di, p.regime
        ):
            return None
        return (fast, slow, adx, dmi)

    def fires(self, signal: Signal, close: float) -> bool:
        return beyond_extreme(signal.direction, close, signal.level("H"), signal.level("L"))

    @property
    def invalidation_timeframe(self) -> str:
        return self.params.context

    def invalidation(self, frames: Frames, signal: Signal, open_time: datetime) -> Check | None:
        """At the 4 h candle ``open_time``; None if it has no values: it is not checked."""
        p = self.params
        frame = frames[p.context]
        fast = frame.value("ema_rapida", open_time)
        slow = frame.value("ema_lenta", open_time)
        adx = frame.value("adx", open_time)
        if fast is None or slow is None or adx is None:
            return None
        values = (fast, slow, adx)
        exits = invalidated(
            signal.direction, fast.outputs[0], slow.outputs[0], adx.outputs[0], p.regime
        )
        spans = merged(value_span(v, frame.step) for v in values)
        return Check(max(v.available_at for v in values), exits, spans)

    def trailing(self, frames: Frames, signal: Signal, open_time: datetime) -> Trail | None:
        """The close of the 1 h candle ``open_time`` minus 3 of its ATR(14) (plus, when
        short); None if the candle is not usable or has no ATR: the stop does not change."""
        p = self.params
        frame = frames[p.signal]
        point = frame.usable(open_time)
        atr = frame.value("atr", open_time)
        if point is None or point.available_at is None or atr is None:
            return None
        level = point.close - signal.direction * float(p.trail_atr) * atr.outputs[0]
        at = max(point.available_at, atr.available_at)
        return Trail(at, level, (value_span(atr, frame.step),))
