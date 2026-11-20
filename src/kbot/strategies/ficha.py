"""What every ficha does with the frames of an instrument (``02`` §12.3.1).

* **Signals:** at each candle t of the signal timeframe and each direction the market
  allows, the ficha reads the values it uses at t and before. The signal exists in D_s, the
  latest of their availabilities, if its preparation holds and its context holds at D_s.
* **Decision:** the K execution candles that start when t ends are walked with the rule of
  §12.3.1, whose instants never go back (F3E3-01). The context is checked again at each of
  them. A decision fixes the initial stop.
* Each ficha declares its indicators by timeframe, its preparation, its context and its
  trigger; everything else is here, the same for the three.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from functools import partial
from typing import Protocol

from kbot.history.timeframes import MinuteCandle
from kbot.indicators.series import Indicator, Value
from kbot.strategies.frames import (
    ONE_MINUTE,
    Frame,
    build_frames,
    candle_span,
    merged,
    step_of,
    trigger_candle,
    value_span,
)
from kbot.strategies.regime import RegimeParams
from kbot.strategies.rules import (
    Decision,
    Interval,
    Outcome,
    Signal,
    context_open,
    directions,
    initial_stop,
    walk,
)
from kbot.strategies.version import params_hash

Frames = Mapping[str, Frame]


class FichaParams(Protocol):
    regime: RegimeParams
    context: str
    signal: str
    execution: str
    trigger_candles: int
    stop_atr: Decimal

    def as_json(self) -> dict[str, object]: ...


def check_timeframes(params: FichaParams) -> None:
    """K execution candles last what one signal candle does (§12.3.1)."""
    if params.trigger_candles < 1:
        raise ValueError("a ficha needs at least one trigger candle")
    if step_of(params.execution) * params.trigger_candles != step_of(params.signal):
        raise ValueError("K execution candles must last what one signal candle does")
    if not step_of(params.context) > step_of(params.signal) > step_of(params.execution):
        raise ValueError("the context, signal and execution timeframes must go down")


class Ficha:
    family: str

    def __init__(self, params: FichaParams) -> None:
        check_timeframes(params)
        self.params = params

    # --- what each ficha declares ------------------------------------------------------

    def indicators(self) -> dict[str, dict[str, Indicator]]:
        """Fresh indicators, by timeframe and name."""
        raise NotImplementedError

    def signal(self, frames: Frames, t: datetime, d: int) -> Signal | None:
        raise NotImplementedError

    def context(self, frames: Frames, d: int, at: datetime) -> Sequence[Value] | None:
        """The values the context uses at ``at`` if it holds; None if it does not."""
        raise NotImplementedError

    def fires(self, signal: Signal, close: float) -> bool:
        raise NotImplementedError

    # --- the same for the three --------------------------------------------------------

    def params_hash(self) -> str:
        return params_hash(self.params.as_json())

    def frames(self, minutes: Iterable[MinuteCandle], *, downloaded_until: datetime) -> Frames:
        p = self.params
        return build_frames(
            minutes,
            downloaded_until=downloaded_until,
            timeframes=(p.context, p.signal, p.execution),
            indicators=self.indicators(),
            regime={p.context: p.regime, p.signal: p.regime},
        )

    def signals(self, frames: Frames) -> list[Signal]:
        frame = frames[self.params.signal]
        found = []
        for t in sorted(frame.points):
            for d in directions(frame.market):
                signal = self.signal(frames, t, d)
                if signal is not None:
                    found.append(signal)
        return found

    def decide(self, frames: Frames, signal: Signal) -> Decision:
        p = self.params
        execution = frames[p.execution]
        start = signal.open_time + step_of(p.signal)
        candles = [
            trigger_candle(execution, frames[ONE_MINUTE], start + i * execution.step)
            for i in range(p.trigger_candles)
        ]
        holds = partial(self._context_holds, frames, signal.direction)
        result = walk(signal.at, candles, partial(self.fires, signal), holds)
        if result.candle is None or result.candle.close is None:
            return Decision(signal, result.outcome, result.at, None, None, None, signal.provenance)
        used = self.context(frames, signal.direction, result.at) or ()
        context_step = step_of(p.context)
        provenance = merged(
            (
                *signal.provenance,
                candle_span(result.candle.open_time, execution.step),
                *(value_span(v, context_step) for v in used),
            )
        )
        stop = initial_stop(signal.direction, result.candle.close, signal.level("atr"), p.stop_atr)
        return Decision(
            signal,
            Outcome.DECISION,
            result.at,
            result.candle.open_time,
            result.candle.close,
            stop,
            provenance,
        )

    def _context_holds(self, frames: Frames, d: int, at: datetime) -> bool:
        return self.context(frames, d, at) is not None

    def make_signal(
        self,
        frames: Frames,
        t: datetime,
        d: int,
        at: datetime,
        levels: Sequence[tuple[str, float]],
        used: Sequence[Value],
        context: Sequence[Value],
        spans: Sequence[Interval] = (),
    ) -> Signal:
        """The signal of candle ``t`` in ``at``: with its levels, the regime of its context
        candle and of ``t``, and the provenance of every value it used."""
        p = self.params
        frame, context_frame = frames[p.signal], frames[p.context]
        opened = context_open(at, context_frame.step)
        provenance = merged(
            (
                *(value_span(v, frame.step) for v in used),
                *(value_span(v, context_frame.step) for v in context),
                *spans,
            )
        )
        return Signal(
            self.family,
            frame.market,
            frame.symbol,
            p.signal,
            t,
            d,
            at,
            opened,
            tuple(levels),
            context_frame.regime(opened, at),
            frame.regime(t, at),
            provenance,
        )
