"""The recorder's core, driven through simulated time (no network, no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from kbot.ingest.core import MAX_CANDLE_ATTEMPTS, InstrumentSpec, RecorderCore
from kbot.market.depth import SlotState
from kbot.market.gaps import GapCause
from kbot.market.last_candles import CandleState, rest_kline
from kbot.market.mark_candles import Evidence, FlowState
from tests.unit.ingest.messages import book, kline, mark, rest_row, trade

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)

SPOT = InstrumentSpec("SPOT", "BTC-USDT", Decimal("0.1"), Decimal(1), l50=True)
SPOT_SMALL = InstrumentSpec("SPOT", "ETH-USDT", Decimal("0.01"), Decimal(1), l50=False)
FUT = InstrumentSpec(
    "FUTURES", "XBTUSDTM", Decimal("0.1"), Decimal("0.001"), l50=True, index_symbol=".KXBTUSDT"
)


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def core(*specs: InstrumentSpec, **kwargs: object) -> RecorderCore:
    return RecorderCore(specs or (SPOT, SPOT_SMALL, FUT), started_at=at(0), **kwargs)  # type: ignore[arg-type]


def connect(recorder: RecorderCore, moment: datetime) -> None:
    for market in ("SPOT", "FUTURES"):
        recorder.on_state(market, "connected", moment, "wss://...")


def feed_minute(recorder: RecorderCore, minute: datetime, *, with_marks: bool = True) -> None:
    """Books, trades and a candle for every instrument, and 60 mark values for XBTUSDTM."""
    for second in range(0, 60, 10):
        moment = minute + timedelta(seconds=second + 1)
        seq = int(moment.timestamp())
        recorder.on_message("SPOT", book("SPOT", "BTC-USDT", seq, moment), moment)
        recorder.on_message("SPOT", book("SPOT", "BTC-USDT", seq, moment, "50"), moment)
        recorder.on_message("SPOT", book("SPOT", "ETH-USDT", seq, moment), moment)
        recorder.on_message("FUTURES", book("FUTURES", "XBTUSDTM", seq, moment), moment)
        recorder.on_message("FUTURES", book("FUTURES", "XBTUSDTM", seq, moment, "50"), moment)
    trade_time = minute + timedelta(seconds=30)
    for market, symbol in (("SPOT", "BTC-USDT"), ("SPOT", "ETH-USDT"), ("FUTURES", "XBTUSDTM")):
        recorder.on_message(market, trade(market, symbol, trade_time), trade_time)
        recorder.on_message(market, kline(market, symbol, minute, trade_time), trade_time)
    if with_marks:
        for second in range(60):
            moment = minute + timedelta(seconds=second)
            recorder.on_message("FUTURES", mark("XBTUSDTM", moment), moment)


def test_subscriptions_follow_the_universe() -> None:
    recorder = core()
    spot = [(s.channel, s.symbol, s.options) for s in recorder.subscriptions("SPOT")]
    assert spot == [
        ("obu", "BTC-USDT", (("depth", "5"),)),
        ("obu", "BTC-USDT", (("depth", "50"),)),
        ("trade", "BTC-USDT", ()),
        ("kline", "BTC-USDT", (("interval", "1min"),)),
        ("obu", "ETH-USDT", (("depth", "5"),)),
        ("trade", "ETH-USDT", ()),
        ("kline", "ETH-USDT", (("interval", "1min"),)),
    ]
    futures = [s.channel for s in recorder.subscriptions("FUTURES")]
    assert futures == ["obu", "obu", "trade", "kline", "mark-price"]
    # 20 + 20 instruments with 10 + 10 candidates stay under 100 topics per connection.
    assert 20 * 3 + 10 <= 100 and 20 * 4 + 10 <= 100


def test_a_whole_minute_is_closed_and_written_in_order() -> None:
    recorder = core()
    connect(recorder, at(0))
    feed_minute(recorder, at(0))
    out = recorder.tick(at(62))
    assert {(s.symbol, s.depth) for s in out.slots} == {
        ("BTC-USDT", 5),
        ("BTC-USDT", 50),
        ("ETH-USDT", 5),
        ("XBTUSDTM", 5),
        ("XBTUSDTM", 50),
    }
    assert all(s.state is SlotState.VALIDA for s in out.slots)
    assert len(out.slots) == 5 * 6 and len(out.minute_snapshots) == 5
    flows = {(f.symbol, f.channel): f.flow_state for f in out.flows}
    assert flows == {
        ("BTC-USDT", "trade"): FlowState.S1,
        ("BTC-USDT", "kline"): FlowState.S1,
        ("ETH-USDT", "trade"): FlowState.S1,
        ("ETH-USDT", "kline"): FlowState.S1,
        ("XBTUSDTM", "trade"): FlowState.S1,
        ("XBTUSDTM", "kline"): FlowState.S1,
        ("XBTUSDTM", "mark-price"): FlowState.S1,
    }
    assert [(c.series, c.evidence) for c in out.own_candles] == [
        ("mark", Evidence.FLOW),
        ("index", Evidence.FLOW),
    ]
    assert out.index_rebuilds == [] and recorder.anomalies == 0
    # The REST candles are asked for 15 s after the minute ends, not before.
    assert recorder.candle_requests(at(74)) == []
    requests = recorder.candle_requests(at(75))
    assert sorted(r.symbol for r in requests) == ["BTC-USDT", "ETH-USDT", "XBTUSDTM"]
    decisions = []
    for request in requests:
        rows = [rest_kline(rest_row(at(0)))]
        decisions += recorder.apply_rest_candles(request, rows, at(75))  # type: ignore[arg-type]
    assert {d.state for d in decisions} == {CandleState.CERRADA_CRUZADA}
    assert recorder.candle_requests(at(200)) == [], "decided minutes are not asked again"


def test_a_disconnection_gives_gaps_desconectado_slots_and_an_index_rebuild() -> None:
    recorder = core(FUT)
    connect(recorder, at(0))
    feed_minute(recorder, at(0))
    recorder.on_state("FUTURES", "disconnected", at(30), "ProtocolViolation: no pong in time")
    recorder.on_state("FUTURES", "connected", at(35), "wss://...")
    out = recorder.tick(at(62))
    states = [s.state for s in out.slots if s.depth == 5]
    assert states[3] is SlotState.DESCONECTADO and states[0] is SlotState.VALIDA
    assert {(g.channel, g.cause) for g in out.gaps} == {
        (c, GapCause.SIN_PONG) for c in ("obu5", "obu50", "trade", "kline", "mark-price")
    }
    assert all((g.start_at, g.end_at) == (at(30), at(35)) for g in out.gaps)
    assert out.own_candles == [], "the mark minute is a gap"
    assert [(r.symbol, r.index_symbol, r.minute) for r in out.index_rebuilds] == [
        ("XBTUSDTM", ".KXBTUSDT", at(0))
    ]
    assert {f.flow_state for f in out.flows} == {FlowState.S2}


def test_messages_of_unknown_instruments_or_topics_are_anomalies() -> None:
    recorder = core(SPOT)
    connect(recorder, at(0))
    recorder.on_message("SPOT", trade("SPOT", "DOGE-USDT", at(1)), at(1))
    recorder.on_message("SPOT", {"T": "level2", "d": {"s": "BTC-USDT"}}, at(1))
    recorder.on_message("SPOT", {"T": "obu.SPOT", "dp": "400", "d": {"s": "BTC-USDT"}}, at(1))
    recorder.on_message("SPOT", {"T": "trade.SPOT", "d": "nonsense"}, at(1))
    assert recorder.anomalies == 4


def test_a_malformed_trade_degrades_its_minute() -> None:
    recorder = core(SPOT)
    connect(recorder, at(0))
    bad = trade("SPOT", "BTC-USDT", at(5))
    bad["d"]["p"] = "NaN"
    recorder.on_message("SPOT", bad, at(5))
    out = recorder.tick(at(62))
    trade_flow = next(f for f in out.flows if f.channel == "trade")
    assert trade_flow.flow_state is FlowState.S2 and trade_flow.anomalies == 1


def test_failed_rest_requests_are_retried_a_limited_number_of_times() -> None:
    recorder = core(SPOT)
    connect(recorder, at(0))
    feed_minute(recorder, at(0))
    recorder.tick(at(62))
    moment = at(75)
    for attempt in range(MAX_CANDLE_ATTEMPTS):
        (request,) = recorder.candle_requests(moment)
        assert recorder.apply_rest_candles(request, None, moment) == []
        assert recorder.candle_requests(moment) == [], "not again within the same minute"
        moment += timedelta(minutes=1)
        if attempt < MAX_CANDLE_ATTEMPTS - 1:
            assert recorder.awaiting_until(at(3600)) == 1
    assert recorder.awaiting_until(at(3600)) == 0, "given up: the day close says why"


def test_a_minute_seen_on_the_websocket_waits_for_its_rest_candle() -> None:
    recorder = core(SPOT)
    connect(recorder, at(0))
    feed_minute(recorder, at(0))
    recorder.tick(at(62))
    (request,) = recorder.candle_requests(at(75))
    assert recorder.apply_rest_candles(request, [], at(75)) == []  # pendiente: ask again
    (again,) = recorder.candle_requests(at(135))
    rows = [rest_kline(rest_row(at(0)))]
    (decision,) = recorder.apply_rest_candles(again, rows, at(135))  # type: ignore[arg-type]
    assert decision.state is CandleState.CERRADA_CRUZADA


def test_a_restart_resumes_where_the_database_says() -> None:
    stopped = at(-300)
    resume = {
        (FUT.market, FUT.symbol, channel): stopped
        for channel in ("obu5", "obu50", "trade", "kline", "mark-price")
    }
    recorder = core(FUT, resume=resume)
    connect(recorder, at(2))
    out = recorder.tick(at(62))
    gaps = {(g.channel, g.start_at, g.end_at, g.cause) for g in out.gaps}
    assert (("trade", stopped, at(2), GapCause.PROCESO_DETENIDO)) in gaps
    slot_states = [s.state for s in out.slots if s.depth == 5]
    assert slot_states[:31] == [SlotState.DESCONECTADO] * 31  # from -300 s to +10 s
    trade_flows = [f for f in out.flows if f.channel == "trade"]
    assert [f.minute for f in trade_flows] == [at(-300 + 60 * i) for i in range(6)]
    assert {f.flow_state for f in trade_flows[:5]} == {FlowState.S3}
    # The REST candles of the stop are asked for too: recoverable history (02 §3.7).
    requests = recorder.candle_requests(at(75))
    assert [(r.first, r.last) for r in requests] == [(at(-300), at(0))]
    assert len(out.index_rebuilds) == 6, "every index minute of the stop is rebuilt"


def test_old_resume_points_are_limited_but_the_gap_covers_the_whole_stop() -> None:
    stopped = at(-3 * 86400)
    resume = {(SPOT.market, SPOT.symbol, c): stopped for c in ("obu5", "obu50", "trade", "kline")}
    recorder = core(SPOT, resume=resume, max_backfill=timedelta(hours=1))
    connect(recorder, at(0))
    out = recorder.tick(at(10))
    trade_gap = next(g for g in out.gaps if g.channel == "trade")
    assert trade_gap.start_at == stopped
    assert min(s.slot_start for s in out.slots) == at(-3600)


def test_the_universe_changes_at_midnight() -> None:
    midnight = datetime(2026, 11, 8, tzinfo=UTC)
    recorder = RecorderCore((SPOT, SPOT_SMALL), started_at=midnight - timedelta(minutes=2))
    for market in ("SPOT", "FUTURES"):
        recorder.on_state(market, "connected", midnight - timedelta(minutes=2), "wss://...")
    feed_minute(recorder, midnight - timedelta(minutes=1), with_marks=False)
    recorder.tick(midnight - timedelta(seconds=30))

    changed = recorder.apply_universe([SPOT, FUT], midnight)  # ETH leaves, XBT enters
    assert changed
    assert [s.symbol for s in recorder.specs] == ["BTC-USDT", "XBTUSDTM"]
    assert recorder.apply_universe([SPOT, FUT], midnight) is False, "nothing changes twice"

    out = recorder.tick(midnight + timedelta(minutes=5))
    eth_slots = [s for s in out.slots if s.symbol == "ETH-USDT"]
    assert eth_slots and max(s.slot_start for s in eth_slots) < midnight, "ETH stops at midnight"
    xbt_slots = [s for s in out.slots if s.symbol == "XBTUSDTM"]
    assert min(s.slot_start for s in xbt_slots) == midnight, "XBT starts at midnight"
    assert all(s.state is SlotState.DESCONECTADO for s in xbt_slots), "not connected yet"
    xbt_gap = [g for g in out.gaps if g.symbol == "XBTUSDTM"]
    assert xbt_gap == [], "its first-connection gap is still open"
    # The last minute of ETH before midnight still gets its REST candle.
    requests = [
        r
        for r in recorder.candle_requests(midnight + timedelta(minutes=5))
        if r.symbol == "ETH-USDT"
    ]
    assert [r.last for r in requests] == [midnight - timedelta(minutes=1)]
    rows = [rest_kline(rest_row(midnight - timedelta(minutes=1)))]
    decisions = recorder.apply_rest_candles(requests[0], rows, midnight + timedelta(minutes=5))  # type: ignore[arg-type]
    assert {d.symbol for d in decisions} == {"ETH-USDT"}
    assert [d.state for d in decisions] == [
        CandleState.AUSENCIA_SIN_TRADES,
        CandleState.CERRADA_CRUZADA,
    ]
    recorder.tick(midnight + timedelta(minutes=6))
    assert recorder.awaiting_until(midnight) == 2, "only the two minutes of BTC still wait"


def test_a_spec_change_retires_and_adds_the_instrument() -> None:
    midnight = datetime(2026, 11, 8, tzinfo=UTC)
    recorder = RecorderCore((SPOT_SMALL,), started_at=midnight - timedelta(minutes=1))
    promoted = InstrumentSpec("SPOT", "ETH-USDT", Decimal("0.01"), Decimal(1), l50=True)
    assert recorder.apply_universe([promoted], midnight)
    assert [s.channel for s in recorder.subscriptions("SPOT")].count("obu") == 2


def test_times_must_carry_a_zone() -> None:
    with pytest.raises(ValueError, match="time zone"):
        RecorderCore([SPOT], started_at=datetime(2026, 11, 7))
    with pytest.raises(ValueError, match="twice"):
        RecorderCore([SPOT, SPOT], started_at=T0)
