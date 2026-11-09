"""The recorder writing to a real PostgreSQL as kbot_ingest, through simulated time.

The scenario of tests/unit/ingest/test_service.py, with every write going through the
foreign keys and CHECK constraints of migration 0002, and then a restart that resumes
from what the database says (acceptance F2-A5, F2-A6 and F2-A8 together).
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from kbot.ingest.service import Recorder
from kbot.ingest.sinks import DatabaseSink
from kbot.kucoin.ratelimit import RestRateLimiter
from kbot.kucoin.rest import KucoinRestClient
from kbot.market import store
from tests.unit.ingest.fake_kucoin import FakeKucoin
from tests.unit.ingest.messages import (
    beat,
    book,
    feed_minute,
    kline,
    rest_candles_until,
    rest_row,
    seconds,
    trade,
)

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]
Factory = Callable[..., KucoinRestClient]
FreeDay = Callable[[], datetime]
HHMM = "to_char(open_time AT TIME ZONE 'UTC', 'HH24:MI')"


def test_the_recorder_writes_a_consistent_day_and_resumes(
    connect: Connect, test_client: Factory, free_day: FreeDay
) -> None:
    # A day and symbols of their own, before any other test's universe, so the universe in
    # force on the days before is empty and no other test shares these rows.
    day = free_day()
    midnight = day + timedelta(days=1)
    start = midnight - timedelta(minutes=2, seconds=30)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"
    fake = FakeKucoin(spot=[spot], futures=[futures], candles=rest_candles_until(midnight))

    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        recorder = Recorder(sink, rest=test_client(fake, RestRateLimiter(pool_weight=10**6)))

        async def first_run() -> None:
            await recorder.startup(start)
            core = recorder.core
            assert core is not None
            for market in ("SPOT", "FUTURES"):
                core.on_state(market, "connected", start + timedelta(seconds=1), "wss://...")
            feed_minute(core, midnight - timedelta(minutes=2), spot, futures)
            await beat(recorder, midnight - timedelta(minutes=2))
            await beat(recorder, midnight - timedelta(minutes=1))
            feed_minute(core, midnight - timedelta(minutes=1), spot, futures)
            core.on_state(
                "FUTURES",
                "disconnected",
                midnight - timedelta(seconds=40),
                "ConnectionClosedError: no close frame received or sent",
            )
            core.on_state("FUTURES", "connected", midnight - timedelta(seconds=35), "wss://...")
            await beat(recorder, midnight - timedelta(seconds=44))
            await beat(recorder, midnight)
            await beat(recorder, midnight + timedelta(seconds=16))
            await beat(recorder, midnight + timedelta(minutes=2, seconds=1))

        asyncio.run(first_run())

        def rows(sql: str, *args: object) -> list[tuple[object, ...]]:
            return conn.execute(sql, args).fetchall()

        # F2-A8: one terminal state per slot, contiguous, from the start to the last closed.
        slots = rows(
            "SELECT slot_start, state FROM market.depth_slot_status"
            " WHERE symbol = %s AND depth = 5 ORDER BY slot_start",
            spot,
        )
        times = [r[0] for r in slots]
        assert times[0] == start
        assert times == [start + timedelta(seconds=10 * i) for i in range(len(times))]
        assert "valida" in {r[1] for r in slots}
        assert rows(
            "SELECT count(*) FROM market.depth_metrics m JOIN market.depth_slot_status s"
            " USING (market, symbol, depth, slot_start) WHERE m.symbol = %s"
            " AND s.state <> 'valida'",
            spot,
        ) == [(0,)]

        # Candles of the last series with their evidence, and own mark and index candles.
        last = dict(
            rows(
                f"SELECT symbol || ' ' || {HHMM}, state FROM market.candles"
                " WHERE series = 'last' AND symbol IN (%s, %s)",
                spot,
                futures,
            )
        )
        assert last == {
            f"{spot} 23:57": "cerrada_rest",
            f"{spot} 23:58": "cerrada_cruzada",
            f"{spot} 23:59": "cerrada_cruzada",
            f"{futures} 23:57": "cerrada_rest",
            f"{futures} 23:58": "cerrada_cruzada",
            f"{futures} 23:59": "cerrada_rest",
        }
        own = rows(
            f"SELECT series, {HHMM}, evidence, samples FROM market.candles"
            " WHERE symbol = %s AND series <> 'last' ORDER BY open_time, series",
            futures,
        )
        assert own == [
            ("index", "23:57", "reconstruida_classic", 60),
            ("index", "23:58", "construida_flujo", 60),
            ("mark", "23:58", "construida_flujo", 60),
            ("index", "23:59", "reconstruida_classic", 60),
        ]

        # F2-A6: the day is closed, 1,440 minutes per instrument, from the database alone.
        statuses = rows(
            "SELECT symbol, status, count(*) FROM market.minute_status"
            " WHERE minute_start >= %s AND minute_start < %s AND symbol IN (%s, %s)"
            " GROUP BY symbol, status ORDER BY symbol, status",
            day,
            midnight,
            spot,
            futures,
        )
        assert statuses == [
            (futures, "datos", 3),
            (futures, "sin_explicar", 1437),
            (spot, "datos", 3),
            (spot, "sin_explicar", 1437),
        ]

        # Gaps with their causes; reference data and universe.
        gaps = rows(
            "SELECT channel, cause FROM market.data_gaps WHERE symbol = %s"
            " ORDER BY start_at, channel",
            futures,
        )
        assert ("trade", "desconexion") in gaps and ("mark-price", "desconexion") in gaps
        assert rows(
            "SELECT count(DISTINCT evaluated_at) FROM market.universe_snapshots WHERE symbol = %s",
            spot,
        ) == [(2,)]
        assert rows(
            "SELECT count(*) FROM market.instruments WHERE symbol IN (%s, %s)", spot, futures
        ) == [(2,)], "unchanged instruments are not stored again"
        assert rows("SELECT count(*) FROM market.risk_tiers WHERE symbol = %s", futures) == [(2,)]
        assert rows(
            "SELECT DISTINCT kind FROM market.funding_rates WHERE symbol = %s ORDER BY kind",
            futures,
        ) == [("liquidado",), ("previsto",)]

        # F2-A5: the process stops after 00:02:01 and starts again at 00:05:00.
        restarted = Recorder(sink, rest=recorder._rest)
        restart = midnight + timedelta(minutes=5)

        async def second_run() -> None:
            await restarted.startup(restart)
            core = restarted.core
            assert core is not None
            for market in ("SPOT", "FUTURES"):
                core.on_state(market, "connected", restart + timedelta(seconds=2), "wss://...")
            await beat(restarted, restart + timedelta(seconds=30))

        asyncio.run(second_run())
        stop_gaps = rows(
            "SELECT channel, start_at, end_at FROM market.data_gaps WHERE symbol = %s"
            " AND cause = 'proceso_detenido' ORDER BY channel",
            spot,
        )
        resumed = {channel: (begin, end) for channel, begin, end in stop_gaps}
        assert set(resumed) == {"obu5", "obu50", "trade", "kline"}
        assert all(end == restart + timedelta(seconds=2) for _, end in resumed.values())
        after = rows(
            "SELECT slot_start, state FROM market.depth_slot_status WHERE symbol = %s"
            " AND depth = 5 AND slot_start >= %s ORDER BY slot_start",
            spot,
            midnight,
        )
        starts = [r[0] for r in after]
        assert starts == [midnight + timedelta(seconds=10 * i) for i in range(len(starts))]
        assert {r[1] for r in after if r[0] >= resumed["obu5"][0]} <= {
            "desconectado",
            "no_recibida",
        }


def test_pending_work_survives_restarts(
    connect: Connect, test_client: Factory, free_day: FreeDay
) -> None:
    """Audit E2-01 and E2-02 in PostgreSQL: the process stops after minute 23:58 is written,
    before its REST candle and its index rebuild are asked for. The next run resumes both
    from the database alone, decides the candle from the stored WebSocket candle and
    rebuilds the index from Classic; a third run finds nothing of them left to do."""
    day = free_day()
    midnight = day + timedelta(days=1)
    start = midnight - timedelta(minutes=2, seconds=30)
    minute = midnight - timedelta(minutes=2)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"
    fake = FakeKucoin(
        spot=[spot], futures=[futures], candles=rest_candles_until(midnight + timedelta(hours=1))
    )

    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        rest = test_client(fake, RestRateLimiter(pool_weight=10**6))

        def rows(sql: str, *args: object) -> list[tuple[object, ...]]:
            return conn.execute(sql, args).fetchall()

        def connected(recorder: Recorder, moment: datetime) -> None:
            assert recorder.core is not None
            for market in ("SPOT", "FUTURES"):
                recorder.core.on_state(market, "connected", moment, "wss://...")

        first = Recorder(sink, rest=rest)

        async def first_run() -> None:
            await first.startup(start)
            assert first.core is not None
            connected(first, start + timedelta(seconds=1))
            feed_minute(first.core, minute, spot, futures)
            first.core.on_state(
                "FUTURES",
                "disconnected",
                minute + timedelta(seconds=40),
                "ConnectionClosedError: no close frame received or sent",
            )
            first.core.on_state("FUTURES", "connected", minute + timedelta(seconds=45), "wss://")
            await beat(first, minute + timedelta(seconds=62))  # 23:58 written, nothing asked

        asyncio.run(first_run())
        assert rows(
            "SELECT symbol FROM market.ws_candles WHERE symbol IN (%s, %s) ORDER BY symbol",
            spot,
            futures,
        ) == [(futures,), (spot,)], "the WebSocket candles of 23:58 are only in the database"
        assert rows(
            "SELECT count(*) FROM market.candles WHERE open_time = %s AND symbol IN (%s, %s)",
            minute,
            spot,
            futures,
        ) == [(0,)]

        second = Recorder(sink, rest=rest)

        async def second_run() -> None:
            await second.startup(minute + timedelta(seconds=70))
            assert second.core is not None
            assert second.core.awaiting_minutes("SPOT", spot) == [minute]
            assert second.core.awaiting_minutes("FUTURES", futures) == [minute]
            assert sorted(m for _, m in second._index) == [minute - timedelta(minutes=1), minute]
            connected(second, minute + timedelta(seconds=71))
            await beat(second, minute + timedelta(seconds=75))  # the REST candles
            await beat(second, minute + timedelta(minutes=3, seconds=1))  # Classic

        asyncio.run(second_run())
        last = rows(
            "SELECT symbol, state, ws_volume FROM market.candles WHERE series = 'last'"
            " AND open_time = %s AND symbol IN (%s, %s) ORDER BY symbol",
            minute,
            spot,
            futures,
        )
        assert last == [
            (futures, "cerrada_rest", None),  # its flow had the outage: REST alone
            (spot, "cerrada_cruzada", Decimal(2)),  # cross-checked with the stored candle
        ]
        index = rows(
            f"SELECT {HHMM}, evidence FROM market.candles WHERE series = 'index'"
            " AND symbol = %s AND open_time <= %s ORDER BY open_time",
            futures,
            minute,
        )
        assert index == [("23:57", "reconstruida_classic"), ("23:58", "reconstruida_classic")]

        third = Recorder(sink, rest=rest)
        asyncio.run(third.startup(minute + timedelta(minutes=3, seconds=5)))
        assert third.core is not None
        for market, symbol in (("SPOT", spot), ("FUTURES", futures)):
            assert [m for m in third.core.awaiting_minutes(market, symbol) if m <= minute] == []
        assert [m for _, m in third._index if m <= minute] == [], "nothing is asked twice"


def test_a_recoverable_candle_keeps_its_day_open(
    connect: Connect, test_client: Factory, free_day: FreeDay
) -> None:
    """Review of E2, second round, in PostgreSQL: KuCoin's REST does not answer for the
    contract six times in a row (network errors). The spot day is closed on time; the
    contract's day gets no terminal status while its candles can be recovered. After a
    restart within 48 h REST answers: the candle of 23:58 is cross-checked with the stored
    WebSocket candle, the day is closed with it, and a third start changes nothing."""
    day = free_day()
    midnight = day + timedelta(days=1)
    start = midnight - timedelta(minutes=2, seconds=30)
    minute = midnight - timedelta(minutes=2)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"
    fake = FakeKucoin(
        spot=[spot], futures=[futures], candles=rest_candles_until(midnight + timedelta(hours=1))
    )
    fake.unreachable = {futures}
    # 23:58 is first asked at 00:00:02, then again 1, 2, 4, 8 and 15 minutes later.
    steps = [minute + timedelta(seconds=62), midnight + timedelta(seconds=2)]
    steps += [midnight + timedelta(seconds=16)]
    steps += [midnight + timedelta(minutes=m, seconds=2) for m in (1, 3, 7, 15, 30)]

    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        rest = test_client(fake, RestRateLimiter(pool_weight=10**6))

        def statuses(symbol: str) -> object:
            return conn.execute(
                "SELECT count(*) FROM market.minute_status WHERE symbol = %s"
                " AND minute_start >= %s AND minute_start < %s",
                (symbol, day, midnight),
            ).fetchone()

        first = Recorder(sink, rest=rest)

        async def first_run() -> None:
            await first.startup(start)
            assert first.core is not None
            for market in ("SPOT", "FUTURES"):
                first.core.on_state(market, "connected", start + timedelta(seconds=1), "wss://")
            feed_minute(first.core, minute, spot, futures)
            for moment in steps:
                await beat(first, moment)

        asyncio.run(first_run())
        assert statuses(spot) == (1440,)
        assert statuses(futures) == (0,), "no terminal status while it can be recovered"

        fake.unreachable = set()  # the network is back
        second = Recorder(sink, rest=rest)

        async def second_run() -> None:
            await second.startup(midnight + timedelta(hours=9))
            await beat(second, midnight + timedelta(hours=9, seconds=1))

        asyncio.run(second_run())
        assert conn.execute(
            "SELECT c.state, c.ws_volume, s.status FROM market.candles c"
            " JOIN market.minute_status s ON s.market = c.market AND s.symbol = c.symbol"
            " AND s.minute_start = c.open_time"
            " WHERE c.series = 'last' AND c.symbol = %s AND c.open_time = %s",
            (futures, minute),
        ).fetchall() == [("cerrada_cruzada", Decimal(2), "datos")]
        assert statuses(futures) == (1440,)

        third = Recorder(sink, rest=rest)

        async def third_run() -> None:
            await third.startup(midnight + timedelta(hours=9, minutes=5))
            await beat(third, midnight + timedelta(hours=9, minutes=5, seconds=1))

        asyncio.run(third_run())
        assert third.core is not None
        assert [m for m in third.core.awaiting_minutes("FUTURES", futures) if m < midnight] == []
        assert (statuses(spot), statuses(futures)) == ((1440,), (1440,))


def day_statuses(conn: psycopg.Connection, symbol: str, day: datetime) -> object:
    return conn.execute(
        "SELECT count(*) FROM market.minute_status WHERE symbol = %s"
        " AND minute_start >= %s AND minute_start < %s",
        (symbol, day, day + timedelta(days=1)),
    ).fetchone()


def test_a_day_left_open_is_closed_after_a_restart_beyond_48_hours(
    connect: Connect, test_client: Factory, free_day: FreeDay
) -> None:
    """Review of E2, third round (I-E2-06), in PostgreSQL: the contract's day stays open
    because KuCoin never answers for its candles, and the process starts again three days
    later. Its minutes are past the 48 h and are not asked for again, but the day is found
    in the database and closed as it is; another start changes nothing."""
    day = free_day()
    midnight = day + timedelta(days=1)
    start = midnight - timedelta(minutes=2, seconds=30)
    minute = midnight - timedelta(minutes=2)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"
    fake = FakeKucoin(
        spot=[spot], futures=[futures], candles=rest_candles_until(midnight + timedelta(hours=1))
    )
    fake.unreachable = {futures}
    later = midnight + timedelta(days=3, hours=1)

    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        rest = test_client(fake, RestRateLimiter(pool_weight=10**6))
        first = Recorder(sink, rest=rest)

        async def first_run() -> None:
            await first.startup(start)
            assert first.core is not None
            for market in ("SPOT", "FUTURES"):
                first.core.on_state(market, "connected", start + timedelta(seconds=1), "wss://")
            feed_minute(first.core, minute, spot, futures)
            for moment in (minute + timedelta(seconds=62), midnight + timedelta(seconds=16)):
                await beat(first, moment)

        asyncio.run(first_run())
        assert (day_statuses(conn, spot, day), day_statuses(conn, futures, day)) == ((1440,), (0,))

        for moment in (later, later + timedelta(minutes=5)):  # two starts three days later
            restarted = Recorder(sink, rest=rest)

            async def run_again(at: datetime = moment, recorder: Recorder = restarted) -> None:
                await recorder.startup(at)
                await beat(recorder, at + timedelta(seconds=1))

            asyncio.run(run_again())
            assert day_statuses(conn, futures, day) == (1440,)
        assert conn.execute(
            "SELECT status, candle_state FROM market.minute_status"
            " WHERE symbol = %s AND minute_start = %s",
            (futures, minute),
        ).fetchall() == [("sin_explicar", "pendiente_estabilizacion")], "no candle: as it is"


def test_a_late_disconnection_never_contradicts_a_valid_slot(
    connect: Connect, test_client: Factory, free_day: FreeDay
) -> None:
    """Review of E2, third round (I-E2-03), in PostgreSQL: the last message arrives at
    12:00:01 and the stream notices the dead connection at 12:00:24, dating the outage from
    that last proof of life. The slot [12:00:00, 12:00:10) is not written before the outage
    is known, and then only as desconectado, without metrics."""
    start = free_day() + timedelta(hours=12)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"
    fake = FakeKucoin(spot=[spot], futures=[futures])

    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        recorder = Recorder(sink, rest=test_client(fake, RestRateLimiter(pool_weight=10**6)))

        async def scenario() -> None:
            await recorder.startup(start)
            core = recorder.core
            assert core is not None
            core.on_state("SPOT", "connected", start, "wss://")
            last = start + timedelta(seconds=1)  # the last message ever received
            core.on_message("SPOT", book("SPOT", spot, 1, last), last)
            await recorder.step(start + timedelta(seconds=12))  # no proof of life after it
            core.on_state(
                "SPOT", "disconnected", start + timedelta(seconds=1), "ProtocolViolation: no pong"
            )
            await recorder.step(start + timedelta(seconds=24))

        asyncio.run(scenario())
        slots = conn.execute(
            "SELECT s.slot_start, s.state, m.slot_start IS NOT NULL"
            " FROM market.depth_slot_status s LEFT JOIN market.depth_metrics m"
            " USING (market, symbol, depth, slot_start)"
            " WHERE s.symbol = %s AND s.depth = 5 ORDER BY s.slot_start",
            (spot,),
        ).fetchall()
        assert slots == [
            (start, "desconectado", False),
            (start + timedelta(seconds=10), "desconectado", False),
        ]


def test_the_day_waits_for_the_proof_of_life_of_its_last_minute(
    connect: Connect, test_client: Factory, free_day: FreeDay
) -> None:
    """Review of E2, fourth round (R4-E2-01), in PostgreSQL, the reviewer's sequence on a
    quiet connection: the trade and the candle of 23:59 arrive, a pong at 00:00:00 and the
    next one at 00:00:18, with no pong in between. At 00:00:15 the day is not closed; after
    the pong of 00:00:18 the flow S1, the candle and the status datos are stored without any
    error, and a restart keeps them."""
    day = free_day()
    midnight = day + timedelta(days=1)
    start = midnight - timedelta(minutes=2, seconds=30)
    minute = midnight - timedelta(minutes=1)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"
    fake = FakeKucoin(spot=[spot], futures=[futures], candles=rest_candles_until(midnight))

    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        rest = test_client(fake, RestRateLimiter(pool_weight=10**6))
        recorder = Recorder(sink, rest=rest)

        async def scenario() -> None:
            await recorder.startup(start)
            core = recorder.core
            assert core is not None
            for market in ("SPOT", "FUTURES"):
                core.on_state(market, "connected", start + timedelta(seconds=1), "wss://")
            await beat(recorder, minute)  # 23:59:00: tomorrow's universe
            feed_minute(core, minute, spot, futures)
            await beat(recorder, midnight)  # the pong at 00:00:00
            await recorder.step(midnight + timedelta(seconds=15))  # no pong since then
            assert day_statuses(conn, spot, day) == (0,), "23:59 waits for its proof of life"
            await beat(recorder, midnight + timedelta(seconds=18))  # the next pong

        asyncio.run(scenario())

        def last_minute(symbol: str) -> list[tuple[object, ...]]:
            return conn.execute(
                "SELECT q.flow_state, c.state, s.status FROM market.data_quality_minute q"
                " JOIN market.candles c ON c.series = 'last' AND c.market = q.market"
                " AND c.symbol = q.symbol AND c.open_time = q.minute_start"
                " JOIN market.minute_status s ON s.market = q.market AND s.symbol = q.symbol"
                " AND s.minute_start = q.minute_start"
                " WHERE q.channel = 'trade' AND q.symbol = %s AND q.minute_start = %s",
                (symbol, minute),
            ).fetchall()

        for symbol in (spot, futures):
            assert last_minute(symbol) == [("S1", "cerrada_cruzada", "datos")]
            assert day_statuses(conn, symbol, day) == (1440,)

        restarted = Recorder(sink, rest=rest)

        async def again() -> None:
            await restarted.startup(midnight + timedelta(minutes=1))
            await beat(restarted, midnight + timedelta(minutes=1, seconds=1))

        asyncio.run(again())
        for symbol in (spot, futures):
            assert last_minute(symbol) == [("S1", "cerrada_cruzada", "datos")]
            assert day_statuses(conn, symbol, day) == (1440,)


def test_a_late_book_is_stored_and_its_slot_never_counts_as_valid(
    connect: Connect, test_client: Factory, free_day: FreeDay
) -> None:
    """Review of E2, fourth round (I-E2-04), in PostgreSQL, the reviewer's sequence: a pong
    at 12:00:12 closes [12:00:00, 12:00:10) as valid, a book published at 12:00:09.5 arrives
    at 12:00:13 and a correct one at 12:00:14, before the next close. The rows written stay
    as they are; the late book is stored, and the first slot counts as fuera_de_plazo."""
    start = free_day() + timedelta(hours=12)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"
    fake = FakeKucoin(spot=[spot], futures=[futures])

    def moment(s: float) -> datetime:
        return start + timedelta(seconds=s)

    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        recorder = Recorder(sink, rest=test_client(fake, RestRateLimiter(pool_weight=10**6)))

        async def scenario() -> None:
            await recorder.startup(start)
            core = recorder.core
            assert core is not None
            core.on_state("SPOT", "connected", start, "wss://")
            core.on_message("SPOT", book("SPOT", spot, 1, moment(1)), moment(1))
            await beat(recorder, moment(12))
            core.on_message("SPOT", book("SPOT", spot, 2, moment(9.5)), moment(13))
            core.on_message("SPOT", book("SPOT", spot, 3, moment(14)), moment(14))
            await beat(recorder, moment(22))

        asyncio.run(scenario())
        written = conn.execute(
            "SELECT slot_start, state, sequence FROM market.depth_slot_status"
            " WHERE symbol = %s AND depth = 5 ORDER BY slot_start",
            (spot,),
        ).fetchall()
        assert written == [(start, "valida", 1), (moment(10), "valida", 3)]
        late = conn.execute(
            "SELECT depth, slot_start, last_slot_start, sequence, published_at, received_at"
            " FROM market.depth_late_books WHERE symbol = %s",
            (spot,),
        ).fetchall()
        assert late == [(5, start, start, 2, moment(9.5), moment(13))]
        assert store.slot_states(conn, "SPOT", spot, 5, start, moment(20)) == {
            start: "fuera_de_plazo",
            moment(10): "valida",
        }


def test_trading_seen_late_is_stored_and_never_left_as_a_probable_absence(
    connect: Connect, test_client: Factory, free_day: FreeDay
) -> None:
    """Reviews of E2, fourth and fifth rounds, in PostgreSQL. Nothing is traded on the
    WebSocket from 23:58 on, so 23:58 and 23:59 close as S1 with neither a trade nor a
    candle. KuCoin's REST gives a flat zero-volume candle in Spot and none in Futures, as it
    does without trades [H, 8 Nov 2026]. A trade of 23:58 arrives in Spot before its candle
    is decided: the candle is discrepante. A candle of 23:59 arrives in Spot, and a trade of
    23:59 in Futures, after the day was closed: they are kept, the rows stay as written, and
    the minutes count as discrepante and sin_explicar."""
    day = free_day()
    midnight = day + timedelta(days=1)
    start = midnight - timedelta(minutes=2, seconds=30)
    first = midnight - timedelta(minutes=3)  # 23:57, recorded only in part
    m58, m59 = midnight - timedelta(minutes=2), midnight - timedelta(minutes=1)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"

    def answer(market: str, symbol: str, begin: int, end: int) -> list[list[object]]:
        minutes = range(end - end % 60, begin - begin % 60, -60)
        if market == "SPOT":
            return [
                [s, "100", "100", "100", "100", "0", "0"] for s in minutes if s < seconds(midnight)
            ]
        return [rest_row(first)] if seconds(first) in minutes else []

    fake = FakeKucoin(spot=[spot], futures=[futures], candles=answer)

    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        recorder = Recorder(sink, rest=test_client(fake, RestRateLimiter(pool_weight=10**6)))

        async def scenario() -> None:
            await recorder.startup(start)
            core = recorder.core
            assert core is not None
            for market in ("SPOT", "FUTURES"):
                core.on_state(market, "connected", start + timedelta(seconds=1), "wss://")
            await beat(recorder, m59 + timedelta(seconds=2))  # 23:58 closed, nothing seen
            late = m58 + timedelta(seconds=59.5)
            core.on_message("SPOT", trade("SPOT", spot, late), m59 + timedelta(seconds=3))
            await beat(recorder, m59 + timedelta(seconds=16))  # 23:58 decided with it
            await beat(recorder, midnight + timedelta(seconds=16))  # 23:59; the day closed
            after = midnight + timedelta(seconds=20)
            core.on_message("SPOT", kline("SPOT", spot, m59, after), after)
            core.on_message("FUTURES", trade("FUTURES", futures, m59), after)
            await beat(recorder, midnight + timedelta(seconds=21))

        asyncio.run(scenario())
        seen = conn.execute(
            "SELECT symbol, minute_start FROM market.trading_seen_late"
            " WHERE symbol IN (%s, %s) ORDER BY symbol, minute_start",
            (spot, futures),
        ).fetchall()
        assert seen == [(futures, m59), (spot, m58)]
        late_candles = conn.execute(
            "SELECT symbol, open_time FROM market.late_ws_candles WHERE symbol IN (%s, %s)",
            (spot, futures),
        ).fetchall()
        assert late_candles == [(spot, m59)]
        written = dict(
            conn.execute(
                "SELECT symbol || ' ' || to_char(minute_start AT TIME ZONE 'UTC', 'HH24:MI'),"
                " status || ' ' || candle_state FROM market.minute_status"
                " WHERE symbol IN (%s, %s) AND minute_start >= %s AND minute_start < %s",
                (spot, futures, first, midnight),
            ).fetchall()
        )
        assert written == {
            f"{spot} 23:57": "datos cerrada_rest",
            f"{spot} 23:58": "datos discrepante",
            f"{spot} 23:59": "datos cerrada_cruzada",
            f"{futures} 23:57": "datos cerrada_rest",
            f"{futures} 23:58": "sin_operaciones_probable ausencia_sin_trades_probable",
            f"{futures} 23:59": "sin_operaciones_probable ausencia_sin_trades_probable",
        }
        spot_day = store.minute_statuses(conn, "SPOT", spot, day)
        futures_day = store.minute_statuses(conn, "FUTURES", futures, day)
        assert (spot_day[m58], spot_day[m59]) == (("datos", "discrepante"),) * 2
        assert futures_day[m58] == ("sin_operaciones_probable", "ausencia_sin_trades_probable")
        assert futures_day[m59] == ("sin_explicar", "pendiente_estabilizacion")


LATE_STEPS = [timedelta(minutes=k, seconds=16) for k in range(5)]  # 00:00:16 to 00:04:16


def late_evidence_run(
    connect: Connect,
    test_client: Factory,
    free_day: FreeDay,
    channel: str,
    rest: str,
    when: str,
    restart: bool,
) -> tuple[object, ...]:
    """23:59 closes as S1 with neither a trade nor a candle at 00:00:02. A trade or a candle
    of 23:59 arrives before its REST candle is decided (00:00:07) or after the day was
    closed (00:05:00), on the same connection or on the one opened after a restart just
    before. KuCoin's REST answers with no candle (``empty``) or with a flat one of volume 0
    (``zero``) in Spot; the contract always gets its candles. Returns what 23:59 counts as
    and the late rows kept for it."""
    day = free_day()
    midnight = day + timedelta(days=1)
    minute = midnight - timedelta(minutes=1)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"

    def answer(market: str, symbol: str, begin: int, end: int) -> list[list[object]]:
        wanted = range(end - end % 60, begin - begin % 60, -60)
        minutes = [s for s in wanted if s < seconds(midnight)]
        if market == "FUTURES":
            return [rest_row(datetime.fromtimestamp(s, UTC)) for s in minutes]
        if rest == "zero":
            return [[s, "100", "100", "100", "100", "0", "0"] for s in minutes]
        return []

    fake = FakeKucoin(spot=[spot], futures=[futures], candles=answer)
    late_at = midnight + (timedelta(seconds=7) if when == "before" else timedelta(minutes=5))
    message = (
        trade("SPOT", spot, minute + timedelta(seconds=59.5))
        if channel == "trade"
        else kline("SPOT", spot, minute, late_at)
    )

    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        rest_client = test_client(fake, RestRateLimiter(pool_weight=10**6))
        recorder = Recorder(sink, rest=rest_client)

        def connect_both(moment: datetime) -> None:
            assert recorder.core is not None
            for market in ("SPOT", "FUTURES"):
                recorder.core.on_state(market, "connected", moment, "wss://")

        async def start(moment: datetime) -> None:
            nonlocal recorder
            recorder = Recorder(sink, rest=rest_client)
            await recorder.startup(moment)
            connect_both(moment + timedelta(seconds=1))

        def deliver() -> None:
            assert recorder.core is not None
            recorder.core.on_message("SPOT", message, late_at)

        async def scenario() -> None:
            await start(midnight - timedelta(seconds=90))
            await beat(recorder, minute)
            await beat(recorder, midnight + timedelta(seconds=2))  # 23:59 closed: nothing seen
            if when == "before":
                if restart:
                    await start(midnight + timedelta(seconds=5))
                deliver()
            for step in LATE_STEPS:  # the day is closed by the last one
                await beat(recorder, midnight + step)
            if when == "after":
                if restart:
                    await start(midnight + timedelta(minutes=4, seconds=30))
                deliver()
                await beat(recorder, late_at + timedelta(seconds=1))

        asyncio.run(scenario())
        assert day_statuses(conn, spot, day) == (1440,)

        def count(table: str, column: str) -> object:
            return conn.execute(
                f"SELECT count(*) FROM market.{table} WHERE symbol = %s AND {column} = %s",
                (spot, minute),
            ).fetchone()

        return (
            store.minute_statuses(conn, "SPOT", spot, day)[minute],
            count("trading_seen_late", "minute_start"),
            count("late_ws_candles", "open_time"),
        )


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("rest", ["empty", "zero"])
@pytest.mark.parametrize("channel", ["trade", "kline"])
def test_late_evidence_counts_the_same_after_a_restart(
    connect: Connect, test_client: Factory, free_day: FreeDay, channel: str, rest: str, when: str
) -> None:
    """Review of E2, fifth round (R5-E2-01), in PostgreSQL: a trade or a candle of a minute
    closed as S1 with nothing seen, received after a restart, before the REST decision or
    after the day close, with no REST candle or a zero-volume one. What it changes is judged
    from the database, so the minute counts the same as without the restart: sin_explicar
    (still pending) without a REST candle, discrepante against a zero-volume one."""
    results = [
        late_evidence_run(connect, test_client, free_day, channel, rest, when, restart)
        for restart in (False, True)
    ]
    assert results[0] == results[1], "the same with and without the restart"
    effective, trades, candles = results[0]
    assert (trades, candles) == (((1,), (0,)) if channel == "trade" else ((0,), (1,)))
    expected = (
        ("datos", "discrepante") if rest == "zero" else ("sin_explicar", "pendiente_estabilizacion")
    )
    assert effective == expected


def closing(market: str, symbol: str, minute: datetime, at: datetime, close: str) -> dict[str, Any]:
    """The WebSocket candle of ``minute`` published at ``at``, with its close set."""
    message = kline(market, symbol, minute, at)
    message["d"]["c"] = close
    return message


def answered_up_to(midnight: datetime, during: Callable[[], None] | None = None) -> Any:
    """REST candles (close 100.5) for every minute before ``midnight``; ``during`` runs
    while KuCoin's answer is awaited, as a message arriving on the WebSocket meanwhile."""

    def answer(market: str, symbol: str, begin: int, end: int) -> list[list[object]]:
        if during is not None:
            during()
        wanted = range(end - end % 60, begin - begin % 60, -60)
        return [rest_row(datetime.fromtimestamp(s, UTC)) for s in wanted if s < seconds(midnight)]

    return answer


def test_a_candle_that_arrives_during_the_rest_answer_is_never_used_unstored(
    connect: Connect, test_client: Factory, free_day: FreeDay
) -> None:
    """Review of E2, sixth round (R6-E2-01), in PostgreSQL: the WebSocket candle of 23:59
    stored with its flow closes at 100.1; another one, closing at 100.5 like the REST
    candle, arrives while KuCoin's REST answer is awaited, and the process stops right
    after the decision, before the next tick. The decision used only what the database
    holds, so after a restart the database explains it: discrepante, with no late candle
    behind it, and counted as written."""
    day = free_day()
    midnight = day + timedelta(days=1)
    minute = midnight - timedelta(minutes=1)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"
    meanwhile: list[Recorder] = []
    arrival = midnight + timedelta(seconds=16)

    def deliver_meanwhile() -> None:
        if meanwhile and meanwhile[0].core is not None:
            late = closing("SPOT", spot, minute, arrival, "100.5")
            meanwhile[0].core.on_message("SPOT", late, arrival)
            meanwhile.clear()  # once

    fake = FakeKucoin(
        spot=[spot], futures=[futures], candles=answered_up_to(midnight, deliver_meanwhile)
    )

    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        rest = test_client(fake, RestRateLimiter(pool_weight=10**6))
        recorder = Recorder(sink, rest=rest)

        async def first_run() -> None:
            await recorder.startup(midnight - timedelta(seconds=90))
            core = recorder.core
            assert core is not None
            for market in ("SPOT", "FUTURES"):
                core.on_state(market, "connected", midnight - timedelta(seconds=89), "wss://")
            await beat(recorder, minute)
            moment = minute + timedelta(seconds=30)
            core.on_message("SPOT", trade("SPOT", spot, moment), moment)
            core.on_message("SPOT", closing("SPOT", spot, minute, moment, "100.1"), moment)
            await beat(recorder, midnight + timedelta(seconds=2))  # 23:59 and its candle stored
            meanwhile.append(recorder)
            await beat(recorder, arrival)  # the REST decision; then the process stops

        asyncio.run(first_run())
        again = Recorder(sink, rest=rest)

        async def second_run() -> None:
            await again.startup(midnight + timedelta(seconds=30))
            await beat(again, midnight + timedelta(seconds=31))

        asyncio.run(second_run())
        decided = conn.execute(
            "SELECT c.state, c.ws_volume, w.close FROM market.candles c"
            " JOIN market.ws_candles w ON w.market = c.market AND w.symbol = c.symbol"
            " AND w.open_time = c.open_time"
            " WHERE c.series = 'last' AND c.symbol = %s AND c.open_time = %s",
            (spot, minute),
        ).fetchall()
        assert decided == [("discrepante", Decimal(2), Decimal("100.1"))], "the stored candle"
        assert conn.execute(
            "SELECT count(*) FROM market.late_ws_candles WHERE symbol = %s", (spot,)
        ).fetchone() == (0,), "never stored: the decision did not use it"
        assert store.minute_statuses(conn, "SPOT", spot, day)[minute] == ("datos", "discrepante")


@pytest.mark.parametrize("restart", [False, True])
def test_an_older_candle_never_replaces_one_sent_again(
    connect: Connect, test_client: Factory, free_day: FreeDay, restart: bool
) -> None:
    """Review of E2, sixth round (R6-E2-02), in PostgreSQL: candle A of 23:59 is stored with
    its flow and sent again, the same, after the minute closed (only its time is kept);
    then B, published before that and with another close, arrives. B is older than the
    latest time A was seen, so it is not kept, with or without a restart in between, and
    23:59 is decided with A: cerrada_cruzada."""
    day = free_day()
    midnight = day + timedelta(days=1)
    minute = midnight - timedelta(minutes=1)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"
    fake = FakeKucoin(spot=[spot], futures=[futures], candles=answered_up_to(midnight))

    def moment(s: float) -> datetime:
        return midnight + timedelta(seconds=s)

    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        rest = test_client(fake, RestRateLimiter(pool_weight=10**6))
        recorder = Recorder(sink, rest=rest)

        async def scenario() -> None:
            nonlocal recorder
            await recorder.startup(midnight - timedelta(seconds=90))
            assert recorder.core is not None
            for market in ("SPOT", "FUTURES"):
                recorder.core.on_state(market, "connected", moment(-89), "wss://")
            await beat(recorder, minute)
            in_time = minute + timedelta(seconds=30)
            recorder.core.on_message("SPOT", trade("SPOT", spot, in_time), in_time)
            recorder.core.on_message("SPOT", kline("SPOT", spot, minute, in_time), in_time)
            await beat(recorder, moment(2))  # 23:59 and candle A stored
            recorder.core.on_message("SPOT", kline("SPOT", spot, minute, moment(5)), moment(5))
            await beat(recorder, moment(6))  # A sent again: only its time is kept
            if restart:
                recorder = Recorder(sink, rest=rest)
                await recorder.startup(moment(7))
                assert recorder.core is not None
                for market in ("SPOT", "FUTURES"):
                    recorder.core.on_state(market, "connected", moment(7.5), "wss://")
            assert recorder.core is not None
            older = closing("SPOT", spot, minute, moment(4), "100.9")  # B, published at :04
            recorder.core.on_message("SPOT", older, moment(8))
            await beat(recorder, moment(9))
            await beat(recorder, moment(16))  # the REST candle, close 100.5 like A

        asyncio.run(scenario())

        def count(table: str) -> object:
            return conn.execute(
                f"SELECT count(*) FROM market.{table} WHERE symbol = %s", (spot,)
            ).fetchone()

        assert (count("late_ws_candles"), count("late_ws_repeats")) == ((0,), (1,))
        assert conn.execute(
            "SELECT state FROM market.candles WHERE series = 'last' AND symbol = %s"
            " AND open_time = %s",
            (spot, minute),
        ).fetchall() == [("cerrada_cruzada",)]
        statuses = store.minute_statuses(conn, "SPOT", spot, day)
        assert statuses[minute] == ("datos", "cerrada_cruzada")


def flat_candle(
    market: str, symbol: str, minute: datetime, published: datetime, volume: str
) -> dict[str, Any]:
    """A WebSocket candle of ``minute`` at a constant price of 100, published at ``published``."""
    message = kline(market, symbol, minute, published)
    message["d"].update(o="100", h="100", l="100", c="100", v=volume, a=str(Decimal(volume) * 100))
    return message


# Review of E2, seventh round (R6-E2-03): (seconds after 23:59:00 when received, when
# published, volume); a candle received after 60 s arrives once 23:59 is closed.
SEQUENCES = {
    "replaced before the close": [(30, 30, "1"), (50, 50, "0")],
    "older, before the close": [(50, 50, "0"), (55, 30, "1")],
    "older, after the close": [(50, 50, "0"), (63, 30, "1")],
}


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("sequence", sorted(SEQUENCES))
def test_trading_shown_by_an_older_or_replaced_candle_is_never_lost(
    connect: Connect, test_client: Factory, free_day: FreeDay, sequence: str, restart: bool
) -> None:
    """Review of E2, seventh round (R6-E2-03), in PostgreSQL: flow S1, no trade, a constant
    price of 100; a candle of 23:59 with volume 1 and one of volume 0 published later. The
    candle with volume is replaced before the close, arrives after the other one, or
    arrives after the close; with or without a restart before the REST answer. The latest
    candle, for comparing prices, is the one of volume 0, and the trading seen is never
    lost: against KuCoin's zero-volume REST candle, 23:59 is datos/discrepante."""
    day = free_day()
    midnight = day + timedelta(days=1)
    minute = midnight - timedelta(minutes=1)
    spot = "S" + secrets.token_hex(4).upper() + "-USDT"
    futures = "F" + secrets.token_hex(4).upper() + "USDTM"

    def answer(market: str, symbol: str, begin: int, end: int) -> list[list[object]]:
        wanted = range(end - end % 60, begin - begin % 60, -60)
        minutes = [s for s in wanted if s < seconds(midnight)]
        if market == "FUTURES":
            return [rest_row(datetime.fromtimestamp(s, UTC)) for s in minutes]
        return [[s, "100", "100", "100", "100", "0", "0"] for s in minutes]

    fake = FakeKucoin(spot=[spot], futures=[futures], candles=answer)

    with connect("kbot_ingest") as conn:
        sink = DatabaseSink(conn)
        rest = test_client(fake, RestRateLimiter(pool_weight=10**6))
        recorder = Recorder(sink, rest=rest)

        async def start(at: datetime) -> None:
            nonlocal recorder
            recorder = Recorder(sink, rest=rest)
            await recorder.startup(at)
            assert recorder.core is not None
            for market in ("SPOT", "FUTURES"):
                recorder.core.on_state(market, "connected", at + timedelta(seconds=1), "wss://")

        async def scenario() -> None:
            await start(midnight - timedelta(seconds=90))
            await beat(recorder, minute)
            closed = False
            for received, published, volume in SEQUENCES[sequence]:
                if received > 60 and not closed:
                    await beat(recorder, midnight + timedelta(seconds=2))  # 23:59 closed
                    closed = True
                at = minute + timedelta(seconds=received)
                message = flat_candle(
                    "SPOT", spot, minute, minute + timedelta(seconds=published), volume
                )
                assert recorder.core is not None
                recorder.core.on_message("SPOT", message, at)
            await beat(recorder, midnight + timedelta(seconds=4))
            if restart:
                await start(midnight + timedelta(seconds=5))
            await beat(recorder, midnight + timedelta(seconds=16))  # KuCoin: volume 0

        asyncio.run(scenario())
        decided = conn.execute(
            "SELECT c.state, c.ws_volume, w.traded FROM market.candles c"
            " JOIN market.ws_candles w ON w.market = c.market AND w.symbol = c.symbol"
            " AND w.open_time = c.open_time"
            " WHERE c.series = 'last' AND c.symbol = %s AND c.open_time = %s",
            (spot, minute),
        ).fetchall()
        in_time = sequence != "older, after the close"
        assert decided == [("discrepante", Decimal(0), in_time)], "compared with the latest"
        late = conn.execute(
            "SELECT count(*) FROM market.late_ws_candles WHERE symbol = %s", (spot,)
        ).fetchone()
        assert late == ((0,) if in_time else (1,))
        assert store.minute_statuses(conn, "SPOT", spot, day)[minute] == ("datos", "discrepante")
