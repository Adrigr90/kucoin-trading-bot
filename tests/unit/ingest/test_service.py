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

from kbot.ingest.core import MAX_CANDLE_ATTEMPTS
from kbot.ingest.service import EVALUATION_TIME, INDEX_ATTEMPTS, INDEX_DELAY, Recorder
from kbot.ingest.sinks import MemorySink
from kbot.kucoin.ratelimit import RestRateLimiter
from kbot.kucoin.rest import KucoinRestClient
from kbot.market.depth import SlotState
from kbot.market.gaps import GapCause
from kbot.market.last_candles import CandleState
from kbot.market.mark_candles import Evidence, FlowState
from tests.unit.ingest.fake_kucoin import FakeKucoin
from tests.unit.ingest.messages import beat, book, kline, mark, rest_row, seconds, trade

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
        await beat(recorder, minute)
        assert {(f.kind, f.symbol) for f in sink.funding} == {
            ("previsto", "XBTUSDTM"),
            ("liquidado", "XBTUSDTM"),
        }

        # 23:59:00: tomorrow's universe is evaluated, effective at midnight.
        await beat(recorder, MIDNIGHT - timedelta(minutes=1))
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
        await beat(recorder, MIDNIGHT - timedelta(seconds=44))
        decided = {(d.symbol, d.minute): d.state for d in sink.last_candles}
        assert decided[("BTC-USDT", minute)] is CandleState.CERRADA_CRUZADA
        assert decided[("XBTUSDTM", minute)] is CandleState.CERRADA_CRUZADA
        before = minute - timedelta(minutes=1)  # 23:57, started half-way: S2, REST alone
        assert decided[("BTC-USDT", before)] is CandleState.CERRADA_REST
        own = [(c.series, c.open_time, c.evidence) for t in sink.ticks for c in t.own_candles]
        assert own == [("mark", minute, Evidence.FLOW), ("index", minute, Evidence.FLOW)]

        # Midnight: the day switches; 00:00:16 the last minute is decided and the day closed.
        await beat(recorder, MIDNIGHT)
        assert recorder.restart_streams is False, "same universe: no new subscriptions"
        await beat(recorder, MIDNIGHT + timedelta(seconds=16))
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
        await beat(recorder, MIDNIGHT + timedelta(minutes=2, seconds=1))
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
        await beat(recorder, MIDNIGHT - timedelta(minutes=1))
        await beat(recorder, MIDNIGHT)
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


def connect_both(recorder: Recorder, moment: datetime) -> None:
    core = recorder.core
    assert core is not None
    for market in ("SPOT", "FUTURES"):
        core.on_state(market, "connected", moment, "wss://...")


def drop_futures(recorder: Recorder, minute: datetime) -> None:
    """A short Futures outage inside ``minute``: its mark-price minute is S2."""
    core = recorder.core
    assert core is not None
    core.on_state(
        "FUTURES",
        "disconnected",
        minute + timedelta(seconds=40),
        "ConnectionClosedError: no close frame received or sent",
    )
    core.on_state("FUTURES", "connected", minute + timedelta(seconds=45), "wss://...")


def test_pending_rest_candles_survive_a_restart(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    """Audit E2-01: the process stops after minute 23:58 is closed and written, before its
    REST candle is asked for. The next start-up asks for it again and decides it from the
    stored WebSocket candle; a second restart finds nothing left to decide."""
    recorder, sink, _fake = setup
    minute = MIDNIGHT - timedelta(minutes=2)

    async def first_run() -> None:
        await recorder.startup(START)
        connect_both(recorder, START + timedelta(seconds=1))
        feed(recorder, minute)
        await beat(recorder, minute + timedelta(seconds=62))  # its REST candle is due at :15

    run(first_run())
    assert {(d.minute, d.state) for d in sink.last_candles} == {
        (minute - timedelta(minutes=1), CandleState.CERRADA_REST)
    }, "only 23:57, which was due"
    stored = {(w.symbol, w.candle.open_time) for t in sink.ticks for w in t.ws_candles}
    assert stored == {("BTC-USDT", minute), ("XBTUSDTM", minute)}

    restarted = Recorder(sink, rest=recorder._rest)

    async def second_run() -> None:
        await restarted.startup(minute + timedelta(seconds=70))
        core = restarted.core
        assert core is not None
        assert core.awaiting_minutes("SPOT", "BTC-USDT") == [minute]
        assert core.awaiting_minutes("FUTURES", "XBTUSDTM") == [minute]
        await beat(restarted, minute + timedelta(seconds=75))

    run(second_run())
    decided = {(d.symbol, d.minute): d for d in sink.last_candles}
    for symbol in ("BTC-USDT", "XBTUSDTM"):
        decision = decided[(symbol, minute)]
        assert decision.state is CandleState.CERRADA_CRUZADA, "never discrepant for lack of it"
        assert decision.ws is not None and decision.ws.volume == Decimal(2)

    again = Recorder(sink, rest=recorder._rest)
    run(again.startup(minute + timedelta(seconds=80)))
    assert again.core is not None
    assert again.core.awaiting_until(MIDNIGHT + timedelta(days=1)) == 0, "nothing twice"


def test_pending_index_rebuilds_survive_a_restart(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    """Audit E2-02: index minutes waiting for Classic when the process stops (23:57 not
    recorded, 23:58 with an outage) are rebuilt after the restart, and not again later."""
    recorder, sink, _fake = setup
    minute = MIDNIGHT - timedelta(minutes=2)

    async def first_run() -> None:
        await recorder.startup(START)
        connect_both(recorder, START + timedelta(seconds=1))
        feed(recorder, minute)
        drop_futures(recorder, minute)
        await beat(recorder, minute + timedelta(seconds=62))  # Classic is asked 3 min later

    run(first_run())
    assert sink.index_candles == []
    restarted = Recorder(sink, rest=recorder._rest)

    async def second_run() -> None:
        await restarted.startup(minute + timedelta(seconds=70))
        await beat(restarted, minute + timedelta(minutes=3, seconds=1))

    run(second_run())
    assert [(c.open_time, c.evidence, c.samples) for c in sink.index_candles] == [
        (minute - timedelta(minutes=1), Evidence.CLASSIC, 60),
        (minute, Evidence.CLASSIC, 60),
    ]
    again = Recorder(sink, rest=recorder._rest)
    run(again.startup(minute + timedelta(minutes=3, seconds=5)))
    assert all(m > minute for _, m in again._index), "rebuilt minutes are not asked again"


@pytest.mark.parametrize(
    ("value", "recorded"),
    [
        (0, True),  # Classic answers, never with a complete minute: given up, recorded
        ("unreadable", False),  # Classic never gives a usable answer: still pending
    ],
)
def test_an_index_minute_that_cannot_be_rebuilt(
    setup: tuple[Recorder, MemorySink, FakeKucoin], value: object, recorded: bool
) -> None:
    """Audit E2-02: rebuilt, pending and given up are told apart. A minute that Classic
    cannot complete is given up once and the database says so; one that Classic never
    answered for stays pending for the next start-up."""
    recorder, sink, fake = setup
    fake.index_value = lambda ms: value
    minute = MIDNIGHT - timedelta(minutes=2)

    async def scenario() -> None:
        await recorder.startup(START)
        connect_both(recorder, START + timedelta(seconds=1))
        feed(recorder, minute)
        drop_futures(recorder, minute)
        moment = minute + timedelta(seconds=62)
        await beat(recorder, moment)
        for _ in range(INDEX_ATTEMPTS):
            moment += INDEX_DELAY
            await beat(recorder, moment)

    run(scenario())
    assert sink.index_candles == []
    given_up = [(m.minute, m.flow_state, n) for m, n in sink.exhausted_index if m.minute <= minute]
    expected = [
        (minute - timedelta(minutes=1), FlowState.S3, INDEX_ATTEMPTS),
        (minute, FlowState.S2, INDEX_ATTEMPTS),
    ]
    assert given_up == (expected if recorded else [])
    restarted = Recorder(sink, rest=recorder._rest)
    run(restarted.startup(minute + timedelta(minutes=10)))
    resumed = sorted(m for _, m in restarted._index if m <= minute)
    assert resumed == ([] if recorded else [minute - timedelta(minutes=1), minute])


def test_a_stall_across_midnight_evaluates_the_day_that_started(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    """Audit E2-04: the recorder is suspended from 23:58 to 00:10 and misses the 23:59
    evaluation. The universe of the day that started is evaluated at 00:10, stored with
    that time and applied at once; the next evaluation is at 23:59, for the next day."""
    recorder, sink, fake = setup
    late = MIDNIGHT + timedelta(minutes=10)

    async def scenario() -> None:
        await recorder.startup(START)
        connect_both(recorder, START + timedelta(seconds=1))
        recorder._last_beat = MIDNIGHT - timedelta(minutes=2)  # as run() does
        await beat(recorder, MIDNIGHT - timedelta(minutes=2))  # the last beat before the stall
        fake.spot = ["BTC-USDT", "ETH-USDT"]  # ETH enters the listings of the new day
        await beat(recorder, late)
        core = recorder.core
        assert core is not None
        assert {e.evaluated_at for e in sink.universe} == {START, late}
        assert recorder.restart_streams is True
        assert [s.symbol for s in core.specs] == ["BTC-USDT", "XBTUSDTM", "ETH-USDT"]
        assert recorder._next_evaluation == MIDNIGHT + EVALUATION_TIME
        assert recorder._tomorrow is None
        recorder._last_beat = None
        await beat(recorder, late + timedelta(seconds=30))

    run(scenario())
    eth = [s.slot_start for t in sink.ticks for s in t.slots if s.symbol == "ETH-USDT"]
    assert eth and min(eth) == late, "ETH is recorded from the late evaluation on"
    stalled = {g.channel for t in sink.ticks for g in t.gaps if g.symbol == "BTC-USDT"}
    assert "trade" in stalled
    assert sorted(sink.closed_days) == [
        ("FUTURES", "XBTUSDTM", DAY),
        ("SPOT", "BTC-USDT", DAY),
    ], "the day before is closed with the universe it had"


def test_a_stop_across_midnight_evaluates_the_new_day_at_start_up(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    """Audit E2-04: stopped at 23:58 and started again at 00:10, the recorder evaluates the
    universe of the new day with the time of the evaluation; the next one is at 23:59."""
    recorder, sink, fake = setup
    run(recorder.startup(START))
    fake.spot = ["BTC-USDT", "ETH-USDT"]
    late = MIDNIGHT + timedelta(minutes=10)
    restarted = Recorder(sink, rest=recorder._rest)
    run(restarted.startup(late))
    assert {e.evaluated_at for e in sink.universe} == {START, late}
    assert restarted.core is not None
    assert [s.symbol for s in restarted.core.specs] == ["BTC-USDT", "ETH-USDT", "XBTUSDTM"]
    assert restarted._next_evaluation == MIDNIGHT + EVALUATION_TIME


def test_a_restart_before_midnight_reuses_tomorrows_evaluation(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    """Tomorrow's universe is evaluated once: a restart between 23:59 and midnight reuses
    the stored evaluation instead of making a second one for the same midnight."""
    recorder, sink, fake = setup
    run(recorder.startup(START))
    run(beat(recorder, MIDNIGHT - timedelta(minutes=1)))
    stored = [e for e in sink.universe if e.evaluated_at == MIDNIGHT]
    assert stored
    fake.spot = ["BTC-USDT", "ETH-USDT"]  # the listings change before the restart
    restarted = Recorder(sink, rest=recorder._rest)

    async def scenario() -> None:
        await restarted.startup(MIDNIGHT - timedelta(seconds=30))
        await beat(restarted, MIDNIGHT - timedelta(seconds=29))
        await beat(restarted, MIDNIGHT)

    run(scenario())
    assert [e for e in sink.universe if e.evaluated_at == MIDNIGHT] == stored
    assert restarted.core is not None
    assert [s.symbol for s in restarted.core.specs] == ["BTC-USDT", "XBTUSDTM"]
    assert restarted.restart_streams is False


def test_a_candle_that_can_still_be_recovered_keeps_its_day_open(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    """Review of E2, second round: KuCoin's REST does not answer for XBTUSDTM (network
    errors) six times in a row. Its day stays open while its candles can be recovered;
    after a restart within 48 h REST answers, the candles are decided from the stored
    evidence and only then is the day closed. BTC-USDT closes on time; nothing twice."""
    recorder, sink, fake = setup
    fake.unreachable = {"XBTUSDTM"}
    minute = MIDNIGHT - timedelta(minutes=2)  # 23:58
    # 23:58 is first asked at 00:00:02, then again 1, 2, 4, 8 and 15 minutes later.
    steps = [minute + timedelta(seconds=62), MIDNIGHT + timedelta(seconds=2)]
    steps += [MIDNIGHT + timedelta(seconds=16)]
    steps += [MIDNIGHT + timedelta(minutes=m, seconds=2) for m in (1, 3, 7, 15, 30)]

    async def first_run() -> None:
        await recorder.startup(START)
        connect_both(recorder, START + timedelta(seconds=1))
        feed(recorder, minute)
        feed(recorder, minute + timedelta(minutes=1))
        for moment in steps:
            await beat(recorder, moment)

    run(first_run())
    assert sink.closed_days == [("SPOT", "BTC-USDT", DAY)], "XBTUSDTM can still be recovered"
    core = recorder.core
    assert core is not None
    waiting = [m for m in core.awaiting_minutes("FUTURES", "XBTUSDTM") if m < MIDNIGHT]
    assert waiting == [minute - timedelta(minutes=1), minute, minute + timedelta(minutes=1)]

    fake.unreachable = set()  # the network is back
    restarted = Recorder(sink, rest=recorder._rest)

    async def second_run() -> None:
        await restarted.startup(MIDNIGHT + timedelta(hours=9))
        await beat(restarted, MIDNIGHT + timedelta(hours=9, seconds=1))

    run(second_run())
    decided = {d.minute: d.state for d in sink.last_candles if d.symbol == "XBTUSDTM"}
    assert decided == {
        minute - timedelta(minutes=1): CandleState.CERRADA_REST,  # started half-way: S2
        minute: CandleState.CERRADA_CRUZADA,  # with the WebSocket candle kept from 23:58
        minute + timedelta(minutes=1): CandleState.CERRADA_CRUZADA,
    }
    assert sorted(sink.closed_days) == [("FUTURES", "XBTUSDTM", DAY), ("SPOT", "BTC-USDT", DAY)]

    again = Recorder(sink, rest=recorder._rest)
    run(again.startup(MIDNIGHT + timedelta(hours=9, minutes=5)))
    run(beat(again, MIDNIGHT + timedelta(hours=9, minutes=5, seconds=1)))
    assert again.core is not None
    assert [m for m in again.core.awaiting_minutes("FUTURES", "XBTUSDTM") if m < MIDNIGHT] == []
    assert len(sink.closed_days) == 2, "no day is closed twice"


def test_a_day_left_open_is_closed_after_a_restart_beyond_48_hours(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    """Review of E2, third round (I-E2-06): XBTUSDTM's day stays open because KuCoin never
    answers for its candles, and the process starts again three days later. Its minutes can
    no longer be recovered, but the day is found in the database and closed as it is;
    another start changes nothing."""
    recorder, sink, fake = setup
    fake.unreachable = {"XBTUSDTM"}
    minute = MIDNIGHT - timedelta(minutes=2)

    async def first_run() -> None:
        await recorder.startup(START)
        connect_both(recorder, START + timedelta(seconds=1))
        feed(recorder, minute)
        for moment in (minute + timedelta(seconds=62), MIDNIGHT + timedelta(seconds=16)):
            await beat(recorder, moment)

    run(first_run())
    assert sink.closed_days == [("SPOT", "BTC-USDT", DAY)]
    later = MIDNIGHT + timedelta(days=3, hours=1)
    restarted = Recorder(sink, rest=recorder._rest)

    async def second_run() -> None:
        await restarted.startup(later)
        await beat(restarted, later + timedelta(seconds=1))

    run(second_run())
    assert restarted.core is not None
    waiting = restarted.core.awaiting_minutes("FUTURES", "XBTUSDTM")
    assert [m for m in waiting if m < MIDNIGHT] == [], "older than 48 h: not asked again"
    of_the_day = sorted(d for d in sink.closed_days if d[2] == DAY)
    assert of_the_day == [("FUTURES", "XBTUSDTM", DAY), ("SPOT", "BTC-USDT", DAY)]
    closed = len(sink.closed_days)
    again = Recorder(sink, rest=recorder._rest)
    run(again.startup(later + timedelta(minutes=5)))
    run(beat(again, later + timedelta(minutes=5, seconds=1)))
    assert len(sink.closed_days) == closed, "nothing is closed twice"


def test_a_minute_settled_without_its_candle_is_final_once_its_day_is_closed(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    """KuCoin answers five times without the candle of 23:59, which the WebSocket saw: the
    minute is decided without a candle, the day is closed, and a later restart does not
    ask for it again. What is closed never changes."""
    recorder, sink, fake = setup
    minute = MIDNIGHT - timedelta(minutes=1)

    def without_it(market: str, symbol: str, start: int, end: int) -> list[list[object]]:
        return [row for row in candles(market, symbol, start, end) if row[0] != seconds(minute)]

    fake.candles = without_it

    async def scenario() -> None:
        await recorder.startup(START)
        connect_both(recorder, START + timedelta(seconds=1))
        feed(recorder, minute)
        for answers in range(MAX_CANDLE_ATTEMPTS + 2):  # 23:59 is first asked at 00:01:02
            await beat(recorder, MIDNIGHT + timedelta(minutes=answers, seconds=2))
            assert (len(sink.closed_days) == 2) is (answers >= MAX_CANDLE_ATTEMPTS)

    run(scenario())
    assert sorted(sink.closed_days) == [("FUTURES", "XBTUSDTM", DAY), ("SPOT", "BTC-USDT", DAY)]
    assert not any(d.minute == minute and d.has_candle for d in sink.last_candles)
    restarted = Recorder(sink, rest=recorder._rest)
    run(restarted.startup(MIDNIGHT + timedelta(hours=1)))
    assert restarted.core is not None
    for market, symbol in (("SPOT", "BTC-USDT"), ("FUTURES", "XBTUSDTM")):
        assert minute not in restarted.core.awaiting_minutes(market, symbol)


def test_incomplete_listings_are_evaluated_again_soon(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    """Review of E2, second round: with incomplete listings at 23:59 nothing is stored and
    the evaluation is tried again 30 s later, still for midnight; the current universe
    stays in force meanwhile."""
    recorder, sink, fake = setup
    run(recorder.startup(START))
    fake.futures = []  # KuCoin lists no contract at 23:59
    run(beat(recorder, MIDNIGHT - timedelta(minutes=1)))
    assert {e.evaluated_at for e in sink.universe} == {START}
    assert recorder._next_evaluation == MIDNIGHT - timedelta(seconds=30)
    fake.futures = ["XBTUSDTM"]
    run(beat(recorder, MIDNIGHT - timedelta(seconds=30)))
    assert {e.evaluated_at for e in sink.universe} == {START, MIDNIGHT}, "on time, for midnight"
    assert recorder._next_evaluation == MIDNIGHT + EVALUATION_TIME


def test_listings_missing_until_after_midnight_give_a_late_evaluation(
    setup: tuple[Recorder, MemorySink, FakeKucoin],
) -> None:
    recorder, sink, fake = setup
    late = MIDNIGHT + timedelta(seconds=30)

    async def scenario() -> None:
        await recorder.startup(START)
        fake.futures = []
        for moment in (MIDNIGHT - timedelta(minutes=1), MIDNIGHT - timedelta(seconds=30), MIDNIGHT):
            await beat(recorder, moment)
        assert {e.evaluated_at for e in sink.universe} == {START}, "never a partial universe"
        fake.futures = ["XBTUSDTM"]
        fake.spot = ["BTC-USDT", "ETH-USDT"]
        await beat(recorder, late)

    run(scenario())
    assert {e.evaluated_at for e in sink.universe} == {START, late}
    assert recorder.restart_streams is True
    assert recorder.core is not None
    assert [s.symbol for s in recorder.core.specs] == ["BTC-USDT", "XBTUSDTM", "ETH-USDT"]
    assert recorder._next_evaluation == MIDNIGHT + EVALUATION_TIME


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
        await beat(recorder, START + timedelta(seconds=3))  # one second later: no stall
        frozen_until = START + timedelta(minutes=2)
        await beat(recorder, frozen_until)  # the next beat comes two minutes late

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
