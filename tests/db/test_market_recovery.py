"""Acceptance F2-A5 in a real PostgreSQL, written as kbot_ingest.

A run records depth slots, the process stops, and a second run resumes from what the
database says it recorded: the stop is a gap with cause ``proceso_detenido``, every slot
of the stop is ``desconectado``, the old book is not valid again after the reconnection,
and every slot from the first run to the second has exactly one terminal state. Writing a
batch twice changes nothing.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from kbot.market.depth import DepthChannel
from kbot.market.gaps import GapCause, GapTracker
from kbot.market.store import (
    depth_channel_name,
    resume_point,
    slot_states,
    write_depth,
    write_late_books,
)

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]
T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
NONE = timedelta(0)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def book(sequence: int, published: datetime) -> dict[str, Any]:
    ns = (published - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1) * 1000
    return {
        "T": "obu.SPOT",
        "P": ns,
        "d": {
            "s": "x",
            "C": sequence,
            "O": sequence,
            "M": ns,
            "b": [["100.0", "2"], ["99.9", "1"]],
            "a": [["100.2", "1"], ["100.3", "3"]],
        },
    }


@pytest.fixture
def ingest(connect: Connect) -> Iterator[psycopg.Connection]:
    with connect("kbot_ingest") as conn:
        yield conn


def test_f2_a5_a_stop_and_a_restart(ingest: psycopg.Connection) -> None:
    symbol = "R" + secrets.token_hex(5).upper() + "-USDT"
    channel_name = depth_channel_name(5)
    assert resume_point(ingest, "SPOT", symbol, 5) is None

    # First run: subscribed at 0, one book at 1 s, recorded until 60 s, then the process stops.
    first = DepthChannel("SPOT", symbol, 5, multiplier=Decimal(1), started_at=at(0))
    gaps = GapTracker("SPOT", channel_name, symbol, started_at=at(0), resumed_from=None)
    first.connection_up(at(0))
    opening = gaps.up(at(0))
    first.on_message(book(1, at(1)), at(1))
    slots, minutes = first.close_until(at(60), NONE)
    write_depth(ingest, slots, minutes, [g for g in [opening] if g is not None])
    assert [s.state.value for s in slots] == ["valida"] * 6
    assert len(minutes) == 1

    # Second run, five minutes later: it resumes from the database, not from memory.
    resumed = resume_point(ingest, "SPOT", symbol, 5)
    assert resumed == at(60)
    second = DepthChannel("SPOT", symbol, 5, multiplier=Decimal(1), started_at=resumed)
    gaps = GapTracker("SPOT", channel_name, symbol, started_at=at(300), resumed_from=resumed)
    second.connection_up(at(302))
    stop_gap = gaps.up(at(302))
    second.on_message(book(9, at(325)), at(325))
    slots, minutes = second.close_until(at(330), NONE)
    write_depth(ingest, slots, minutes, [g for g in [stop_gap] if g is not None])
    write_depth(ingest, slots, minutes, [g for g in [stop_gap] if g is not None])  # a rerun

    rows = ingest.execute(
        "SELECT slot_start, state, sequence FROM market.depth_slot_status"
        " WHERE market = 'SPOT' AND symbol = %s AND depth = 5 ORDER BY slot_start",
        (symbol,),
    ).fetchall()
    assert [r[0] for r in rows] == [at(10 * i) for i in range(33)], "contiguous, one per slot"
    states = {r[0]: r[1] for r in rows}
    assert all(states[at(10 * i)] == "desconectado" for i in range(6, 31))  # 60 s to 310 s
    assert states[at(310)] == "no_recibida", "the old book is not valid after reconnecting"
    assert states[at(320)] == "valida"
    assert [r[2] for r in rows if r[0] == at(320)] == [9]

    gap_rows = ingest.execute(
        "SELECT start_at, end_at, cause FROM market.data_gaps"
        " WHERE market = 'SPOT' AND channel = %s AND symbol = %s ORDER BY start_at",
        (channel_name, symbol),
    ).fetchall()
    assert gap_rows == [(at(60), at(302), GapCause.PROCESO_DETENIDO.value)]

    metric_slots = ingest.execute(
        "SELECT slot_start FROM market.depth_metrics WHERE symbol = %s ORDER BY slot_start",
        (symbol,),
    ).fetchall()
    assert [r[0] for r in metric_slots] == [at(10 * i) for i in range(6)] + [at(320)]
    snapshots = ingest.execute(
        "SELECT minute_start, slot_start, sequence FROM market.depth_snapshots"
        " WHERE symbol = %s ORDER BY minute_start",
        (symbol,),
    ).fetchall()
    assert snapshots == [(at(0), at(50), 1)]


def test_a_late_book_is_kept_and_its_slots_count_as_late(ingest: psycopg.Connection) -> None:
    """Review of E2, fourth round (I-E2-04), from the database: three slots are written as
    valid, then a book published in the second one arrives. It is stored with the slots it
    contradicts; their rows and metrics stay as written, and they count as fuera_de_plazo."""
    symbol = "L" + secrets.token_hex(5).upper() + "-USDT"
    channel = DepthChannel("SPOT", symbol, 5, multiplier=Decimal(1), started_at=at(0))
    channel.connection_up(at(0))
    channel.on_message(book(1, at(1)), at(1))
    slots, minutes = channel.close_until(at(32), timedelta(seconds=2))
    write_depth(ingest, slots, minutes)
    channel.on_message(book(2, at(19.5)), at(33))  # published in [10, 20)
    write_late_books(ingest, channel.take_late_books())
    write_late_books(ingest, [])  # nothing more to store
    assert slot_states(ingest, "SPOT", symbol, 5, at(0), at(40)) == {
        at(0): "valida",
        at(10): "fuera_de_plazo",
        at(20): "fuera_de_plazo",
    }
    rows = ingest.execute(
        "SELECT s.state, m.slot_start IS NOT NULL FROM market.depth_slot_status s"
        " LEFT JOIN market.depth_metrics m USING (market, symbol, depth, slot_start)"
        " WHERE s.symbol = %s ORDER BY s.slot_start",
        (symbol,),
    ).fetchall()
    assert rows == [("valida", True)] * 3, "what was written stays as it was"
    late = ingest.execute(
        "SELECT slot_start, last_slot_start, sequence, published_at, received_at"
        " FROM market.depth_late_books WHERE symbol = %s",
        (symbol,),
    ).fetchall()
    assert late == [(at(10), at(20), 2, at(19.5), at(33))]


def test_late_evidence_is_judged_from_the_database_alone(ingest: psycopg.Connection) -> None:
    """Reviews of E2, fourth to sixth rounds, from the database: seven minutes closed as S1
    with no trade in time. Late trades and candles are handed to the database, which keeps
    what changes each minute:

    0. no candle: the trade is kept before the close, which writes sin_explicar;
    1. a zero-volume REST candle cross-checked without a WebSocket candle: kept, it counts
       as discrepante;
    2. the day closed first as a probable absence: kept after the close, it counts as
       sin_explicar;
    3. a WebSocket candle of volume 0 and a zero-volume REST candle (R5-E2-02): kept, it
       counts as discrepante;
    4. a WebSocket candle with volume: the trade changes nothing; the closing candle sent
       again keeps only its time; a later candle with another close is kept, and against
       the REST candle the minute counts as discrepante;
    5. candle A sent again at 80 s, then B published at 70 s (R6-E2-02): B is older, it is
       not kept, and the minute stays cerrada_cruzada with A;
    6. a WebSocket candle of volume 0, a late candle with volume, a late trade and a later
       candle of volume 0 (R6-E2-03): the trade is not kept, but the trading seen stays
       seen, and against a zero-volume REST candle the minute counts as discrepante.

    The rows written stay as they are, and closing again changes nothing."""
    from kbot.market.last_candles import (
        CandleState,
        FlowMinute,
        LateTrade,
        LateWsCandle,
        Ohlcv,
        WsCandle,
        decide,
    )
    from kbot.market.mark_candles import FlowState
    from kbot.market.store import (
        close_day,
        minute_statuses,
        write_flow,
        write_last_candles,
        write_late_minutes,
        write_ws_candles,
    )

    symbol = "Q" + secrets.token_hex(5).upper() + "-USDT"
    day = datetime(2026, 11, 7, tzinfo=UTC) + timedelta(days=secrets.randbelow(3000))
    m = [day + timedelta(minutes=i) for i in range(7)]
    tick = Decimal("0.1")
    ingest.execute(  # the tick size in force, which deciding a minute again needs
        "INSERT INTO market.instruments (market, symbol, observed_at, base_currency,"
        " quote_currency, settle_currency, tick_size, quantity_step, multiplier, min_quantity,"
        " min_funds, trading_status, is_trading, raw) VALUES ('SPOT', %s, %s, 'Q', 'USDT',"
        " NULL, 0.1, 0.0001, 1, 0.0001, 0.1, 'Trading', true, '{}')",
        (symbol, day - timedelta(days=1)),
    )

    def flow(minute: datetime, channel: str = "trade") -> FlowMinute:
        return FlowMinute("SPOT", symbol, channel, minute, FlowState.S1, 0, 0, 0)

    def ohlcv(minute: datetime, *values: str) -> Ohlcv:
        return Ohlcv(minute, *(Decimal(v) for v in values))

    def late(minute: datetime) -> LateTrade:
        return LateTrade("SPOT", symbol, minute, minute + timedelta(seconds=70))

    write_flow(ingest, [flow(minute, channel) for minute in m for channel in ("trade", "kline")])

    def at(minute: int, seconds: float) -> datetime:
        return m[minute] + timedelta(seconds=seconds)

    def zero(minute: int) -> Ohlcv:
        return ohlcv(m[minute], "100", "100", "100", "100", "0", "0")

    def traded(minute: int, close: str = "100.5") -> Ohlcv:
        return ohlcv(m[minute], "100", "101", "99", close, "2", "201")

    def candle(value: Ohlcv, published: datetime) -> LateWsCandle:
        return LateWsCandle("SPOT", symbol, value, published, published)

    write_ws_candles(
        ingest,
        [
            WsCandle("SPOT", symbol, zero(3), at(3, 30), False),
            WsCandle("SPOT", symbol, traded(4), at(4, 30), True),
            WsCandle("SPOT", symbol, traded(5), at(5, 30), True),
            WsCandle("SPOT", symbol, zero(6), at(6, 30), False),
        ],
    )
    decisions = [
        decide("SPOT", symbol, flow(m[1]), zero(1), None, tick),
        decide("SPOT", symbol, flow(m[3]), zero(3), zero(3), tick),
        decide("SPOT", symbol, flow(m[4]), traded(4), traded(4), tick),
        decide("SPOT", symbol, flow(m[5]), traded(5), traded(5), tick),
        decide("SPOT", symbol, flow(m[6]), zero(6), zero(6), tick),
    ]
    assert [d.state for d in decisions] == [CandleState.CERRADA_CRUZADA] * 5, "before them"
    write_last_candles(ingest, decisions)
    kept = write_late_minutes(
        ingest,
        [
            late(m[0]),
            late(m[1]),
            late(m[3]),
            late(m[4]),
            candle(traded(4), at(4, 88)),  # the same closing candle, sent again
            candle(traded(4, "100.9"), at(4, 89)),
            candle(traded(5), at(5, 80)),  # A again
            candle(traded(5, "100.9"), at(5, 70)),  # B, older than A seen again
            candle(traded(6), at(6, 63)),  # with volume
            late(m[6]),
            candle(zero(6), at(6, 65)),  # volume 0, the latest
        ],
    )
    assert len(kept) == 8, "every one except the trade of 4, B and the trade of 6"
    close_day(ingest, "SPOT", symbol, day)
    write_late_minutes(ingest, [late(m[2])])
    close_day(ingest, "SPOT", symbol, day)  # again, as after a restart

    def rows(query: str) -> list[object]:
        return [row[0] for row in ingest.execute(query, (symbol,)).fetchall()]

    assert rows(
        "SELECT minute_start FROM market.trading_seen_late WHERE symbol = %s ORDER BY 1"
    ) == [m[0], m[1], m[2], m[3]]
    assert rows("SELECT published_at FROM market.late_ws_candles WHERE symbol = %s ORDER BY 1") == [
        at(4, 89),
        at(6, 63),
        at(6, 65),
    ]
    assert rows("SELECT published_at FROM market.late_ws_repeats WHERE symbol = %s ORDER BY 1") == [
        at(4, 88),
        at(5, 80),
    ]
    written = ingest.execute(
        "SELECT status, candle_state FROM market.minute_status WHERE symbol = %s"
        " ORDER BY minute_start LIMIT 7",
        (symbol,),
    ).fetchall()
    assert written == [
        ("sin_explicar", "pendiente_estabilizacion"),
        ("datos", "cerrada_cruzada"),
        ("sin_operaciones_probable", "ausencia_sin_trades_probable"),
        ("datos", "cerrada_cruzada"),
        ("datos", "cerrada_cruzada"),
        ("datos", "cerrada_cruzada"),
        ("datos", "cerrada_cruzada"),
    ]
    effective = minute_statuses(ingest, "SPOT", symbol, day)
    assert [effective[minute] for minute in m] == [
        ("sin_explicar", "pendiente_estabilizacion"),
        ("datos", "discrepante"),
        ("sin_explicar", "pendiente_estabilizacion"),
        ("datos", "discrepante"),
        ("datos", "discrepante"),
        ("datos", "cerrada_cruzada"),  # B never became the latest candle
        ("datos", "discrepante"),  # the trading seen stays seen
    ]
    assert len(effective) == 1440
    nobody = day + timedelta(minutes=7)  # nothing was recording it: S3 at the close
    assert effective[nobody] == ("sin_explicar", "ausencia_no_verificable")


def test_f2_a6_closing_a_day_from_the_database(ingest: psycopg.Connection) -> None:
    """F2-A6: every minute of the day gets exactly one terminal status, from the database alone;
    closing again (as after a restart) changes nothing."""
    from kbot.market.gaps import DataGap
    from kbot.market.last_candles import FlowMinute, Ohlcv, decide
    from kbot.market.mark_candles import FlowState
    from kbot.market.store import close_day, write_flow, write_gap, write_last_candles

    symbol = "D" + secrets.token_hex(5).upper() + "-USDT"
    day = datetime(2026, 11, 7, tzinfo=UTC) + timedelta(days=secrets.randbelow(3000))
    m = [day + timedelta(minutes=i) for i in range(5)]

    def flow(minute: datetime, channel: str, state: FlowState, messages: int) -> FlowMinute:
        return FlowMinute("SPOT", symbol, channel, minute, state, messages, 0, 0)

    write_flow(
        ingest,
        [
            flow(m[0], "trade", FlowState.S1, 4),
            flow(m[0], "kline", FlowState.S1, 2),
            flow(m[1], "trade", FlowState.S1, 0),
            flow(m[1], "kline", FlowState.S1, 0),
            flow(m[2], "trade", FlowState.S1, 3),
            flow(m[2], "kline", FlowState.S1, 1),
        ],
    )
    candle = Ohlcv(m[0], *(Decimal(v) for v in ("100", "101", "99", "100.5", "2", "201")))
    trade_flow = flow(m[0], "trade", FlowState.S1, 4)
    write_last_candles(ingest, [decide("SPOT", symbol, trade_flow, candle, candle, Decimal("0.1"))])
    write_gap(
        ingest,
        DataGap(
            "SPOT", "trade", symbol, m[3] + timedelta(seconds=5), m[4], GapCause.DESCONEXION, None
        ),
    )

    assert close_day(ingest, "SPOT", symbol, day) == 1440
    assert close_day(ingest, "SPOT", symbol, day) == 1440  # again: nothing changes

    rows = ingest.execute(
        "SELECT minute_start, status, candle_state, flow_state, gap_start"
        " FROM market.minute_status WHERE symbol = %s ORDER BY minute_start",
        (symbol,),
    ).fetchall()
    assert len(rows) == 1440
    assert [r[1:] for r in rows[:5]] == [
        ("datos", "cerrada_cruzada", "S1", None),
        ("sin_operaciones_probable", "ausencia_sin_trades_probable", "S1", None),
        ("sin_explicar", "pendiente_estabilizacion", "S1", None),
        ("hueco", "ausencia_no_verificable", "S3", m[3] + timedelta(seconds=5)),
        ("sin_explicar", "ausencia_no_verificable", "S3", None),
    ]
    assert {r[1] for r in rows[5:]} == {"sin_explicar"}, "nothing was recording: S3, no gap"


def test_a_trade_without_a_websocket_candle_rules_out_an_absence(
    ingest: psycopg.Connection,
) -> None:
    """Review of E2, third round (I-E2-02), from the database: in minute 0 the trade channel
    received a trade, the kline channel nothing, and KuCoin gave no candle: pending, never a
    probable absence of trades. In minute 1 KuCoin gave a candle of volume 0 against a trade
    received: a discrepancy. Closed again after a restart, nothing changes."""
    from kbot.market.last_candles import FlowMinute, Ohlcv, decide
    from kbot.market.mark_candles import FlowState
    from kbot.market.store import close_day, write_flow, write_last_candles

    symbol = "T" + secrets.token_hex(5).upper() + "-USDT"
    day = datetime(2026, 11, 7, tzinfo=UTC) + timedelta(days=secrets.randbelow(3000))
    m = [day, day + timedelta(minutes=1)]

    def flow(minute: datetime, channel: str, messages: int) -> FlowMinute:
        return FlowMinute("SPOT", symbol, channel, minute, FlowState.S1, messages, 0, 0)

    write_flow(ingest, [flow(m[0], "trade", 1), flow(m[0], "kline", 0)])
    write_flow(ingest, [flow(m[1], "trade", 1), flow(m[1], "kline", 0)])
    zero = Ohlcv(m[1], *(Decimal(v) for v in ("100", "100", "100", "100", "0", "0")))
    decision = decide("SPOT", symbol, flow(m[1], "trade", 1), zero, None, Decimal("0.1"))
    assert decision.differences == ("websocket_sin_vela", "volumen_rest_cero")
    write_last_candles(ingest, [decision])
    for _ in range(2):
        close_day(ingest, "SPOT", symbol, day)
    rows = ingest.execute(
        "SELECT status, candle_state FROM market.minute_status WHERE symbol = %s"
        " ORDER BY minute_start LIMIT 2",
        (symbol,),
    ).fetchall()
    assert rows == [("sin_explicar", "pendiente_estabilizacion"), ("datos", "discrepante")]
