"""The breakout ficha (``02`` §12.3.5), approved by the owner on 20 November 2026.

* **Context, 4 h:** the close on the side of the position from EMA(50).
* **Signal, 1 h:** (a) volatility ``compresion`` in one of the candles t-5 to t-1; (b) the
  first close beyond the level of t-1: above the resistance(5, 100) when long, below the
  support(5, 100) when short, which must exist; (c) relative volume(20) of t >= 1.5.
* **Trigger, 15 m, K = 4:** a close beyond the extreme of the signal candle.
* **Stop:** 2 ATR(14) of 1 h from the trigger close. **Target:** 2 R from the entry, fixed
  when entering; no entry if it is not positive (F3E3-02).
* **Invalidation**, at each 1 h candle that ends after the entry: the close goes back to the
  broken level, which stays fixed. No time exit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from kbot.indicators.levels import Resistance, Support
from kbot.indicators.price import ATR, EMA
from kbot.indicators.series import PRICE, Indicator, Value
from kbot.indicators.volume import RelativeVolume
from kbot.strategies.ficha import Ficha, Frames, check_timeframes
from kbot.strategies.frames import candle_span
from kbot.strategies.regime import RegimeParams, Volatility
from kbot.strategies.rules import LONG, Check, Signal, at_least, beyond_extreme, context_open

FAMILY = "breakout"


@dataclass(frozen=True)
class BreakoutParams:
    regime: RegimeParams = field(default_factory=RegimeParams)
    context: str = "4h"
    signal: str = "1h"
    execution: str = "15m"
    context_slow: int = 50
    pivot_k: int = 5
    pivot_m: int = 100
    compression_candles: int = 5
    relative_volume: int = 20
    relative_volume_min: Decimal = Decimal("1.5")
    atr: int = 14
    trigger_candles: int = 4
    stop_atr: Decimal = Decimal("2")
    target_r: Decimal = Decimal("2")

    def __post_init__(self) -> None:
        check_timeframes(self)
        if self.compression_candles < 1:
            raise ValueError("the compression looks at one candle at least")

    def as_json(self) -> dict[str, object]:
        return {
            "familia": FAMILY,
            "temporalidades": {
                "contexto": self.context,
                "senal": self.signal,
                "ejecucion": self.execution,
            },
            "regimen": self.regime.as_json(),
            "contexto": {"ema_lenta": self.context_slow},
            "senal": {
                "pivote_k": self.pivot_k,
                "pivote_m": self.pivot_m,
                "compresion_velas": self.compression_candles,
                "volumen_relativo": self.relative_volume,
                "volumen_relativo_minimo": self.relative_volume_min,
                "atr": self.atr,
            },
            "ejecucion": {"velas": self.trigger_candles},
            "stop": {"multiplo_atr": self.stop_atr},
            "objetivo": {"multiplo_r": self.target_r},
        }


# --- the rules, on plain values ----------------------------------------------------------


def context_holds(d: int, close: float, ema_slow: float) -> bool:
    return close > ema_slow if d == LONG else close < ema_slow


def setup_holds(
    d: int,
    labels: Sequence[Volatility | None],
    close: float,
    level: float,
    relative_volume: float,
    minimum: Decimal,
) -> bool:
    """``labels``: the volatility of the candles t-5 to t-1 (None without a value);
    ``level``: the resistance (support when short) of t-1."""
    if Volatility.COMPRESION not in labels or not at_least(relative_volume, minimum):
        return False
    return close > level if d == LONG else close < level


def target(d: int, entry: float, stop: float, multiple: Decimal) -> float:
    """The target, ``multiple`` R from the entry."""
    return entry + d * float(multiple) * (d * (entry - stop))


def invalidated(d: int, close: float, level: float) -> bool:
    """The close is back on the other side of the broken level."""
    return close <= level if d == LONG else close >= level


# --- the ficha on the frames -------------------------------------------------------------


class Breakout(Ficha):
    family = FAMILY
    params: BreakoutParams

    def __init__(self, params: BreakoutParams | None = None) -> None:
        super().__init__(params or BreakoutParams())

    def indicators(self) -> dict[str, dict[str, Indicator]]:
        p = self.params
        return {
            p.context: {"ema_lenta": EMA(p.context_slow)},
            p.signal: {
                "resistencia": Resistance(p.pivot_k, p.pivot_m),
                "soporte": Support(p.pivot_k, p.pivot_m),
                "volumen_relativo": RelativeVolume(p.relative_volume),
                "atr": ATR(p.atr),
            },
        }

    def signal(self, frames: Frames, t: datetime, d: int) -> Signal | None:
        p = self.params
        frame = frames[p.signal]
        level = frame.value("resistencia" if d == LONG else "soporte", t - frame.step)
        volume = frame.value("volumen_relativo", t)
        atr = frame.value("atr", t)
        point = frame.points.get(t)
        if level is None or volume is None or atr is None or point is None:
            return None
        earlier = [
            frame.regimes.get(t - i * frame.step) for i in range(p.compression_candles, 0, -1)
        ]
        seen = [r for r in earlier if r is not None and r.volatility is not None]
        labels = [r.volatility for r in seen]
        if not setup_holds(
            d, labels, point.close, level.outputs[0], volume.outputs[0], p.relative_volume_min
        ):
            return None
        if atr.outputs[0] <= 0.0:
            return None
        used = [level, volume, atr]
        instants = [v.available_at for v in used]
        instants += [r.volatility_at for r in seen if r.volatility_at is not None]
        at = max(instants)
        context = self.context(frames, d, at)
        if context is None:
            return None
        spans = [(r.volatility_from, r.open_time + frame.step) for r in seen if r.volatility_from]
        levels = (
            ("H", point.high),
            ("L", point.low),
            ("atr", atr.outputs[0]),
            ("nivel", level.outputs[0]),
        )
        return self.make_signal(frames, t, d, at, levels, used, context, spans)

    def context(self, frames: Frames, d: int, at: datetime) -> Sequence[Value] | None:
        frame = frames[self.params.context]
        opened = context_open(at, frame.step)
        slow = frame.value("ema_lenta", opened, at)
        if slow is None:
            return None
        point = frame.points[opened]  # it has an EMA value, so it is there
        return (slow,) if context_holds(d, point.close, slow.outputs[0]) else None

    def fires(self, signal: Signal, close: float) -> bool:
        return beyond_extreme(signal.direction, close, signal.level("H"), signal.level("L"))

    def entry_target(self, signal: Signal, entry: float, stop: float) -> float:
        return target(signal.direction, entry, stop, self.params.target_r)

    @property
    def invalidation_timeframe(self) -> str:
        return self.params.signal

    def invalidation(self, frames: Frames, signal: Signal, open_time: datetime) -> Check | None:
        """At the 1 h candle ``open_time``, against the broken level of the signal, which
        stays fixed; None if the candle is not usable: it is not checked."""
        frame = frames[self.params.signal]
        point = frame.usable(open_time, dims=PRICE)
        if point is None or point.available_at is None:
            return None
        exits = invalidated(signal.direction, point.close, signal.level("nivel"))
        return Check(point.available_at, exits, (candle_span(open_time, frame.step),))
