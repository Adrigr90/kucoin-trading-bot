"""The recorder's core, driven through simulated time (no network, no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from kbot.ingest.core import MAX_CANDLE_ATTEMPTS, InstrumentSpec, RecorderCore, TickOutput
from kbot.ingest.sinks import MemorySink
from kbot.market.depth import SlotState, effective_state
from kbot.market.gaps import GapCause
from kbot.market.last_candles import (
    CandleDecision,
    CandleState,
    FlowMinute,
    LateTrade,
    LateWsCandle,
    PendingCandle,
    rest_kline,
    ws_kline,
)
from kbot.market.mark_candles import Evidence, FlowState
from kbot.market.reference import InstrumentRow
from tests.unit.ingest.messages import book, kline, mark, pong, rest_row, seconds, trade

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


def tick(recorder: RecorderCore, moment: datetime) -> TickOutput:
    """A tick right after a pong on both connections: everything up to ``moment`` proven."""
    pong(recorder, moment)
    return recorder.tick(moment)


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
    out = tick(recorder, at(62))
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
    out = tick(recorder, at(62))
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


def test_nothing_is_closed_as_healthy_without_a_later_proof_of_life() -> None:
    """Review of E2, third round (I-E2-03): the last message arrives at 1 s and the stream
    notices the dead connection only at 24 s, dating the outage from that last proof of
    life. Until then nothing after the proof is closed, so the outage never contradicts a
    slot already written as valid."""
    recorder = core(SPOT)
    connect(recorder, at(0))
    recorder.on_message("SPOT", book("SPOT", "BTC-USDT", 1, at(1)), at(1))
    assert recorder.tick(at(12)).slots == [], "no proof of life after the slot's deadline"
    recorder.on_state("SPOT", "disconnected", at(1), "ProtocolViolation: no pong in time")
    out = recorder.tick(at(24))
    assert [(s.slot_start, s.state) for s in out.slots if s.depth == 5] == [
        (at(0), SlotState.DESCONECTADO),
        (at(10), SlotState.DESCONECTADO),
    ]
    assert all(s.metrics is None for s in out.slots)

    healthy = core(SPOT)
    connect(healthy, at(0))
    healthy.on_message("SPOT", book("SPOT", "BTC-USDT", 1, at(1)), at(1))
    healthy.on_alive("SPOT", at(12))  # a pong: the connection was alive after the deadline
    assert [s.state for s in healthy.tick(at(12)).slots if s.depth == 5] == [SlotState.VALIDA]


@pytest.mark.parametrize("earlier_book", [False, True])
def test_a_late_book_is_seen_before_its_slot_closes(earlier_book: bool) -> None:
    """Review of E2, third round (I-E2-04): with a 2 s margin the slot [0, 10) cannot close at
    12 s without a proof of life; the book published at 9.5 s arrives at 13 s and the slot
    closes fuera_de_plazo, with or without an earlier book."""
    recorder = core(SPOT)
    connect(recorder, at(0))
    if earlier_book:
        recorder.on_message("SPOT", book("SPOT", "BTC-USDT", 1, at(1)), at(1))
    assert recorder.tick(at(12)).slots == []
    recorder.on_message("SPOT", book("SPOT", "BTC-USDT", 2, at(9.5)), at(13))
    (first,) = [s for s in recorder.tick(at(13)).slots if s.depth == 5]
    assert first.state is SlotState.FUERA_DE_PLAZO
    assert first.sequence == (1 if earlier_book else None)


def test_a_book_for_a_slot_already_closed_is_an_anomaly() -> None:
    """If a book still comes for a closed slot (the connection broke its order), it is never
    a valid book for the following slots: they are libro_inconsistente until a new one."""
    recorder = core(SPOT)
    connect(recorder, at(0))
    recorder.on_message("SPOT", book("SPOT", "BTC-USDT", 1, at(1)), at(1))
    assert [s.state for s in tick(recorder, at(12)).slots if s.depth == 5] == [SlotState.VALIDA]
    recorder.on_message("SPOT", book("SPOT", "BTC-USDT", 2, at(9.5)), at(13))
    assert [s.state for s in tick(recorder, at(22)).slots if s.depth == 5] == [
        SlotState.LIBRO_INCONSISTENTE
    ]
    recorder.on_message("SPOT", book("SPOT", "BTC-USDT", 3, at(25)), at(25))
    assert [s.state for s in tick(recorder, at(32)).slots if s.depth == 5] == [SlotState.VALIDA]


def test_a_late_book_leaves_a_lasting_record_even_if_a_correct_one_follows() -> None:
    """Review of E2, fourth round (I-E2-04), the reviewer's sequence: a pong at 12 s closes
    [0, 10) as valid, a book published at 9.5 s arrives at 13 s and a correct one at 14 s,
    before the next close. [10, 20) is valid with the correct book, and the late book goes
    to the database with the next tick: [0, 10) never counts as valid again."""
    recorder = core(SPOT)
    connect(recorder, at(0))
    recorder.on_message("SPOT", book("SPOT", "BTC-USDT", 1, at(1)), at(1))
    (first,) = [s for s in tick(recorder, at(12)).slots if s.depth == 5]
    assert first.state is SlotState.VALIDA
    recorder.on_message("SPOT", book("SPOT", "BTC-USDT", 2, at(9.5)), at(13))
    recorder.on_message("SPOT", book("SPOT", "BTC-USDT", 3, at(14)), at(14))
    out = tick(recorder, at(22))
    assert [(s.state, s.sequence) for s in out.slots if s.depth == 5] == [(SlotState.VALIDA, 3)]
    (late,) = out.late_books
    assert (late.depth, late.slot_start, late.last_slot_start, late.sequence) == (
        5,
        at(0),
        at(0),
        2,
    )
    assert effective_state(first.state, True) is SlotState.FUERA_DE_PLAZO
    assert tick(recorder, at(32)).late_books == [], "stored once"


def zero_kline(minute: datetime, received: datetime) -> dict[str, object]:
    """A flat WebSocket candle of volume 0 (the parser accepts it; not seen from KuCoin)."""
    message = kline("SPOT", "BTC-USDT", minute, received)
    message["d"].update(o="100", h="100", l="100", c="100", v="0", a="0")
    return message


def kline_closing(minute: datetime, published: datetime, close: str) -> dict[str, object]:
    message = kline("SPOT", "BTC-USDT", minute, published)
    message["d"]["c"] = close
    return message


ZERO_REST = rest_kline([seconds(at(0)), "100", "100", "100", "100", "0", "0"])


def written(recorder: RecorderCore, sink: MemorySink, *moments: float) -> TickOutput:
    """Tick at each moment and write the output, as the service does: what the database
    keeps of the late evidence is handed back to the core. The last output."""
    out = TickOutput()
    for moment in moments:
        out = tick(recorder, at(moment))
        recorder.late_kept(sink.write_tick(out))
    return out


def restarted(sink: MemorySink, moment: float) -> RecorderCore:
    """A new core started at ``moment`` that resumes, from what ``sink`` holds, the minutes
    still to decide; the minutes before ``moment`` are closed for it."""
    reference = InstrumentRow(
        "SPOT", "BTC-USDT", "BTC", "USDT", None, SPOT.tick_size, Decimal("0.0001"),
        Decimal(1), None, None, "Trading", True, {},
    )  # fmt: skip
    sink.write_instruments([reference], at(0))  # the start-up stores it before anything else
    recorder = RecorderCore((SPOT,), started_at=at(moment))
    recorder.restore_awaiting(sink.pending_candles(at(-3600)))
    connect(recorder, at(moment))
    return recorder


def decide_now(recorder: RecorderCore, moment: float, rows: list[object]) -> CandleDecision:
    (request,) = recorder.candle_requests(at(moment))
    (decision,) = recorder.apply_rest_candles(request, rows, at(moment))  # type: ignore[arg-type]
    return decision


def test_a_late_trade_goes_to_the_database_and_then_decides_its_minute() -> None:
    """Review of E2, fourth round: [0, 60) closes as S1 with neither a trade nor a candle,
    and a trade executed at 59.5 s arrives at 63 s. It goes to the database with the next
    tick and, once kept there, the minute is decided with it: without a REST candle it
    waits instead of being a probable absence of trades, and a zero-volume REST candle
    contradicts it."""
    recorder, sink = core(SPOT), MemorySink()
    connect(recorder, at(0))
    out = written(recorder, sink, 62)
    assert [(f.channel, f.flow_state, f.messages) for f in out.flows] == [
        ("kline", FlowState.S1, 0),
        ("trade", FlowState.S1, 0),
    ]
    recorder.on_message("SPOT", trade("SPOT", "BTC-USDT", at(59.5)), at(63))
    late = LateTrade("SPOT", "BTC-USDT", at(0), at(63))
    assert written(recorder, sink, 64).late_minutes == [late]
    assert sink.late_trades == [late]
    (request,) = recorder.candle_requests(at(75))
    assert recorder.apply_rest_candles(request, [], at(75)) == [], "pending, not an absence"
    decision = decide_now(recorder, 135, [ZERO_REST])
    assert decision.state is CandleState.DISCREPANTE
    assert decision.differences == ("websocket_sin_vela", "volumen_rest_cero")


def test_a_late_trade_contradicts_a_websocket_candle_of_volume_zero() -> None:
    """Review of E2, fifth round (R5-E2-02): the minute had a WebSocket candle of volume 0,
    which is no trading. A trade of it that arrives late contradicts a zero-volume REST
    candle all the same."""
    recorder, sink = core(SPOT), MemorySink()
    connect(recorder, at(0))
    recorder.on_message("SPOT", zero_kline(at(0), at(30)), at(30))
    written(recorder, sink, 62)
    recorder.on_message("SPOT", trade("SPOT", "BTC-USDT", at(59.5)), at(63))
    written(recorder, sink, 64)
    decision = decide_now(recorder, 75, [ZERO_REST])
    assert decision.state is CandleState.DISCREPANTE
    assert decision.differences == ("volumen_rest_cero",)

    without, sink = core(SPOT), MemorySink()  # without the late trade: both saw no trading
    connect(without, at(0))
    without.on_message("SPOT", zero_kline(at(0), at(30)), at(30))
    written(without, sink, 62)
    assert decide_now(without, 75, [ZERO_REST]).state is CandleState.CERRADA_CRUZADA


def test_a_late_candle_that_changes_the_minute_decides_it_once_kept() -> None:
    """A WebSocket candle of [0, 60) published and received at 70 s, after the minute
    closed, with another close: once kept, the minute is decided with it, the latest one
    KuCoin published."""
    recorder, sink = core(SPOT), MemorySink()
    connect(recorder, at(0))
    recorder.on_message("SPOT", kline("SPOT", "BTC-USDT", at(0), at(30)), at(30))
    written(recorder, sink, 62)
    recorder.on_message("SPOT", kline_closing(at(0), at(70), "100.9"), at(70))
    (late,) = written(recorder, sink, 71).late_minutes
    assert isinstance(late, LateWsCandle) and sink.late_ws_candles == [late]
    decision = decide_now(recorder, 75, [rest_kline(rest_row(at(0)))])  # close 100.5
    assert decision.state is CandleState.DISCREPANTE and decision.differences == ("close",)
    assert decision.ws is not None and decision.ws.close == Decimal("100.9")


def test_a_late_candle_is_used_only_once_the_database_holds_it() -> None:
    """Review of E2, sixth round (R6-E2-01): the stored WebSocket candle closes at 100.1;
    another one, closing at 100.5 like the REST candle, arrives while the REST answer is
    awaited. The decision does not use it: the database does not hold it yet, so a stop
    right after the decision would leave a decision that the database cannot explain. It is
    kept with the next tick."""
    recorder, sink = core(SPOT), MemorySink()
    connect(recorder, at(0))
    recorder.on_message("SPOT", kline_closing(at(0), at(30), "100.1"), at(30))
    written(recorder, sink, 62)
    (request,) = recorder.candle_requests(at(75))
    recorder.on_message("SPOT", kline_closing(at(0), at(75), "100.5"), at(75))  # meanwhile
    rows = [rest_kline(rest_row(at(0)))]  # close 100.5
    (decision,) = recorder.apply_rest_candles(request, rows, at(75))  # type: ignore[arg-type]
    assert decision.ws is not None and decision.ws.close == Decimal("100.1"), "the stored one"
    assert decision.state is CandleState.DISCREPANTE
    written(recorder, sink, 76)
    assert [c.candle.close for c in sink.late_ws_candles] == [Decimal("100.5")]


@pytest.mark.parametrize("restart", [False, True])
def test_an_older_candle_never_replaces_one_sent_again(restart: bool) -> None:
    """Review of E2, sixth round (R6-E2-02): candle A is stored with P = 30 s and sent again,
    the same, with P = 80 s; then candle B, published at 70 s, arrives. B is older than the
    latest time A was seen, so it never becomes the latest candle, with or without a
    restart in between: only the time of A sent again is kept (not as a candle), and the
    minute is decided with A."""
    recorder, sink = core(SPOT), MemorySink()
    connect(recorder, at(0))
    recorder.on_message("SPOT", kline("SPOT", "BTC-USDT", at(0), at(30)), at(30))
    written(recorder, sink, 62)
    recorder.on_message("SPOT", kline("SPOT", "BTC-USDT", at(0), at(80)), at(80))
    written(recorder, sink, 81)
    if restart:
        recorder = restarted(sink, 81)
    recorder.on_message("SPOT", kline_closing(at(0), at(70), "100.9"), at(82))
    written(recorder, sink, 83)
    assert sink.late_ws_candles == [], "B is older: not the latest"
    assert [r.published_at for r in sink.late_ws_repeats] == [at(80)]
    decision = decide_now(recorder, 84, [rest_kline(rest_row(at(0)))])
    assert decision.state is CandleState.CERRADA_CRUZADA and decision.ws is not None
    assert decision.ws.close == Decimal("100.5")


@pytest.mark.parametrize("restart", [False, True])
def test_trading_once_seen_is_never_forgotten(restart: bool) -> None:
    """Review of E2, sixth round (R6-E2-03): a minute with a WebSocket candle of volume 0
    gets a late candle with volume; a late trade then changes nothing and is not kept; a
    later candle of volume 0 becomes the latest. The WebSocket saw trading all the same:
    against a zero-volume REST candle the minute is discrepante, also after a restart."""
    recorder, sink = core(SPOT), MemorySink()
    connect(recorder, at(0))
    recorder.on_message("SPOT", zero_kline(at(0), at(30)), at(30))
    written(recorder, sink, 62)
    recorder.on_message("SPOT", kline("SPOT", "BTC-USDT", at(0), at(63)), at(63))  # volume 2
    recorder.on_message("SPOT", trade("SPOT", "BTC-USDT", at(59)), at(64))
    recorder.on_message("SPOT", zero_kline(at(0), at(65)), at(65))  # volume 0, latest
    written(recorder, sink, 66)
    assert sink.late_trades == [], "the candle with volume already showed trading"
    assert [c.published_at for c in sink.late_ws_candles] == [at(63), at(65)]
    if restart:
        recorder = restarted(sink, 67)
    decision = decide_now(recorder, 75, [ZERO_REST])
    assert decision.ws is not None and decision.ws.volume == 0, "the latest candle"
    assert decision.state is CandleState.DISCREPANTE
    assert decision.differences == ("volumen_rest_cero",)


def flat_kline(published: float, volume: str) -> dict[str, object]:
    """A candle of [0, 60) at a constant price of 100, published at ``published`` s."""
    message = zero_kline(at(0), at(published))
    message["d"].update(v=volume, a=str(Decimal(volume) * 100))
    return message


# Review of E2, seventh round (R6-E2-03): the three sequences in which the candle that shows
# trading is not the latest. (moment received, published, volume), and when the minute closes.
SEQUENCES = {
    "replaced before the close": [(30, 30, "1"), (50, 50, "0")],
    "older, before the close": [(50, 50, "0"), (55, 30, "1")],
    "older, after the close": [(50, 50, "0"), (63, 30, "1")],
}


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("sequence", sorted(SEQUENCES))
def test_trading_shown_by_an_older_or_replaced_candle_is_never_lost(
    sequence: str, restart: bool
) -> None:
    """Flow S1, no trade, a constant price of 100: a candle with volume 1 and one of volume
    0, published later. The latest candle, for comparing prices, is the one of volume 0; but
    the WebSocket saw trading all the same, whether the candle with volume was replaced
    before the close, arrived after the other one or after the close. Against a zero-volume
    REST candle the minute is discrepante, also after a restart (review of E2, seventh
    round)."""
    recorder, sink = core(SPOT), MemorySink()
    connect(recorder, at(0))
    for received, published, volume in SEQUENCES[sequence]:
        if received > 62:  # after the close of [0, 60)
            written(recorder, sink, 62)
        recorder.on_message("SPOT", flat_kline(published, volume), at(received))
    written(recorder, sink, 62, 64)
    if restart:
        recorder = restarted(sink, 65)
    decision = decide_now(recorder, 75, [ZERO_REST])
    assert decision.ws is not None and decision.ws.volume == 0, "the latest candle by P"
    assert decision.state is CandleState.DISCREPANTE
    assert decision.differences == ("volumen_rest_cero",)
    assert sink.late_ws_candles == [], "no late candle became the latest"
    trading_only = [c.published_at for c in sink.late_ws_trading]
    assert trading_only == ([at(30)] if sequence == "older, after the close" else [])


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("tie_with", ["the stored candle", "a candle sent again"])
def test_a_candle_with_the_same_p_never_competes_for_the_latest(
    tie_with: str, restart: bool
) -> None:
    """Review of E2, eighth round (N-E2-01): the latest candle has volume 0; another one,
    different and with volume, arrives late with the same P (that of the stored candle, or
    that of the candle sent again). It is not newer, so it never becomes the latest: it
    only shows trading. The latest stays the same, with or without a restart, and against
    a zero-volume REST candle the minute is discrepante."""
    recorder, sink = core(SPOT), MemorySink()
    connect(recorder, at(0))
    recorder.on_message("SPOT", flat_kline(50, "0"), at(50))
    written(recorder, sink, 62)
    tie = 50
    if tie_with == "a candle sent again":
        recorder.on_message("SPOT", flat_kline(80, "0"), at(80))  # the same candle, P = 80
        written(recorder, sink, 81)
        tie = 80
    other = flat_kline(tie, "1")
    other["d"]["c"] = "100"  # flat at 100 like the latest, but with volume: different
    recorder.on_message("SPOT", other, at(82))
    written(recorder, sink, 83)
    assert sink.late_ws_candles == [] and [c.published_at for c in sink.late_ws_trading] == [
        at(tie)
    ]
    if restart:
        recorder = restarted(sink, 84)
    decision = decide_now(recorder, 85, [ZERO_REST])
    assert decision.ws is not None and decision.ws.volume == 0, "the same latest candle"
    assert decision.state is CandleState.DISCREPANTE
    assert decision.differences == ("volumen_rest_cero",)


def test_late_evidence_is_kept_once_and_only_while_it_changes_the_minute() -> None:
    """Every late trade and candle goes to the database, which keeps it only while it
    changes what the minute can be decided as (here, the same rules in memory): two late
    trades of a minute decided as a probable absence are kept once; once a late candle with
    volume is kept, a late trade changes nothing more."""
    recorder, sink = core(SPOT), MemorySink()
    connect(recorder, at(0))
    written(recorder, sink, 62)
    decision = decide_now(recorder, 75, [])
    assert decision.state is CandleState.AUSENCIA_SIN_TRADES
    for moment in (80, 81):  # two trades of the minute, after it was decided
        recorder.on_message("SPOT", trade("SPOT", "BTC-USDT", at(59)), at(moment))
    assert len(written(recorder, sink, 82).late_minutes) == 2, "both go to the database"
    assert sink.late_trades == [LateTrade("SPOT", "BTC-USDT", at(0), at(80))], "kept once"

    recorder, sink = core(SPOT), MemorySink()
    connect(recorder, at(0))
    written(recorder, sink, 62)
    recorder.on_message("SPOT", kline("SPOT", "BTC-USDT", at(0), at(63)), at(63))
    recorder.on_message("SPOT", trade("SPOT", "BTC-USDT", at(59)), at(64))
    written(recorder, sink, 65)
    assert [c.received_at for c in sink.late_ws_candles] == [at(63)]
    assert sink.late_trades == [], "the late candle had volume: the trade changes nothing"


def test_a_closing_candle_sent_again_keeps_only_its_time() -> None:
    """KuCoin Futures sends the closing candle of a minute again with the first trade of the
    next one [H, E1 recording]. The same candle is not a late candle: only its time is kept
    (review of E2, sixth round). A late trade of a minute that already had trading changes
    nothing and is not kept (fifth round)."""
    recorder, sink = core(FUT), MemorySink()
    connect(recorder, at(0))
    recorder.on_message("FUTURES", trade("FUTURES", "XBTUSDTM", at(30)), at(30))
    recorder.on_message("FUTURES", kline("FUTURES", "XBTUSDTM", at(0), at(30)), at(30))
    written(recorder, sink, 62)
    again = kline("FUTURES", "XBTUSDTM", at(0), at(30))  # the same candle, sent at 88 s
    again["P"] = kline("FUTURES", "XBTUSDTM", at(0), at(88))["P"]
    recorder.on_message("FUTURES", again, at(88))
    recorder.on_message("FUTURES", trade("FUTURES", "XBTUSDTM", at(59)), at(88))
    out = written(recorder, sink, 89)
    assert len(out.late_minutes) == 2
    assert sink.late_ws_candles == [] and sink.late_trades == []
    assert [r.published_at for r in sink.late_ws_repeats] == [at(88)]


def test_a_trade_counts_in_the_minute_in_which_it_was_executed() -> None:
    """A trade executed at 59.5 s and received at 60.5 s, before its minute closes, counts in
    [0, 60), never in the minute in which it arrived (review of E2, fourth round); a
    malformed one, which has no valid match time, where it arrived."""
    recorder = core(SPOT)
    connect(recorder, at(0))
    recorder.on_message("SPOT", trade("SPOT", "BTC-USDT", at(59.5)), at(60.5))
    bad = trade("SPOT", "BTC-USDT", at(59.6))
    bad["d"]["M"] = -1
    recorder.on_message("SPOT", bad, at(60.6))
    flows = [f for f in tick(recorder, at(122)).flows if f.channel == "trade"]
    assert [(f.minute, f.messages, f.anomalies) for f in flows] == [(at(0), 1, 0), (at(60), 1, 1)]


def test_trading_seen_late_survives_a_restart() -> None:
    """The database says that the WebSocket saw trading in the minute: after a restart the
    minute is decided with it, never as a probable absence."""
    minute = at(-120)
    flow = FlowMinute("SPOT", "BTC-USDT", "trade", minute, FlowState.S1, 0, 0, 0)
    recorder = core(SPOT)
    recorder.restore_awaiting([PendingCandle(flow, None, Decimal("0.1"), active=True)])
    (request,) = recorder.candle_requests(at(0))
    assert recorder.apply_rest_candles(request, [], at(0)) == [], "pending: asked again"
    blind = core(SPOT)
    blind.restore_awaiting([PendingCandle(flow, None, Decimal("0.1"))])
    (request,) = blind.candle_requests(at(0))
    (decision,) = blind.apply_rest_candles(request, [], at(0))
    assert decision.state is CandleState.AUSENCIA_SIN_TRADES


def test_the_day_close_waits_until_the_core_has_closed_every_minute() -> None:
    """Review of E2, fourth round (R4-E2-01): on a quiet connection the last minute waits for
    its proof of life, and the day close waits for it. A retired instrument counts up to
    the minute in which it was retired; one the core does not record has nothing to close."""
    recorder = core(SPOT)
    connect(recorder, at(0))
    recorder.on_alive("SPOT", at(60))
    recorder.tick(at(75))  # the last pong was at 60 s: [0, 60) is not closed yet
    assert not recorder.closed_through("SPOT", "BTC-USDT", at(60))
    tick(recorder, at(78))  # the next pong
    assert recorder.closed_through("SPOT", "BTC-USDT", at(60))
    assert recorder.closed_through("SPOT", "ETH-USDT", at(60)), "not recorded: nothing to close"

    recorder.apply_universe([], at(150))  # a late evaluation retires it at 2:30
    assert not recorder.closed_through("SPOT", "BTC-USDT", at(180))
    tick(recorder, at(160))
    assert recorder.closed_through("SPOT", "BTC-USDT", at(180)), "up to 2:00"
    assert recorder._retired == [], "nothing of it is left to close"


def test_a_stall_never_reconnects_a_connection_that_was_down() -> None:
    """Review of E2, third round (I-E2-05): the connection drops at 10 s, then the process
    is suspended from 20 to 40 s. The stall does not declare it connected: its gap stays
    open and nothing after 10 s is healthy until the stream really reconnects."""
    recorder = core(SPOT)
    connect(recorder, at(0))
    recorder.on_message("SPOT", book("SPOT", "BTC-USDT", 1, at(1)), at(1))
    recorder.on_state("SPOT", "disconnected", at(10), "ConnectionClosedError: no close frame")
    recorder.on_stall(at(20), at(40), "Stall: did not run for 20 s")
    out = tick(recorder, at(60))
    assert [g for g in out.gaps if g.symbol == "BTC-USDT"] == [], "its gap is still open"
    assert {s.state for s in out.slots if s.slot_start >= at(10)} == {SlotState.DESCONECTADO}
    recorder.on_state("SPOT", "connected", at(70), "wss://...")
    gaps = {(g.channel, g.start_at, g.end_at, g.cause) for g in tick(recorder, at(72)).gaps}
    assert ("trade", at(10), at(70), GapCause.DESCONEXION) in gaps


def test_a_stall_on_a_live_connection_is_an_outage_from_its_last_proof_of_life() -> None:
    recorder = core(SPOT)
    connect(recorder, at(0))
    recorder.on_message("SPOT", book("SPOT", "BTC-USDT", 1, at(1)), at(1))
    recorder.on_alive("SPOT", at(15))
    recorder.on_stall(at(20), at(40), "Stall: did not run for 20 s")
    out = recorder.tick(at(60))  # no proof of life after the stall yet
    states = {s.slot_start: s.state for s in out.slots if s.depth == 5}
    assert states == {
        at(0): SlotState.VALIDA,
        at(10): SlotState.DESCONECTADO,  # from the pong at 15 s, the last proof of life
        at(20): SlotState.DESCONECTADO,
    }, "[30, 40) waits: the slot after it cannot close before a proof of life"
    stall = [g for g in out.gaps if g.channel == "trade"]
    assert [(g.start_at, g.end_at, g.cause) for g in stall] == [
        (at(15), at(40), GapCause.PROCESO_DETENIDO)
    ]


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
    out = tick(recorder, at(62))
    trade_flow = next(f for f in out.flows if f.channel == "trade")
    assert trade_flow.flow_state is FlowState.S2 and trade_flow.anomalies == 1


def test_answers_without_the_candle_are_limited_attempts() -> None:
    recorder = core(SPOT)
    connect(recorder, at(0))
    feed_minute(recorder, at(0))
    tick(recorder, at(62))
    moment = at(75)
    for attempt in range(MAX_CANDLE_ATTEMPTS):
        (request,) = recorder.candle_requests(moment)
        decisions = recorder.apply_rest_candles(request, [], moment)
        assert recorder.candle_requests(moment) == [], "not again within the same minute"
        moment += timedelta(minutes=1)
        if attempt < MAX_CANDLE_ATTEMPTS - 1:
            assert decisions == [] and recorder.awaiting_until(at(3600)) == 1
    assert [d.state for d in decisions] == [CandleState.PENDIENTE], "decided without a candle"
    assert recorder.awaiting_until(at(3600)) == 0


def test_failed_requests_never_use_up_the_attempts() -> None:
    """Review of E2, second round: a failed request (network or KuCoin error) is not an
    answer. The minute is asked again after 1, 2, 4 and 8 minutes, then every 15, and stays
    pending; the candle is decided as soon as KuCoin answers."""
    recorder = core(SPOT)
    connect(recorder, at(0))
    feed_minute(recorder, at(0))
    tick(recorder, at(62))
    moment = at(75)
    for pause in (1, 2, 4, 8, 15, 15, 15):
        (request,) = recorder.candle_requests(moment)
        assert recorder.apply_rest_candles(request, None, moment) == []
        moment += timedelta(minutes=pause)
        assert recorder.candle_requests(moment - timedelta(seconds=1)) == []
    assert recorder.awaiting_until(at(86_400)) == 1, "seven failures: still pending"
    (request,) = recorder.candle_requests(moment)
    rows = [rest_kline(rest_row(at(0)))]
    (decision,) = recorder.apply_rest_candles(request, rows, moment)  # type: ignore[arg-type]
    assert decision.state is CandleState.CERRADA_CRUZADA


def test_a_minute_never_answered_is_given_up_after_48_hours() -> None:
    recorder = core(SPOT)
    connect(recorder, at(0))
    feed_minute(recorder, at(0))
    tick(recorder, at(62))
    for moment in (at(75), at(48 * 3600 - 200)):  # the second one, 200 s before 48 h
        (request,) = recorder.candle_requests(moment)
        recorder.apply_rest_candles(request, None, moment)
        assert recorder.awaiting_until(at(10**6)) == 1
    last = at(48 * 3600 + 100)
    (request,) = recorder.candle_requests(last)
    recorder.apply_rest_candles(request, None, last)
    assert recorder.awaiting_until(at(10**6)) == 0, "given up: its day closes as it is"


def test_a_minute_seen_on_the_websocket_waits_for_its_rest_candle() -> None:
    recorder = core(SPOT)
    connect(recorder, at(0))
    feed_minute(recorder, at(0))
    tick(recorder, at(62))
    (request,) = recorder.candle_requests(at(75))
    assert recorder.apply_rest_candles(request, [], at(75)) == []  # pendiente: ask again
    (again,) = recorder.candle_requests(at(135))
    rows = [rest_kline(rest_row(at(0)))]
    (decision,) = recorder.apply_rest_candles(again, rows, at(135))  # type: ignore[arg-type]
    assert decision.state is CandleState.CERRADA_CRUZADA


def test_the_websocket_candle_leaves_memory_with_its_flow() -> None:
    """Audit E2-01: the WebSocket candle of a closed minute goes to the database with the
    trade flow of that minute, with the time KuCoin published it."""
    recorder = core(SPOT)
    connect(recorder, at(0))
    feed_minute(recorder, at(0))
    out = tick(recorder, at(62))
    (ws,) = out.ws_candles
    assert (ws.market, ws.symbol, ws.candle.open_time) == ("SPOT", "BTC-USDT", at(0))
    assert ws.published_at == at(30) and ws.candle.volume == Decimal(2)
    assert any(f.channel == "trade" and f.minute == at(0) for f in out.flows)


def test_a_restored_minute_is_decided_from_the_stored_evidence() -> None:
    """Audit E2-01: after a restart a minute is decided from the flow and the WebSocket
    candle that the database holds. Without that candle the same REST candle would be
    taken for a discrepancy."""
    minute = at(-120)  # closed by the previous run
    flow = FlowMinute("SPOT", "BTC-USDT", "trade", minute, FlowState.S1, 3, 0, 0)
    ws = ws_kline(kline("SPOT", "BTC-USDT", minute, minute + timedelta(seconds=30)))
    rows = [rest_kline(rest_row(minute))]

    recorder = core(SPOT)
    assert recorder.restore_awaiting([PendingCandle(flow, ws, Decimal("0.1"))]) == 1
    assert recorder.restore_awaiting([PendingCandle(flow, None, Decimal("0.1"))]) == 0
    (request,) = recorder.candle_requests(at(0))
    (decision,) = recorder.apply_rest_candles(request, rows, at(0))  # type: ignore[arg-type]
    assert decision.state is CandleState.CERRADA_CRUZADA and decision.ws == ws
    assert recorder.awaiting_until(at(3600)) == 0

    blind = core(SPOT)
    blind.restore_awaiting([PendingCandle(flow, None, Decimal("0.1"))])
    (request,) = blind.candle_requests(at(0))
    (decision,) = blind.apply_rest_candles(request, rows, at(0))  # type: ignore[arg-type]
    assert decision.state is CandleState.DISCREPANTE, "what the audit warned about"


def test_a_request_never_spans_more_than_a_hundred_minutes() -> None:
    """Minutes far apart (resumed after a restart) never widen the REST window beyond 102
    candles: a request covers at most 100 minutes from its first to its last."""
    recorder = core(SPOT)
    minutes = [at(-86_400), at(-86_400 + 99 * 60), at(-86_400 + 100 * 60), at(-600)]
    recorder.restore_awaiting(
        PendingCandle(
            FlowMinute("SPOT", "BTC-USDT", "trade", m, FlowState.S3, 0, 0, 0), None, Decimal(1)
        )
        for m in minutes
    )
    requests = recorder.candle_requests(at(0))
    assert [(r.first, r.last) for r in requests] == [
        (minutes[0], minutes[1]),
        (minutes[2], minutes[2]),
        (minutes[3], minutes[3]),
    ]


def test_a_restart_resumes_where_the_database_says() -> None:
    stopped = at(-300)
    resume = {
        (FUT.market, FUT.symbol, channel): stopped
        for channel in ("obu5", "obu50", "trade", "kline", "mark-price")
    }
    recorder = core(FUT, resume=resume)
    connect(recorder, at(2))
    out = tick(recorder, at(62))
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
    out = tick(recorder, at(10))
    trade_gap = next(g for g in out.gaps if g.channel == "trade")
    assert trade_gap.start_at == stopped
    assert min(s.slot_start for s in out.slots) == at(-3600)


def test_the_universe_changes_at_midnight() -> None:
    midnight = datetime(2026, 11, 8, tzinfo=UTC)
    recorder = RecorderCore((SPOT, SPOT_SMALL), started_at=midnight - timedelta(minutes=2))
    for market in ("SPOT", "FUTURES"):
        recorder.on_state(market, "connected", midnight - timedelta(minutes=2), "wss://...")
    feed_minute(recorder, midnight - timedelta(minutes=1), with_marks=False)
    tick(recorder, midnight - timedelta(seconds=30))

    changed = recorder.apply_universe([SPOT, FUT], midnight)  # ETH leaves, XBT enters
    assert changed
    assert [s.symbol for s in recorder.specs] == ["BTC-USDT", "XBTUSDTM"]
    assert recorder.apply_universe([SPOT, FUT], midnight) is False, "nothing changes twice"

    out = tick(recorder, midnight + timedelta(minutes=5))
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
    tick(recorder, midnight + timedelta(minutes=6))
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
