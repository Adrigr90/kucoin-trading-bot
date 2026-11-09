"""Last-price candles, flow and minute status (02 §3.4 and §3.5; owner decision of 8 Nov 2026)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from kbot.kucoin.jsonparse import loads
from kbot.market.last_candles import (
    CandleEvidence,
    CandleState,
    EventFlow,
    FlowMinute,
    Ohlcv,
    WsCandles,
    candle_kind,
    day_close,
    decide,
    rest_kline,
    state_without_candle,
    trade_changes,
    ws_kline,
)
from kbot.market.mark_candles import FlowState

DAY = datetime(2026, 11, 7, tzinfo=UTC)
T0 = DAY + timedelta(hours=12)
T0_S = 1_794_052_800
TICK = Decimal("0.1")
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "kucoin"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def candle(o: str = "100", h: str = "101", low: str = "99", c: str = "100.5") -> Ohlcv:
    return Ohlcv(T0, Decimal(o), Decimal(h), Decimal(low), Decimal(c), Decimal(2), Decimal(201))


def flow(state: FlowState, minute: datetime = T0, trades: int = 0) -> FlowMinute:
    """A minute of the trade channel; by default the WebSocket received no trade in it."""
    return FlowMinute("SPOT", "BTC-USDT", "trade", minute, state, trades, 0, 0)


@pytest.mark.parametrize(
    ("state", "rest", "ws", "expected", "evidence"),
    [
        (FlowState.S1, candle(), candle(), "cerrada_cruzada", "vela_cruzada_consistente"),
        (FlowState.S1, candle(), candle(h="101.1"), "cerrada_cruzada", "vela_cruzada_consistente"),
        (FlowState.S1, candle(), candle(h="101.2"), "discrepante", "vela_cruzada_discrepante"),
        (FlowState.S1, candle(), None, "discrepante", "vela_cruzada_discrepante"),
        (FlowState.S2, candle(), candle(h="150"), "cerrada_rest", "vela_rest_suficiente"),
        (FlowState.S3, candle(), None, "cerrada_rest", "vela_rest_suficiente"),
        (FlowState.S1, None, None, "ausencia_sin_trades_probable", None),
        (FlowState.S1, None, candle(), "pendiente_estabilizacion", None),
        (FlowState.S2, None, None, "ausencia_no_verificable", None),
        (FlowState.S3, None, candle(), "ausencia_no_verificable", None),
    ],
)
def test_the_candle_of_a_minute(
    state: FlowState, rest: Ohlcv | None, ws: Ohlcv | None, expected: str, evidence: str | None
) -> None:
    decision = decide("SPOT", "BTC-USDT", flow(state), rest, ws, TICK)
    assert decision.state.value == expected
    assert (decision.evidence.value if decision.evidence else None) == evidence
    if decision.state is CandleState.CERRADA_REST:
        assert decision.ws is None, "a degraded flow is never compared"


def test_volume_never_judges_the_candle_before_d26() -> None:
    other_volume = Ohlcv(T0, *(Decimal(v) for v in ("100", "101", "99", "100.5", "4", "402")))
    decision = decide("SPOT", "BTC-USDT", flow(FlowState.S1), candle(), other_volume, TICK)
    assert decision.state is CandleState.CERRADA_CRUZADA
    assert decision.ws is not None and decision.ws.volume == Decimal(4), "kept for D26"


def test_the_differences_are_named() -> None:
    decision = decide(
        "SPOT", "BTC-USDT", flow(FlowState.S1), candle(), candle(o="99.8", c="100.7"), TICK
    )
    assert decision.differences == ("open", "close")


def test_a_candle_of_another_minute_is_refused() -> None:
    late = replace(candle(), open_time=at(60))
    with pytest.raises(ValueError, match="another minute"):
        decide("SPOT", "BTC-USDT", flow(FlowState.S1), late, None, TICK)


def test_the_recorded_rest_and_websocket_candles_agree() -> None:
    """With the candles recorded in E1, the same minute is cross-checked as consistent."""
    for market, tick in (("spot", Decimal("0.1")), ("futures", Decimal("0.1"))):
        evidence = loads((FIXTURES / f"ws_{market}.json").read_bytes())["minute"]
        ws = ws_kline(evidence["kline_last"][0]["message"])
        body = loads((FIXTURES / f"v2_kline_{market}_minute.json").read_bytes())["body"]
        rows = [rest_kline(row) for row in body["data"]["list"]]
        (rest,) = [r for r in rows if r is not None and r.open_time == ws.open_time]  # type: ignore[union-attr]
        minute_flow = FlowMinute("X", "Y", "trade", rest.open_time, FlowState.S1, 1, 0, 0)
        decision = decide("X", "Y", minute_flow, rest, ws, tick)
        assert decision.state is CandleState.CERRADA_CRUZADA, market


@pytest.mark.parametrize(
    "row",
    [
        [T0_S, "100", "101", "99", "100.5", "2", "201"],
    ],
)
def test_rest_rows_are_read_with_the_e1_columns(row: list[object]) -> None:
    parsed = rest_kline(row)
    assert parsed == candle()


@pytest.mark.parametrize(
    "row",
    [
        [T0_S + 1, "100", "101", "99", "100.5", "2", "201"],  # not on a minute
        [T0_S, "100", "99", "101", "100.5", "2", "201"],  # high below low
        [T0_S, "100", "101", "99", "100.5", "-2", "201"],
        [T0_S, "NaN", "101", "99", "100.5", "2", "201"],
        [T0_S, 100.0, "101", "99", "100.5", "2", "201"],
        [T0_S, "100", "101", "99", "100.5", "2"],
        "not a row",
    ],
)
def test_invalid_rest_rows_are_refused(row: object) -> None:
    assert rest_kline(row) is None


def kline(o: str, h: str, published: int, opened: int = T0_S) -> dict[str, object]:
    return {
        "T": "kline.SPOT",
        "P": published,
        "d": {
            "s": "BTC-USDT",
            "i": "1min",
            "O": opened,
            "C": opened + 60,
            "o": o,
            "h": h,
            "l": "99",
            "c": "100.5",
            "v": "2",
            "a": "201",
        },
    }


def test_the_last_published_websocket_candle_of_each_minute_counts() -> None:
    book = WsCandles()
    assert book.add(kline("100", "100.5", published=2))
    assert book.add(kline("100", "101", published=5))
    assert book.add(kline("100", "100.7", published=3))  # older: does not replace
    assert not book.add(kline("100", "98", published=6))  # high below low: invalid
    latest = book.pop(T0)
    assert latest is not None and latest.high == Decimal(101)
    assert book.pop(T0) is None


def event_flow() -> EventFlow:
    return EventFlow("SPOT", "BTC-USDT", "trade", started_at=at(0))


def test_event_flow_states() -> None:
    tracker = event_flow()
    tracker.connection_up(at(0))
    tracker.message(at(5), valid=True)
    tracker.connection_down(at(70))
    tracker.connection_up(at(75))
    tracker.connection_down(at(130))
    tracker.message(at(185), valid=False)
    tracker.connection_up(at(245))
    minutes = tracker.close_until(at(300))
    states = [m.flow_state.value for m in minutes]
    assert states == ["S1", "S2", "S2", "S3", "S2"]
    assert minutes[0].messages == 1 and minutes[1].reconnections == 1
    assert minutes[3].anomalies == 1


def test_silence_on_a_healthy_connection_is_s1() -> None:
    tracker = event_flow()
    tracker.connection_up(at(0))
    (minute,) = tracker.close_until(at(60))
    assert minute.flow_state is FlowState.S1 and minute.messages == 0


def test_a_malformed_message_degrades_the_minute() -> None:
    tracker = event_flow()
    tracker.connection_up(at(0))
    tracker.message(at(10), valid=False)
    (minute,) = tracker.close_until(at(60))
    assert minute.flow_state is FlowState.S2


def full_day(state: FlowState) -> dict[datetime, FlowState]:
    return {DAY + timedelta(minutes=i): state for i in range(1440)}


def test_day_close_maps_every_minute_as_in_section_3_5() -> None:
    flows = full_day(FlowState.S1)
    m = [DAY + timedelta(minutes=i) for i in range(6)]
    flows[m[3]] = FlowState.S2
    flows[m[4]] = FlowState.S2
    decisions = {
        m[0]: decide("SPOT", "X", flow(FlowState.S1, m[0]), _at(m[0]), _at(m[0]), TICK),
        m[1]: decide("SPOT", "X", flow(FlowState.S1, m[1]), None, None, TICK),
        m[2]: decide("SPOT", "X", flow(FlowState.S1, m[2]), None, _at(m[2]), TICK),
        m[3]: decide("SPOT", "X", flow(FlowState.S2, m[3]), None, None, TICK),
        m[4]: decide("SPOT", "X", flow(FlowState.S2, m[4]), None, None, TICK),
    }
    gaps = [(m[3] + timedelta(seconds=20), m[3] + timedelta(seconds=50))]
    states = {minute: decision.state for minute, decision in decisions.items()}
    statuses = day_close(DAY, flows, states, gaps, market="SPOT", symbol="X")
    assert len(statuses) == 1440
    first = [(s.status.value, s.candle_state.value, s.gap_start) for s in statuses[:6]]
    assert first == [
        ("datos", "cerrada_cruzada", None),
        ("sin_operaciones_probable", "ausencia_sin_trades_probable", None),
        ("sin_explicar", "pendiente_estabilizacion", None),
        ("hueco", "ausencia_no_verificable", gaps[0][0]),
        ("sin_explicar", "ausencia_no_verificable", None),  # degraded, but no gap recorded
        ("sin_explicar", "abierta", None),  # never decided
    ]
    assert statuses[0].candle_series == "last" and statuses[1].candle_series is None


def test_day_close_needs_every_flow_and_a_midnight() -> None:
    flows = full_day(FlowState.S1)
    del flows[DAY + timedelta(minutes=700)]
    with pytest.raises(ValueError, match="no flow state"):
        day_close(DAY, flows, {}, [], market="SPOT", symbol="X")
    with pytest.raises(ValueError, match="midnight"):
        day_close(
            DAY + timedelta(hours=1), full_day(FlowState.S1), {}, [], market="SPOT", symbol="X"
        )


def _at(minute: datetime) -> Ohlcv:
    return replace(candle(), open_time=minute)


@pytest.mark.parametrize(
    ("flow_state", "klines", "expected"),
    [
        (FlowState.S1, 0, CandleState.AUSENCIA_SIN_TRADES),
        (FlowState.S1, 3, CandleState.PENDIENTE),
        (FlowState.S2, 0, CandleState.AUSENCIA_NO_VERIFICABLE),
        (FlowState.S3, 5, CandleState.AUSENCIA_NO_VERIFICABLE),
    ],
)
def test_a_minute_without_candle_is_rebuilt_from_what_was_recorded(
    flow_state: FlowState, klines: int, expected: CandleState
) -> None:
    assert state_without_candle(flow_state, klines) is expected


def flat(price: str = "100.5") -> Ohlcv:
    value = Decimal(price)
    return Ohlcv(T0, value, value, value, value, Decimal(0), Decimal(0))


def test_a_zero_volume_spot_candle_agrees_with_a_flow_without_trades() -> None:
    """Spot: KuCoin publishes a flat zero-volume candle for a minute without trades
    (measured on 8 November 2026); Futures publishes nothing."""
    quiet = decide("SPOT", "BNB-USDT", flow(FlowState.S1), flat(), None, TICK)
    assert quiet.state is CandleState.CERRADA_CRUZADA
    assert quiet.evidence is CandleEvidence.CRUZADA_CONSISTENTE and quiet.ws is None
    traded = decide(
        "SPOT",
        "BNB-USDT",
        flow(FlowState.S1),
        flat(),
        candle(o="100.5", h="100.5", low="100.5", c="100.5"),
        TICK,
    )
    assert traded.state is CandleState.DISCREPANTE
    assert traded.differences == ("volumen_rest_cero",)
    degraded = decide("SPOT", "BNB-USDT", flow(FlowState.S2), flat(), None, TICK)
    assert degraded.state is CandleState.CERRADA_REST


def test_a_trade_without_a_websocket_candle_still_counts_as_trading() -> None:
    """Review of E2, third round (I-E2-02): the trade channel received a trade but no candle
    arrived on the kline channel. A REST answer without the candle is not a probable
    absence of trades, and a REST candle of volume 0 contradicts the trade, also after a
    restart, when the day is closed from the database."""
    traded = flow(FlowState.S1, trades=1)
    assert decide("SPOT", "BTC-USDT", traded, None, None, TICK).state is CandleState.PENDIENTE
    zero = decide("SPOT", "BTC-USDT", traded, flat(), None, TICK)
    assert zero.state is CandleState.DISCREPANTE
    assert zero.differences == ("websocket_sin_vela", "volumen_rest_cero")
    flat_ws = decide("SPOT", "BTC-USDT", traded, flat(), flat(), TICK)
    assert flat_ws.state is CandleState.DISCREPANTE
    assert flat_ws.differences == ("volumen_rest_cero",)
    assert state_without_candle(FlowState.S1, 0, trade_messages=1) is CandleState.PENDIENTE
    assert state_without_candle(FlowState.S1, 0, 0) is CandleState.AUSENCIA_SIN_TRADES
    assert rest_kline([T0_S, "130.678", "130.678", "130.678", "130.678", "0", "0"]) == Ohlcv(
        T0, *(Decimal(v) for v in ("130.678",) * 4), Decimal(0), Decimal(0)
    )


def test_what_late_evidence_changes() -> None:
    """Reviews of E2, fifth and sixth rounds: the rules applied, against what the database
    holds, to a trade or a candle that arrives after its minute closed
    (store.write_late_minutes and MemorySink use them both)."""
    quiet, traded, degraded = flow(FlowState.S1), flow(FlowState.S1, trades=1), flow(FlowState.S2)
    assert trade_changes(quiet, active=False)
    assert not trade_changes(quiet, active=True), "trading already seen, in any candle"
    assert not trade_changes(traded, active=False), "a trade already arrived in time"
    assert not trade_changes(degraded, active=False), "on S2 the WebSocket decides nothing"

    assert candle_kind(quiet, None, None, candle(), at(70)) == "candle", "the first one"
    assert candle_kind(quiet, candle(), at(30), candle(c="100.9"), at(70)) == "candle"
    assert candle_kind(quiet, candle(), at(30), candle(), at(70)) == "repeat", "sent again"
    assert candle_kind(quiet, candle(), at(30), candle(c="100.9"), at(20)) is None, "older"
    # R6-E2-02: the same candle was seen again at 80 s, so one published at 70 s is older.
    assert candle_kind(quiet, candle(), at(80), candle(c="100.9"), at(70)) is None
    assert candle_kind(degraded, None, None, candle(), at(70)) is None
