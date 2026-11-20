"""The fichas on series of candles (02 §12.3.1 and §12.3.7): signals and decisions from a
random walk of minutes, with short periods so that they appear in a few days. Poisoning
everything after D_s changes no signal, and everything after a decision changes neither the
decision nor its stop (F3-A4, as far as signals go). Also: Spot has no shorts, the context
candle is never an older one, an unusable execution candle resolves when that is known, and
the share only REST counts original minutes."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from kbot.market.last_candles import STABILIZATION
from kbot.strategies.breakout import Breakout, BreakoutParams
from kbot.strategies.ficha import Ficha
from kbot.strategies.frames import ONE_MINUTE, Frame, known_unusable, rest_only, trigger_candle
from kbot.strategies.mean_reversion import MeanReversion, MeanReversionParams
from kbot.strategies.regime import RegimeParams
from kbot.strategies.rules import LONG, SHORT, Outcome, context_open, execution_minute
from kbot.strategies.trend import Trend, TrendParams
from tests.unit.strategies.support import (
    MINUTE,
    START,
    contrasted,
    poisoned,
    walk_minutes,
)

DAYS = 4
UNTIL = START + timedelta(days=DAYS)
REGIME = RegimeParams(adx=3, atr=3, bollinger=5, window=10)
# A random walk has an ADX of 3 above 20 nearly always: the mean reversion sees a range
# with higher thresholds. The approved ones are checked at their frontiers elsewhere.
WIDE_RANGE = RegimeParams(
    adx=3, atr=3, bollinger=5, window=10, trend_from=Decimal("70"), range_up_to=Decimal("50")
)
SHORT_TIMEFRAMES = {"context": "1h", "signal": "15m", "execution": "5m", "trigger_candles": 3}


def fichas() -> list[Ficha]:
    return [
        Trend(
            TrendParams(
                REGIME,
                **SHORT_TIMEFRAMES,
                context_fast=3,
                context_slow=6,
                fast=3,
                slow=6,
                pullback=3,
                macd=(2, 4, 2),
                atr=3,
            )
        ),
        Breakout(
            BreakoutParams(
                REGIME,
                **SHORT_TIMEFRAMES,
                context_slow=6,
                pivot_k=2,
                pivot_m=20,
                relative_volume=5,
                relative_volume_min=Decimal("1.2"),
                atr=3,
            )
        ),
        MeanReversion(
            MeanReversionParams(
                WIDE_RANGE,
                **SHORT_TIMEFRAMES,
                context_bollinger=10,  # with 5 closes, one can never be beyond 2 sigma
                bollinger=10,
                rsi=3,
                atr=3,
                vwap_atr=Decimal("0.5"),
            )
        ),
    ]


MINUTES = walk_minutes(DAYS * 1440, seed=9)
IDS = [f.family for f in fichas()]


@pytest.fixture(scope="module", params=range(3), ids=IDS)
def scanned(request: pytest.FixtureRequest) -> tuple[Ficha, dict[str, Frame], list, list]:
    ficha = fichas()[request.param]
    frames = dict(ficha.frames(MINUTES, downloaded_until=UNTIL))
    signals = ficha.signals(frames)
    decisions = [ficha.decide(frames, s) for s in signals]
    return ficha, frames, signals, decisions


def test_each_ficha_finds_signals_and_decisions(scanned) -> None:
    ficha, frames, signals, decisions = scanned
    assert {s.direction for s in signals} == {LONG, SHORT}
    assert any(d.decided for d in decisions)
    signal_step = frames[ficha.params.signal].step
    for signal in signals:
        assert signal.at >= signal.open_time + signal_step + STABILIZATION
        assert signal.context_open == context_open(signal.at, frames[ficha.params.context].step)
        assert signal.provenance and signal.provenance[-1][1] == signal.open_time + signal_step
    for decision in decisions:
        assert decision.at >= decision.signal.at  # decision >= availability
        if decision.decided:
            d, close, stop = decision.signal.direction, decision.trigger_close, decision.stop
            assert close is not None and stop is not None and d * (close - stop) > 0.0
            assert execution_minute(decision.at) > decision.at


def test_poisoning_after_d_s_changes_no_signal(scanned) -> None:
    ficha, _, signals, _ = scanned
    for index, signal in enumerate(signals[:: max(1, len(signals) // 4)]):
        frames = ficha.frames(poisoned(MINUTES, signal.at, seed=index), downloaded_until=UNTIL)
        assert ficha.signal(frames, signal.open_time, signal.direction) == signal


def test_poisoning_after_the_decision_changes_neither_the_decision_nor_its_stop(
    scanned,
) -> None:
    ficha, _, _, decisions = scanned
    decided = [d for d in decisions if d.decided]
    for index, decision in enumerate(decided[:: max(1, len(decided) // 4)]):
        frames = ficha.frames(poisoned(MINUTES, decision.at, seed=index), downloaded_until=UNTIL)
        assert ficha.decide(frames, decision.signal) == decision


def test_spot_has_no_shorts(scanned) -> None:
    ficha, _, signals, _ = scanned
    spot = [replace(m, market="SPOT", symbol="ETH-USDT") for m in MINUTES]
    found = ficha.signals(ficha.frames(spot, downloaded_until=UNTIL))
    assert found and {s.direction for s in found} == {LONG}
    assert len(found) == sum(1 for s in signals if s.direction == LONG)


def test_the_context_candle_is_never_an_older_one(scanned) -> None:
    """With the values of its context candle not yet available at D_s, there is no
    context, though the candle before has its values."""
    ficha, frames, signals, _ = scanned
    context = frames[ficha.params.context]
    signal = next(
        s
        for s in signals
        if all(s.context_open - context.step in values for values in context.values.values())
    )
    late = signal.at + timedelta(seconds=1)
    held = {
        name: {
            o: replace(v, available_at=late) if o == signal.context_open else v
            for o, v in values.items()
        }
        for name, values in context.values.items()
    }
    changed = {**frames, ficha.params.context: replace(context, values=held)}
    earlier = signal.context_open - context.step
    assert all(earlier in values for values in context.values.values())
    assert ficha.context(changed, signal.direction, signal.at) is None
    assert ficha.signal(changed, signal.open_time, signal.direction) is None


USED = {  # the values each ficha reads at the signal candle (the level, at the one before)
    "tendencia": lambda d: ("ema_rapida", "ema_lenta", "macd", "atr"),
    "breakout": lambda d: ("resistencia" if d == LONG else "soporte", "volumen_relativo", "atr"),
    "reversion_media": lambda d: ("bollinger", "rsi", "atr", "vwap"),
}


def test_without_a_value_it_needs_there_is_no_signal(scanned) -> None:
    ficha, frames, signals, _ = scanned
    frame = frames[ficha.params.signal]
    for signal in signals[:3]:
        t = signal.open_time
        for name in USED[ficha.family](signal.direction):
            values = {o: v for o, v in frame.values[name].items() if o not in (t, t - frame.step)}
            without = {
                **frames,
                ficha.params.signal: replace(frame, values={**frame.values, name: values}),
            }
            assert ficha.signal(without, t, signal.direction) is None, name
        assert ficha.signal(frames, t, signal.direction) == signal


def test_an_unusable_execution_candle_resolves_when_that_is_known() -> None:
    skipped = 2 * 1440 + 7  # the third day, 00:07: inside the 5 m candle [00:05, 00:10)
    minutes = walk_minutes(DAYS * 1440, seed=7, skip=[skipped])
    ficha = fichas()[0]
    frames = ficha.frames(minutes, downloaded_until=UNTIL)
    opened = START + timedelta(days=2, minutes=5)
    candle = trigger_candle(frames["5m"], frames[ONE_MINUTE], opened)
    absent = START + skipped * MINUTE
    assert candle.close is None
    assert candle.resolved_at == absent + MINUTE + STABILIZATION
    assert known_unusable(frames["5m"], frames[ONE_MINUTE], opened) == candle.resolved_at
    following = trigger_candle(frames["5m"], frames[ONE_MINUTE], opened + 5 * MINUTE)
    assert following.close is not None
    assert following.resolved_at == opened + 10 * MINUTE + STABILIZATION


def test_the_share_only_rest_counts_original_minutes(scanned) -> None:
    ficha, frames, signals, _ = scanned
    signal = signals[0]
    total, rest = rest_only(signal.provenance, frames[ONE_MINUTE])
    expected = sum((end - start) // MINUTE for start, end in signal.provenance)
    assert total == expected and rest == total  # the history: 100 % only REST
    # The same minutes, contrasted from the opening of the signal candle on: those of its
    # provenance before it are still only REST, each counted once.
    mixed = [contrasted(m) if m.candle.open_time >= signal.open_time else m for m in MINUTES]
    again = ficha.frames(mixed, downloaded_until=UNTIL)
    total2, rest2 = rest_only(signal.provenance, again[ONE_MINUTE])
    before = sum(
        (min(end, signal.open_time) - start) // MINUTE
        for start, end in signal.provenance
        if start < signal.open_time
    )
    assert total2 == total and rest2 == before < total


def test_every_expiry_has_its_reason(scanned) -> None:
    _, _, _, decisions = scanned
    for decision in decisions:
        assert decision.outcome in set(Outcome)
        assert (decision.stop is None) == (not decision.decided)
