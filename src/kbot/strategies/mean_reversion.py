"""The mean reversion ficha (``02`` §12.3.6), approved by the owner on 20 November 2026.

* **Context, 1 h:** direction ``rango``, and the close within its Bollinger(20, 2) bands.
* **Signal, 15 m:** (a) the close beyond its band (below the lower one when long); (b)
  RSI(14) <= 30 (>= 70 when short); (c) at least 1 ATR(14) away from the VWAP of the UTC
  day, on the same side. The target T is the middle of the bands at t, fixed.
* **Trigger, 5 m, K = 3:** a close back between the band of t and T.
* **Stop:** 2 ATR(14) of 15 m from the trigger close.
* **Invalidation**, at each 1 h candle that ends after the entry: direction ``tendencia``,
  or the close out of its band against the position. **Time exit:** at the opening of the
  first minute present that starts 240 minutes or more after the entry one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from kbot.indicators.price import ADX, ATR, RSI, Bollinger
from kbot.indicators.series import Indicator, Value
from kbot.indicators.volume import VWAP
from kbot.strategies.ficha import Ficha, Frames, check_timeframes
from kbot.strategies.regime import Direction, RegimeParams, direction
from kbot.strategies.rules import LONG, Check, Signal, at_least, at_most, context_open

FAMILY = "reversion_media"


@dataclass(frozen=True)
class MeanReversionParams:
    regime: RegimeParams = field(default_factory=RegimeParams)
    context: str = "1h"
    signal: str = "15m"
    execution: str = "5m"
    context_bollinger: int = 20
    context_bollinger_k: Decimal = Decimal("2")
    bollinger: int = 20
    bollinger_k: Decimal = Decimal("2")
    rsi: int = 14
    rsi_long: Decimal = Decimal("30")
    rsi_short: Decimal = Decimal("70")
    atr: int = 14
    vwap_atr: Decimal = Decimal("1")
    trigger_candles: int = 3
    stop_atr: Decimal = Decimal("2")
    time_limit_minutes: int = 240

    def __post_init__(self) -> None:
        check_timeframes(self)
        if self.time_limit_minutes < 1:
            raise ValueError("the time exit needs at least one minute")

    def as_json(self) -> dict[str, object]:
        return {
            "familia": FAMILY,
            "temporalidades": {
                "contexto": self.context,
                "senal": self.signal,
                "ejecucion": self.execution,
            },
            "regimen": self.regime.as_json(),
            "contexto": {
                "bollinger": self.context_bollinger,
                "bollinger_k": self.context_bollinger_k,
            },
            "senal": {
                "bollinger": self.bollinger,
                "bollinger_k": self.bollinger_k,
                "rsi": self.rsi,
                "rsi_largo": self.rsi_long,
                "rsi_corto": self.rsi_short,
                "atr": self.atr,
                "distancia_vwap_atr": self.vwap_atr,
            },
            "ejecucion": {"velas": self.trigger_candles},
            "stop": {"multiplo_atr": self.stop_atr},
            "salida_tiempo_minutos": self.time_limit_minutes,
        }


# --- the rules, on plain values ----------------------------------------------------------


def context_holds(
    adx: float, close: float, lower: float, upper: float, regime: RegimeParams | None = None
) -> bool:
    """The same for both directions."""
    return direction(adx, regime) is Direction.RANGO and lower <= close <= upper


def setup_holds(
    d: int,
    close: float,
    lower: float,
    upper: float,
    rsi: float,
    vwap: float,
    atr: float,
    params: MeanReversionParams | None = None,
) -> bool:
    p = params or MeanReversionParams()
    distance = float(p.vwap_atr) * atr
    if d == LONG:
        return close < lower and at_most(rsi, p.rsi_long) and vwap - close >= distance
    return close > upper and at_least(rsi, p.rsi_short) and close - vwap >= distance


def fires(d: int, close: float, lower: float, upper: float, target: float) -> bool:
    """Back between the band of the signal candle and the target, both fixed."""
    return lower < close < target if d == LONG else target < close < upper


def invalidated(
    d: int, adx: float, close: float, lower: float, upper: float, regime: RegimeParams | None = None
) -> bool:
    if direction(adx, regime) is Direction.TENDENCIA:
        return True
    return close < lower if d == LONG else close > upper


def time_limit(entry_minute: datetime, minutes: int = 240) -> datetime:
    """The exit is at the opening of the first minute present from this instant on."""
    return entry_minute + timedelta(minutes=minutes)


# --- the ficha on the frames -------------------------------------------------------------


class MeanReversion(Ficha):
    family = FAMILY
    params: MeanReversionParams

    def __init__(self, params: MeanReversionParams | None = None) -> None:
        super().__init__(params or MeanReversionParams())

    def indicators(self) -> dict[str, dict[str, Indicator]]:
        p = self.params
        return {
            p.context: {
                "adx": ADX(p.regime.adx),
                "bollinger": Bollinger(p.context_bollinger, float(p.context_bollinger_k)),
            },
            p.signal: {
                "bollinger": Bollinger(p.bollinger, float(p.bollinger_k)),
                "rsi": RSI(p.rsi),
                "atr": ATR(p.atr),
                "vwap": VWAP(),
            },
        }

    def signal(self, frames: Frames, t: datetime, d: int) -> Signal | None:
        p = self.params
        frame = frames[p.signal]
        bands = frame.value("bollinger", t)
        rsi = frame.value("rsi", t)
        atr = frame.value("atr", t)
        vwap = frame.value("vwap", t)
        point = frame.points.get(t)
        if bands is None or rsi is None or atr is None or vwap is None or point is None:
            return None
        upper, middle, lower = bands.outputs
        if not setup_holds(
            d, point.close, lower, upper, rsi.outputs[0], vwap.outputs[0], atr.outputs[0], p
        ):
            return None
        if atr.outputs[0] <= 0.0:
            return None
        used = [bands, rsi, atr, vwap]
        at = max(v.available_at for v in used)
        context = self.context(frames, d, at)
        if context is None:
            return None
        levels = (
            ("superior", upper),
            ("inferior", lower),
            ("objetivo", middle),
            ("atr", atr.outputs[0]),
        )
        return self.make_signal(frames, t, d, at, levels, used, context)

    def context(self, frames: Frames, d: int, at: datetime) -> Sequence[Value] | None:
        p = self.params
        frame = frames[p.context]
        opened = context_open(at, frame.step)
        adx = frame.value("adx", opened, at)
        bands = frame.value("bollinger", opened, at)
        if adx is None or bands is None:
            return None
        upper, _, lower = bands.outputs
        point = frame.points[opened]  # it has values, so it is there
        if not context_holds(adx.outputs[0], point.close, lower, upper, p.regime):
            return None
        return (adx, bands)

    def fires(self, signal: Signal, close: float) -> bool:
        return fires(
            signal.direction,
            close,
            signal.level("inferior"),
            signal.level("superior"),
            signal.level("objetivo"),
        )

    def invalidation(self, frames: Frames, d: int, open_time: datetime) -> Check | None:
        """At the 1 h candle ``open_time``; None if it has no values: it is not checked."""
        p = self.params
        frame = frames[p.context]
        adx = frame.value("adx", open_time)
        bands = frame.value("bollinger", open_time)
        if adx is None or bands is None:
            return None
        upper, _, lower = bands.outputs
        close = frame.points[open_time].close
        at = max(adx.available_at, bands.available_at)
        return Check(at, invalidated(d, adx.outputs[0], close, lower, upper, p.regime))

    def time_limit(self, entry_minute: datetime) -> datetime:
        return time_limit(entry_minute, self.params.time_limit_minutes)
