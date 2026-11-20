"""What every ficha does with the frames of an instrument (``02`` §12.3.1).

* **Signals:** at each candle t of the signal timeframe and each direction the market
  allows, the ficha reads the values it uses at t and before. The signal exists in D_s, the
  latest of their availabilities, if its preparation holds and its context holds at D_s.
* **Decision:** the K execution candles that start when t ends are walked with the rule of
  §12.3.1, whose instants never go back (F3E3-01). The context is checked again at each of
  them. A decision fixes the initial stop. Its provenance holds every execution candle
  whose close was looked at, the trigger and those before it, and the context at each of
  their instants (F3E3-03).
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
    Check,
    Decision,
    Interval,
    Outcome,
    Signal,
    Trail,
    TriggerCandle,
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

    # --- its exits after the entry (§12.3.4 to §12.3.6), the same interface for the three

    @property
    def invalidation_timeframe(self) -> str:
        """The timeframe whose closed candles are checked for the invalidation."""
        raise NotImplementedError

    def invalidation(self, frames: Frames, signal: Signal, open_time: datetime) -> Check | None:
        """The check at the candle ``open_time`` of the invalidation timeframe; None if
        that candle is not usable or lacks a value: it is not checked."""
        raise NotImplementedError

    def trailing(self, frames: Frames, signal: Signal, open_time: datetime) -> Trail | None:
        """What the candle ``open_time`` of the signal timeframe proposes for the stop.
        Only the trend trails."""
        return None

    def entry_target(self, signal: Signal, entry: float, stop: float) -> float | None:
        """The target fixed when entering, if the ficha has one."""
        return None

    def time_limit(self, entry_minute: datetime) -> datetime | None:
        """From when the time exit applies, if the ficha has one."""
        return None

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
        provenance = self._examined(frames, signal, result.examined)
        if result.candle is None or result.candle.close is None:
            return Decision(signal, result.outcome, result.at, None, None, None, provenance)
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

    def _examined(
        self, frames: Frames, signal: Signal, examined: Sequence[tuple[TriggerCandle, datetime]]
    ) -> tuple[Interval, ...]:
        """The provenance of what the walk gave: the signal's, every execution candle whose
        close was looked at, and the context at each of their instants (F3E3-03). Each
        original minute counts once."""
        execution, context = step_of(self.params.execution), step_of(self.params.context)
        spans = list(signal.provenance)
        for instant in sorted({at for _, at in examined}):
            used = self.context(frames, signal.direction, instant) or ()
            spans.extend(value_span(v, context) for v in used)
        spans.extend(candle_span(candle.open_time, execution) for candle, _ in examined)
        return merged(spans)

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
