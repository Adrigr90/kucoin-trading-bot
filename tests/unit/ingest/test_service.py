"""The whole recorder (service and core) through simulated time, with a fake KuCoin REST.

From 23:57:30 to 00:02:30 UTC: start-up, a recorded minute with its REST candles and own
mark and index candles, funding, tomorrow's universe at 23:59, a disconnection whose index
minute is rebuilt from Classic, the switch of day and the close of the day.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from kbot.ingest.service import Recorder
from kbot.ingest.sinks import MemorySink
from kbot.kucoin.ratelimit import RestRateLimiter
from kbot.kucoin.rest import KucoinRestClient
from kbot.market.depth import SlotState
from kbot.market.gaps import GapCause
from kbot.market.last_candles import CandleState
from kbot.market.mark_candles import Evidence
from tests.unit.ingest.fake_kucoin import FakeKucoin
from tests.unit.ingest.messages import book, kline, mark, rest_row, seconds, trade

DAY = datetime(2026, 11, 7, tzinfo=UTC)
MIDNIGHT = DAY + timedelta(days=1)
START = MIDNIGHT - timedelta(minutes=2, seconds=30)

Factory = Callable[..., KucoinRestClient]


def candles(market: str, symbol: str, start: int, end: int) -> list[list[object]]:
    """A REST candle for every minute in the range (newest first, as KuCoin answers)."""
    first = start - start % 60 + 60
    return [
        rest_row(datetime.fromtimestamp(s, UTC))
        for s in range(end - end % 60, first - 1, -60)
        if s < seconds(MIDNIGHT)
    ]


def feed(recorder: Recorder, minute: datetime) -> None:
    core = recorder.core
    assert core is not None
    for second in range(0, 60, 10):
        moment = minute + timedelta(seconds=second + 1)
        sequence = int(moment.timestamp())
        core.on_message("SPOT", book("SPOT", "BTC-USDT", sequence, moment), moment)
        core.on_message("SPOT", book("SPOT", "BTC-USDT", sequence, moment, "50"), moment)
        core.on_message("FUTURES", book("FUTURES", "XBTUSDTM", sequence, moment), moment)
        core.on_message("FUTURES", book("FUTURES", "XBTUSDTM", sequence, moment, "50"), moment)
    at = minute + timedelta(seconds=30)
    for market, symbol in (("SPOT", "BTC-USDT"), ("FUTURES", "XBTUSDTM")):
        core.on_message(market, trade(market, symbol, at), at)
        core.on_message(market, kline(market, symbol, minute, at), at)
    for second in range(60):
        moment = minute + timedelta(seconds=second)
        core.on_message("FUTURES", mark("XBTUSDTM", moment), moment)


def run(coroutine: object) -> None:
    asyncio.run(coroutine)  # type: ignore[arg-type]


@pytest.fixture
def setup(test_client: Factory) -> tuple[Recorder, MemorySink, FakeKucoin]:
    fake = FakeKucoin(spot=["BTC-USDT"], futures=["XBTUSDTM"], candles=candles)
    sink = MemorySink()
    budget = RestRateLimiter(pool_weight=10**6)  # tests never wait for the real budget
    return Recorder(sink, rest=test_client(fake, budget)), sink, fake


def test_five_simulated_minutes_around_midnight(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    recorder, sink, fake = setup

    async def scenario() -> None:
        await recorder.startup(START)
        core = recorder.core
        assert core is not None
        assert [(s.market, s.symbol, s.l50, s.index_symbol) for s in core.specs] == [
            ("SPOT", "BTC-USDT", True, None),
            ("FUTURES", "XBTUSDTM", True, ".KXBTUSDT"),
        ]
        assert {e.evaluated_at for e in sink.universe} == {START}
        assert [r.symbol for r in sink.instruments] == ["BTC-USDT", "XBTUSDTM"]
        assert [t.level for t in sink.risk_tiers["XBTUSDTM"]] == [1, 2]

        for market in ("SPOT", "FUTURES"):
            core.on_state(market, "connected", START + timedelta(seconds=1), "wss://...")
        minute = MIDNIGHT - timedelta(minutes=2)  # 23:58
        feed(recorder, minute)
        await recorder.step(minute)
        assert {(f.kind, f.symbol) for f in sink.funding} == {
            ("previsto", "XBTUSDTM"),
            ("liquidado", "XBTUSDTM"),
        }

        # 23:59:00: tomorrow's universe is evaluated, effective at midnight.
        await recorder.step(MIDNIGHT - timedelta(minutes=1))
        assert {e.evaluated_at for e in sink.universe} == {START, MIDNIGHT}

        # 23:59:16: minute 23:58 closed, its REST candles decided, its own candles built.
        feed(recorder, MIDNIGHT - timedelta(minutes=1))
        core.on_state(
            "FUTURES",
            "disconnected",
            MIDNIGHT - timedelta(seconds=40),
            "ConnectionClosedError: no close frame received or sent",
        )
        core.on_state("FUTURES", "connected", MIDNIGHT - timedelta(seconds=35), "wss://...")
        await recorder.step(MIDNIGHT - timedelta(seconds=44))
        decided = {(d.symbol, d.minute): d.state for d in sink.last_candles}
        assert decided[("BTC-USDT", minute)] is CandleState.CERRADA_CRUZADA
        assert decided[("XBTUSDTM", minute)] is CandleState.CERRADA_CRUZADA
        before = minute - timedelta(minutes=1)  # 23:57, started half-way: S2, REST alone
        assert decided[("BTC-USDT", before)] is CandleState.CERRADA_REST
        own = [(c.series, c.open_time, c.evidence) for t in sink.ticks for c in t.own_candles]
        assert own == [("mark", minute, Evidence.FLOW), ("index", minute, Evidence.FLOW)]

        # Midnight: the day switches; 00:00:16 the last minute is decided and the day closed.
        await recorder.step(MIDNIGHT)
        assert recorder.restart_streams is False, "same universe: no new subscriptions"
        await recorder.step(MIDNIGHT + timedelta(seconds=16))
        last = MIDNIGHT - timedelta(minutes=1)
        decided = {(d.symbol, d.minute): d.state for d in sink.last_candles}
        assert decided[("BTC-USDT", last)] is CandleState.CERRADA_CRUZADA
        assert decided[("XBTUSDTM", last)] is CandleState.CERRADA_REST, "S2: the outage"
        assert sorted(sink.closed_days) == [
            ("FUTURES", "XBTUSDTM", DAY),
            ("SPOT", "BTC-USDT", DAY),
        ]

        # 00:02:01: the index minute of the outage is rebuilt from Classic (23:57, the
        # half-recorded first minute, was rebuilt at midnight).
        await recorder.step(MIDNIGHT + timedelta(minutes=2, seconds=1))
        assert [(c.series, c.open_time, c.evidence, c.samples) for c in sink.index_candles] == [
            ("index", before, Evidence.CLASSIC, 60),
            ("index", last, Evidence.CLASSIC, 60),
        ]
        assert sink.index_candles[0].open == Decimal(75000)

    run(scenario())

    gaps = [(g.channel, g.cause) for t in sink.ticks for g in t.gaps]
    assert ("trade", GapCause.DESCONEXION) in gaps
    states = [s.state for t in sink.ticks for s in t.slots if s.symbol == "BTC-USDT"]
    assert SlotState.VALIDA in states
    assert all(path.startswith("/api/") for path in fake.requests)


def test_a_new_universe_renews_the_subscriptions(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    recorder, sink, fake = setup

    async def scenario() -> None:
        await recorder.startup(START)
        fake.spot = ["BTC-USDT", "ETH-USDT"]  # ETH appears in tomorrow's listings
        await recorder.step(MIDNIGHT - timedelta(minutes=1))
        await recorder.step(MIDNIGHT)
        assert recorder.restart_streams is True
        core = recorder.core
        assert core is not None
        assert [s.symbol for s in core.specs] == ["BTC-USDT", "XBTUSDTM", "ETH-USDT"]

    run(scenario())
    tomorrow = [e.symbol for e in sink.universe if e.evaluated_at == MIDNIGHT]
    assert sorted(tomorrow) == ["BTC-USDT", "ETH-USDT", "XBTUSDTM"]


def test_start_up_fails_without_listings(test_client: Factory) -> None:
    fake = FakeKucoin(spot=[], futures=[], candles=candles)
    recorder = Recorder(MemorySink(), rest=test_client(fake, RestRateLimiter(pool_weight=10**6)))
    with pytest.raises(RuntimeError, match="listings"):
        run(recorder.startup(START))


def test_a_stored_universe_of_the_day_is_reused(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    recorder, sink, fake = setup
    run(recorder.startup(START))
    stored = list(sink.universe)
    restarted = Recorder(sink, rest=recorder._rest)
    fake.spot = ["BTC-USDT", "ETH-USDT"]  # the listings changed, but not the day
    run(restarted.startup(START + timedelta(minutes=1)))
    assert sink.universe == stored, "no second evaluation within the same day"
    assert restarted.core is not None
    assert [s.symbol for s in restarted.core.specs] == ["BTC-USDT", "XBTUSDTM"]


def test_a_stall_is_an_outage_never_a_healthy_connection(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    """The live run on 8 November 2026 froze for 17 minutes when the PC was suspended:
    that time must be recorded as a gap and its slots as desconectado."""
    recorder, sink, _fake = setup

    async def scenario() -> None:
        await recorder.startup(START)
        core = recorder.core
        assert core is not None
        for market in ("SPOT", "FUTURES"):
            core.on_state(market, "connected", START + timedelta(seconds=1), "wss://...")
        recorder._last_beat = START + timedelta(seconds=2)  # as run() does
        await recorder.step(START + timedelta(seconds=3))  # one second later: no stall
        frozen_until = START + timedelta(minutes=2)
        await recorder.step(frozen_until)  # the next beat comes two minutes late

    run(scenario())
    gaps = [g for t in sink.ticks for g in t.gaps if g.cause is GapCause.PROCESO_DETENIDO]
    assert {g.channel for g in gaps} == {"obu5", "obu50", "trade", "kline", "mark-price"}
    assert {(g.start_at, g.end_at) for g in gaps} == {
        (START + timedelta(seconds=3), START + timedelta(minutes=2))
    }
    frozen = [
        s
        for t in sink.ticks
        for s in t.slots
        if START + timedelta(seconds=10) <= s.slot_start < START + timedelta(minutes=1, seconds=50)
    ]
    assert frozen and {s.state for s in frozen} == {SlotState.DESCONECTADO}
