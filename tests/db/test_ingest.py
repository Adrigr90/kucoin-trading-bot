"""The recorder writing to a real PostgreSQL as kbot_ingest, through simulated time.

The scenario of tests/unit/ingest/test_service.py, with every write going through the
foreign keys and CHECK constraints of migration 0002, and then a restart that resumes
from what the database says (phase 2 acceptance A5, A6 and A8 together).
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

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

        # A8: one terminal state per slot, contiguous, from the start to the last closed.
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

        # A6: the day is closed, 1,440 minutes per instrument, from the database alone.
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

        # A5: the process stops after 00:02:01 and starts again at 00:05:00.
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
