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

import psycopg
import pytest

from kbot.ingest.service import Recorder
from kbot.ingest.sinks import DatabaseSink
from kbot.kucoin.ratelimit import RestRateLimiter
from kbot.kucoin.rest import KucoinRestClient
from tests.unit.ingest.fake_kucoin import FakeKucoin
from tests.unit.ingest.messages import feed_minute, rest_candles_until

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]
Factory = Callable[..., KucoinRestClient]
HHMM = "to_char(open_time AT TIME ZONE 'UTC', 'HH24:MI')"


def test_the_recorder_writes_a_consistent_day_and_resumes(
    connect: Connect, test_client: Factory
) -> None:
    # A day and symbols of their own, before any other test's universe, so the universe in
    # force on the days before is empty and no other test shares these rows.
    day = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=secrets.randbelow(2000))
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
            await recorder.step(midnight - timedelta(minutes=2))
            await recorder.step(midnight - timedelta(minutes=1))
            feed_minute(core, midnight - timedelta(minutes=1), spot, futures)
            core.on_state(
                "FUTURES",
                "disconnected",
                midnight - timedelta(seconds=40),
                "ConnectionClosedError: no close frame received or sent",
            )
            core.on_state("FUTURES", "connected", midnight - timedelta(seconds=35), "wss://...")
            await recorder.step(midnight - timedelta(seconds=44))
            await recorder.step(midnight)
            await recorder.step(midnight + timedelta(seconds=16))
            await recorder.step(midnight + timedelta(minutes=2, seconds=1))

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
            await restarted.step(restart + timedelta(seconds=30))

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


def test_pending_work_survives_restarts(connect: Connect, test_client: Factory) -> None:
    """Audit E2-01 and E2-02 in PostgreSQL: the process stops after minute 23:58 is written,
    before its REST candle and its index rebuild are asked for. The next run resumes both
    from the database alone, decides the candle from the stored WebSocket candle and
    rebuilds the index from Classic; a third run finds nothing of them left to do."""
    day = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=secrets.randbelow(2000))
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
            await first.step(minute + timedelta(seconds=62))  # 23:58 written, nothing asked

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
            await second.step(minute + timedelta(seconds=75))  # the REST candles
            await second.step(minute + timedelta(minutes=3, seconds=1))  # Classic

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


def test_a_recoverable_candle_keeps_its_day_open(connect: Connect, test_client: Factory) -> None:
    """Review of E2, second round, in PostgreSQL: KuCoin's REST does not answer for the
    contract six times in a row (network errors). The spot day is closed on time; the
    contract's day gets no terminal status while its candles can be recovered. After a
    restart within 48 h REST answers: the candle of 23:58 is cross-checked with the stored
    WebSocket candle, the day is closed with it, and a third start changes nothing."""
    day = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=secrets.randbelow(2000))
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
                await first.step(moment)

        asyncio.run(first_run())
        assert statuses(spot) == (1440,)
        assert statuses(futures) == (0,), "no terminal status while it can be recovered"

        fake.unreachable = set()  # the network is back
        second = Recorder(sink, rest=rest)

        async def second_run() -> None:
            await second.startup(midnight + timedelta(hours=9))
            await second.step(midnight + timedelta(hours=9, seconds=1))

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
            await third.step(midnight + timedelta(hours=9, minutes=5, seconds=1))

        asyncio.run(third_run())
        assert third.core is not None
        assert [m for m in third.core.awaiting_minutes("FUTURES", futures) if m < midnight] == []
        assert (statuses(spot), statuses(futures)) == ((1440,), (1440,))
