"""The market schema in a real PostgreSQL 18, with real kbot_ingest sessions (02 §3.8).

Acceptance F2-A7 (kbot_ingest only reads and inserts in market) and F2-A8 (one terminal
state per depth slot), and the review of E2: the database itself refuses metrics of a slot
that is not valid (problem 2), a candle or a minute status that contradicts its flow
(problem 3), and a second terminal result for the same key (idempotence).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from psycopg import errors
from psycopg.types.json import Jsonb

from kbot.market import store
from kbot.market.mark_candles import FlowState
from tools.grant_policy import MARKET_TABLES

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]
T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def ingest(connect: Connect) -> Iterator[psycopg.Connection]:
    with connect("kbot_ingest") as conn:
        yield conn


@pytest.fixture
def symbol() -> str:
    """A fresh contract symbol per test, so tests never share rows."""
    return "T" + secrets.token_hex(6).upper()


def quality(
    conn: psycopg.Connection,
    symbol: str,
    channel: str,
    minute: datetime,
    flow: str,
    *,
    market: str = "FUTURES",
    conflict: bool = False,
) -> None:
    clean = flow == "S1"
    conn.execute(
        "INSERT INTO market.data_quality_minute (market, symbol, channel, minute_start,"
        " flow_state, messages, anomalies, sequence_breaks, reconnections)"
        " VALUES (%s, %s, %s, %s, %s, 60, %s, 0, %s)"
        + (" ON CONFLICT DO NOTHING" if conflict else ""),
        (market, symbol, channel, minute, flow, 0 if clean else 1, 0 if clean else 1),
    )


def candle(
    conn: psycopg.Connection,
    symbol: str,
    minute: datetime,
    *,
    series: str = "last",
    state: str = "cerrada_cruzada",
    evidence: str = "vela_cruzada_consistente",
    flow_channel: str | None = "trade",
    flow_state: str | None = "S1",
    samples: int | None = None,
    volume: str | None = "2",
    conflict: bool = False,
) -> None:
    conn.execute(
        "INSERT INTO market.candles (series, market, symbol, open_time, open, high, low, close,"
        " volume, turnover, ws_volume, ws_turnover, state, evidence, samples, flow_channel,"
        " flow_state)"
        " VALUES (%s, 'FUTURES', %s, %s, 100, 101, 99, 100.5, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s)" + (" ON CONFLICT DO NOTHING" if conflict else ""),
        (
            series,
            symbol,
            minute,
            volume,
            "200" if volume is not None else None,
            volume if state != "cerrada_rest" else None,
            "200" if volume is not None and state != "cerrada_rest" else None,
            state,
            evidence,
            samples,
            flow_channel,
            flow_state,
        ),
    )


def slot(
    conn: psycopg.Connection, symbol: str, start: datetime, state: str, *, conflict: bool = False
) -> None:
    has_book = state not in ("desconectado", "no_recibida")
    conn.execute(
        "INSERT INTO market.depth_slot_status (market, symbol, depth, slot_start, state,"
        " sequence, snapshot_age_ms) VALUES ('SPOT', %s, 5, %s, %s, %s, %s)"
        + (" ON CONFLICT DO NOTHING" if conflict else ""),
        (symbol, start, state, 7 if has_book else None, 1500 if has_book else None),
    )


def metrics(conn: psycopg.Connection, symbol: str, start: datetime) -> None:
    conn.execute(
        "INSERT INTO market.depth_metrics (market, symbol, depth, slot_start, best_bid,"
        " best_ask, mid, spread, bid_levels, ask_levels, bid_quantity, ask_quantity,"
        " bid_notional, ask_notional, bid_notional_10bps, ask_notional_10bps,"
        " bid_notional_50bps, ask_notional_50bps, bid_deepest_price, ask_deepest_price)"
        " VALUES ('SPOT', %s, 5, %s, 100.0, 100.2, 100.1, 0.2, 2, 2, 3, 4, 299.9, 401,"
        " 200, 100.2, 299.9, 401, 99.9, 100.3)",
        (symbol, start),
    )


# --- F2-A7: privileges in real sessions ---------------------------------------------------


@pytest.mark.parametrize("table", MARKET_TABLES)
@pytest.mark.parametrize(
    "statement",
    ["UPDATE {t} SET recorded_at = recorded_at", "DELETE FROM {t}", "TRUNCATE {t}"],
)
def test_f2_a7_kbot_ingest_can_never_change_or_delete(
    ingest: psycopg.Connection, table: str, statement: str
) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        ingest.execute(statement.format(t=table))


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM config.risk_limits",
        "CREATE TABLE market.x (a int)",
        "ALTER TABLE market.candles ADD COLUMN x int",
        "DROP TABLE market.data_gaps",
    ],
)
def test_f2_a7_kbot_ingest_has_nothing_outside_its_rows(
    ingest: psycopg.Connection, statement: str
) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        ingest.execute(statement)


def test_f2_a7_kbot_ingest_reads_and_inserts(ingest: psycopg.Connection, symbol: str) -> None:
    ingest.execute(
        "INSERT INTO market.data_gaps (market, channel, symbol, start_at, end_at, cause)"
        " VALUES ('SPOT', 'obu5', %s, %s, %s, 'desconexion')",
        (symbol, T0, T0 + timedelta(seconds=7)),
    )
    rows = ingest.execute(
        "SELECT cause FROM market.data_gaps WHERE symbol = %s", (symbol,)
    ).fetchall()
    assert rows == [("desconexion",)]


# --- F2-A8 and problem 2: depth slots and their metrics -----------------------------------


def test_f2_a8_one_terminal_state_per_slot_and_the_first_one_stays(
    ingest: psycopg.Connection, symbol: str
) -> None:
    slot(ingest, symbol, T0, "valida")
    with pytest.raises(errors.UniqueViolation):
        slot(ingest, symbol, T0, "desconectado")
    slot(ingest, symbol, T0, "desconectado", conflict=True)  # a rerun after a restart
    rows = ingest.execute(
        "SELECT state FROM market.depth_slot_status WHERE symbol = %s", (symbol,)
    ).fetchall()
    assert rows == [("valida",)]


def test_metrics_need_a_valid_slot(ingest: psycopg.Connection, symbol: str) -> None:
    slot(ingest, symbol, T0, "valida")
    metrics(ingest, symbol, T0)
    with pytest.raises(errors.UniqueViolation):
        metrics(ingest, symbol, T0)


@pytest.mark.parametrize(
    "state", ["libro_inconsistente", "no_recibida", "desconectado", "fuera_de_plazo"]
)
def test_metrics_of_any_other_slot_are_refused(
    ingest: psycopg.Connection, symbol: str, state: str
) -> None:
    slot(ingest, symbol, T0, state)
    with pytest.raises(errors.ForeignKeyViolation):
        metrics(ingest, symbol, T0)


def test_metrics_without_any_slot_or_with_another_state_are_refused(
    ingest: psycopg.Connection, symbol: str
) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        metrics(ingest, symbol, T0)
    with pytest.raises(errors.CheckViolation):
        ingest.execute(
            "INSERT INTO market.depth_metrics (market, symbol, depth, slot_start, state,"
            " best_bid, best_ask, mid, spread, bid_levels, ask_levels, bid_quantity,"
            " ask_quantity, bid_notional, ask_notional, bid_notional_10bps,"
            " ask_notional_10bps, bid_notional_50bps, ask_notional_50bps,"
            " bid_deepest_price, ask_deepest_price) VALUES ('SPOT', %s, 5, %s, 'desconectado',"
            " 100, 100.2, 100.1, 0.2, 1, 1, 1, 1, 100, 100.2, 100, 100.2, 100, 100.2, 100, 100.2)",
            (symbol, T0),
        )


@pytest.mark.parametrize(
    "change",
    [
        ("spread", "0.3"),  # not best_ask - best_bid
        ("mid", "100.2"),  # not the middle
        ("bid_levels", "6"),  # more levels than the depth
        ("bid_notional_10bps", "300"),  # a band larger than the visible book
        ("bid_deepest_price", "100.1"),  # deeper level above the best bid
    ],
)
def test_metrics_must_be_internally_consistent(
    ingest: psycopg.Connection, symbol: str, change: tuple[str, str]
) -> None:
    slot(ingest, symbol, T0, "valida")
    column, value = change
    values = {
        "best_bid": "100.0",
        "best_ask": "100.2",
        "mid": "100.1",
        "spread": "0.2",
        "bid_levels": "2",
        "ask_levels": "2",
        "bid_quantity": "3",
        "ask_quantity": "4",
        "bid_notional": "299.9",
        "ask_notional": "401",
        "bid_notional_10bps": "200",
        "ask_notional_10bps": "100.2",
        "bid_notional_50bps": "299.9",
        "ask_notional_50bps": "401",
        "bid_deepest_price": "99.9",
        "ask_deepest_price": "100.3",
    }
    values[column] = value
    columns = ", ".join(values)
    with pytest.raises(errors.CheckViolation):
        ingest.execute(
            f"INSERT INTO market.depth_metrics (market, symbol, depth, slot_start, {columns})"
            f" VALUES ('SPOT', %s, 5, %s, {', '.join(['%s'] * len(values))})",
            (symbol, T0, *values.values()),
        )


def test_minute_snapshots_point_at_a_valid_slot(ingest: psycopg.Connection, symbol: str) -> None:
    book = Jsonb([["100.0", "1"]])
    insert = (
        "INSERT INTO market.depth_snapshots (market, symbol, depth, minute_start, slot_start,"
        " sequence, published_at, exchange_ns, received_at, bids, asks)"
        " VALUES ('SPOT', %s, 5, %s, %s, 7, %s, NULL, %s, %s, %s)"
    )
    slot(ingest, symbol, T0 + timedelta(seconds=40), "desconectado")
    with pytest.raises(errors.ForeignKeyViolation):
        ingest.execute(insert, (symbol, T0, T0 + timedelta(seconds=40), T0, T0, book, book))
    slot(ingest, symbol, T0 + timedelta(seconds=30), "valida")
    ingest.execute(insert, (symbol, T0, T0 + timedelta(seconds=30), T0, T0, book, book))


# --- problem 3: candles, flow and minute status cannot contradict each other ----------


def test_a_cross_checked_candle_needs_a_continuous_flow(
    ingest: psycopg.Connection, symbol: str
) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        candle(ingest, symbol, T0)  # no flow row yet: the order is flow first
    quality(ingest, symbol, "trade", T0, "S2")
    with pytest.raises(errors.CheckViolation):
        candle(ingest, symbol, T0, flow_state="S2")  # cross-checked needs S1
    candle(
        ingest, symbol, T0, state="cerrada_rest", evidence="vela_rest_suficiente", flow_state="S2"
    )
    with pytest.raises(errors.ForeignKeyViolation):
        candle(
            ingest,
            symbol,
            T0 + timedelta(minutes=1),
            state="cerrada_rest",
            evidence="vela_rest_suficiente",
            flow_state="S3",
        )  # that minute has no flow row


def test_own_mark_candles_need_sixty_samples_and_an_s1_mark_flow(
    ingest: psycopg.Connection, symbol: str
) -> None:
    own = {
        "series": "mark",
        "state": "cerrada_propia",
        "evidence": "construida_flujo",
        "flow_channel": "mark-price",
        "volume": None,
    }
    quality(ingest, symbol, "mark-price", T0, "S2")
    with pytest.raises(errors.CheckViolation):
        candle(ingest, symbol, T0, flow_state="S2", samples=60, **own)  # type: ignore[arg-type]
    minute = T0 + timedelta(minutes=1)
    quality(ingest, symbol, "mark-price", minute, "S1")
    with pytest.raises(errors.CheckViolation):
        candle(ingest, symbol, minute, flow_state="S1", samples=59, **own)  # type: ignore[arg-type]
    candle(ingest, symbol, minute, flow_state="S1", samples=60, **own)  # type: ignore[arg-type]


def test_only_index_minutes_are_rebuilt_from_classic(
    ingest: psycopg.Connection, symbol: str
) -> None:
    rebuilt = {
        "state": "cerrada_propia",
        "evidence": "reconstruida_classic",
        "flow_channel": None,
        "flow_state": None,
        "samples": 60,
        "volume": None,
    }
    with pytest.raises(errors.CheckViolation):
        candle(ingest, symbol, T0, series="mark", **rebuilt)  # type: ignore[arg-type]
    candle(ingest, symbol, T0, series="index", **rebuilt)  # type: ignore[arg-type]


# --- audit E2-01, E2-02 and E2-03: pending work and late slots in the database ------------


def ws_candle(conn: psycopg.Connection, symbol: str, minute: datetime, low: str = "99") -> None:
    conn.execute(
        "INSERT INTO market.ws_candles (market, symbol, open_time, open, high, low, close,"
        " volume, turnover, published_at) VALUES ('FUTURES', %s, %s, 100, 101, %s, 100.5, 2,"
        " 201, %s)",
        (symbol, minute, low, minute + timedelta(seconds=30)),
    )


def given_up(conn: psycopg.Connection, symbol: str, minute: datetime, flow_state: str) -> None:
    conn.execute(
        "INSERT INTO market.exhausted_index_rebuilds (market, symbol, minute_start, attempts,"
        " flow_state) VALUES ('FUTURES', %s, %s, 3, %s)",
        (symbol, minute, flow_state),
    )


def test_a_websocket_candle_needs_the_trade_flow_of_its_minute(
    ingest: psycopg.Connection, symbol: str
) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        ws_candle(ingest, symbol, T0)  # no flow row: the order is flow first
    quality(ingest, symbol, "kline", T0, "S1")
    with pytest.raises(errors.ForeignKeyViolation):
        ws_candle(ingest, symbol, T0)  # it is kept with the trade flow of its minute
    quality(ingest, symbol, "trade", T0, "S1")
    with pytest.raises(errors.CheckViolation):
        ws_candle(ingest, symbol, T0, low="100.6")  # low above the close
    ws_candle(ingest, symbol, T0)
    with pytest.raises(errors.UniqueViolation):
        ws_candle(ingest, symbol, T0)  # one per minute


def test_only_an_index_minute_without_its_own_candle_can_be_given_up(
    ingest: psycopg.Connection, symbol: str
) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        given_up(ingest, symbol, T0, "S2")  # no mark-price flow row
    quality(ingest, symbol, "mark-price", T0, "S1")
    with pytest.raises(errors.CheckViolation):
        given_up(ingest, symbol, T0, "S1")  # an S1 minute builds its own candle
    with pytest.raises(errors.ForeignKeyViolation):
        given_up(ingest, symbol, T0, "S2")  # contradicts the flow of the minute
    minute = T0 + timedelta(minutes=1)
    quality(ingest, symbol, "mark-price", minute, "S3")
    given_up(ingest, symbol, minute, "S3")


@pytest.mark.parametrize(
    ("columns", "values"),
    [
        # A last candle without its flow channel: the CHECK would be NULL and the foreign key
        # not checked, so it would stand without any flow row.
        (
            "series, flow_channel, flow_state, state, evidence, samples, volume, turnover",
            "'last', NULL, 'S2', 'cerrada_rest', 'vela_rest_suficiente', NULL, 2, 200",
        ),
        # Own candles without their sample count or without the state of their flow.
        (
            "series, flow_channel, flow_state, state, evidence, samples, volume, turnover",
            "'mark', 'mark-price', 'S1', 'cerrada_propia', 'construida_flujo', NULL, NULL, NULL",
        ),
        (
            "series, flow_channel, flow_state, state, evidence, samples, volume, turnover",
            "'index', 'mark-price', NULL, 'cerrada_propia', 'construida_flujo', 60, NULL, NULL",
        ),
    ],
)
def test_a_candle_never_stands_without_the_evidence_it_needs(
    ingest: psycopg.Connection, symbol: str, columns: str, values: str
) -> None:
    """Review of E2, third round (I-E2-01): a NULL in a column the combination needs is
    refused, never let through by a CHECK that evaluates to NULL."""
    quality(ingest, symbol, "mark-price", T0, "S1")
    with pytest.raises(errors.CheckViolation):
        ingest.execute(
            f"INSERT INTO market.candles (market, symbol, open_time, open, high, low, close,"
            f" {columns}) VALUES ('FUTURES', %s, %s, 100, 101, 99, 100.5, {values})",
            (symbol, T0),
        )


def test_a_data_minute_never_stands_without_its_candle(
    ingest: psycopg.Connection, symbol: str
) -> None:
    """Review of E2, third round (I-E2-01): `datos` with no candle series would skip the
    foreign key to the candle; it is refused. The legitimate rows still go in."""
    quality(ingest, symbol, "trade", T0, "S2")
    with pytest.raises(errors.CheckViolation):
        ingest.execute(
            "INSERT INTO market.minute_status (market, symbol, minute_start, status,"
            " candle_state, flow_channel, flow_state, candle_series) VALUES ('FUTURES', %s,"
            " %s, 'datos', 'cerrada_rest', 'trade', 'S2', NULL)",
            (symbol, T0),
        )
    candle(
        ingest, symbol, T0, state="cerrada_rest", evidence="vela_rest_suficiente", flow_state="S2"
    )
    ingest.execute(
        "INSERT INTO market.minute_status (market, symbol, minute_start, status,"
        " candle_state, flow_channel, flow_state, candle_series) VALUES ('FUTURES', %s,"
        " %s, 'datos', 'cerrada_rest', 'trade', 'S2', 'last')",
        (symbol, T0),
    )


def test_a_late_slot_with_no_book_in_force_has_no_sequence(
    ingest: psycopg.Connection, symbol: str
) -> None:
    ingest.execute(
        "INSERT INTO market.depth_slot_status (market, symbol, depth, slot_start, state,"
        " sequence, snapshot_age_ms) VALUES ('SPOT', %s, 5, %s, 'fuera_de_plazo', NULL, NULL)",
        (symbol, T0),
    )


def late_book(
    conn: psycopg.Connection,
    symbol: str,
    first: datetime,
    last: datetime,
    published: datetime | None = None,
) -> None:
    conn.execute(
        "INSERT INTO market.depth_late_books (market, symbol, depth, slot_start,"
        " last_slot_start, sequence, published_at, received_at)"
        " VALUES ('SPOT', %s, 5, %s, %s, 8, %s, %s)",
        (
            symbol,
            first,
            last,
            published if published is not None else first + timedelta(seconds=9.5),
            last + timedelta(seconds=13),
        ),
    )


def test_a_late_book_names_slots_already_written_and_changes_none(
    ingest: psycopg.Connection, symbol: str
) -> None:
    """Review of E2, fourth round (I-E2-04): a late book points at the slot in which it was
    published and at the last slot closed when it arrived, both written before it. The rows
    it contradicts stay as they are; readers see the valid and missing ones as late."""
    with pytest.raises(errors.ForeignKeyViolation):
        late_book(ingest, symbol, T0, T0)  # no slot written yet
    written = ["valida", "no_recibida", "libro_inconsistente", "valida"]
    for i, state in enumerate(written):
        slot(ingest, symbol, T0 + timedelta(seconds=10 * i), state)
    with pytest.raises(errors.ForeignKeyViolation):
        late_book(ingest, symbol, T0, T0 + timedelta(seconds=40))  # a slot not written
    with pytest.raises(errors.CheckViolation):
        late_book(ingest, symbol, T0 + timedelta(seconds=10), T0)  # ends before it starts
    with pytest.raises(errors.CheckViolation):
        late_book(ingest, symbol, T0, T0, published=T0 - timedelta(seconds=1))  # another slot
    late_book(ingest, symbol, T0, T0 + timedelta(seconds=20))
    assert store.slot_states(ingest, "SPOT", symbol, 5, T0, T0 + timedelta(seconds=40)) == {
        T0: "fuera_de_plazo",
        T0 + timedelta(seconds=10): "fuera_de_plazo",
        T0 + timedelta(seconds=20): "libro_inconsistente",  # already not valid: it stands
        T0 + timedelta(seconds=30): "valida",  # closed after the late book arrived
    }
    rows = ingest.execute(
        "SELECT state FROM market.depth_slot_status WHERE symbol = %s ORDER BY slot_start",
        (symbol,),
    ).fetchall()
    assert [r[0] for r in rows] == written, "the rows written never change"


def seen_late(conn: psycopg.Connection, symbol: str, minute: datetime) -> None:
    conn.execute(
        "INSERT INTO market.trading_seen_late (market, symbol, minute_start, received_at)"
        " VALUES ('FUTURES', %s, %s, %s)",
        (symbol, minute, minute + timedelta(seconds=75)),
    )


def late_candle(conn: psycopg.Connection, symbol: str, minute: datetime, low: str = "99") -> None:
    conn.execute(
        "INSERT INTO market.late_ws_candles (market, symbol, open_time, published_at, open,"
        " high, low, close, volume, turnover, received_at)"
        " VALUES ('FUTURES', %s, %s, %s, 100, 101, %s, 100.5, 2, 201, %s)",
        (symbol, minute, minute + timedelta(seconds=70), low, minute + timedelta(seconds=71)),
    )


def late_repeat(conn: psycopg.Connection, symbol: str, minute: datetime) -> None:
    conn.execute(
        "INSERT INTO market.late_ws_repeats (market, symbol, open_time, published_at,"
        " received_at) VALUES ('FUTURES', %s, %s, %s, %s)",
        (symbol, minute, minute + timedelta(seconds=80), minute + timedelta(seconds=81)),
    )


def test_late_evidence_belongs_to_a_minute_closed_as_s1(
    ingest: psycopg.Connection, symbol: str
) -> None:
    """Reviews of E2, fourth to sixth rounds: only a minute whose trade flow was S1 can be
    decided from the WebSocket, so only such a minute keeps a late trade, a late candle or
    the time of a candle sent again; a trade once, a candle and a repeat once per
    publication time, and a candle only if it is consistent."""
    inserts = (seen_late, late_candle, late_repeat)
    for insert in inserts:
        with pytest.raises(errors.ForeignKeyViolation):
            insert(ingest, symbol, T0)  # no flow row
    quality(ingest, symbol, "kline", T0, "S1")
    for insert in inserts:
        with pytest.raises(errors.ForeignKeyViolation):
            insert(ingest, symbol, T0)  # they belong to the trade flow of their minute
    quality(ingest, symbol, "trade", T0, "S2")
    for insert in inserts:
        with pytest.raises(errors.ForeignKeyViolation):
            insert(ingest, symbol, T0)  # on a degraded flow the WebSocket decides nothing
    minute = T0 + timedelta(minutes=1)
    quality(ingest, symbol, "trade", minute, "S1")
    with pytest.raises(errors.CheckViolation):
        late_candle(ingest, symbol, minute, low="100.6")  # low above the close
    for insert in inserts:
        insert(ingest, symbol, minute)
        with pytest.raises(errors.UniqueViolation):
            insert(ingest, symbol, minute)


def test_pending_work_is_found_from_the_database_alone(
    ingest: psycopg.Connection, symbol: str
) -> None:
    """What a restart resumes: minutes without a REST candle (with their WebSocket candle and
    the tick size in force) and index minutes neither rebuilt nor given up."""

    def instrument(observed_at: datetime, tick: str) -> None:
        ingest.execute(
            "INSERT INTO market.instruments (market, symbol, observed_at, base_currency,"
            " quote_currency, settle_currency, tick_size, quantity_step, multiplier,"
            " min_quantity, min_funds, trading_status, is_trading, raw) VALUES ('FUTURES', %s,"
            " %s, 'XBT', 'USDT', 'USDT', %s, 1, 0.001, 1, NULL, 'Open', true, '{}')",
            (symbol, observed_at, tick),
        )

    instrument(T0 - timedelta(days=1), "0.1")
    instrument(T0 + timedelta(seconds=90), "0.5")  # the tick size changes after minute 0
    m = [T0 + timedelta(minutes=i) for i in range(5)]
    for minute, flow in zip(m, ["S1", "S2", "S1", "S1", "S1"], strict=True):
        quality(ingest, symbol, "trade", minute, flow)
    ws_candle(ingest, symbol, m[0])  # waits, with its WebSocket candle
    candle(  # decided
        ingest, symbol, m[1], state="cerrada_rest", evidence="vela_rest_suficiente", flow_state="S2"
    )
    ingest.execute(  # its day was closed
        "INSERT INTO market.minute_status (market, symbol, minute_start, status, candle_state,"
        " flow_channel, flow_state) VALUES ('FUTURES', %s, %s, 'sin_operaciones_probable',"
        " 'ausencia_sin_trades_probable', 'trade', 'S1')",
        (symbol, m[3]),
    )
    seen_late(ingest, symbol, m[2])  # trading seen after it was closed (fourth round)
    pending = [p for p in store.pending_candles(ingest, m[0]) if p.flow.symbol == symbol]
    assert [(p.flow.minute, p.flow.flow_state, p.ws is not None, p.tick_size) for p in pending] == [
        (m[0], FlowState.S1, True, Decimal("0.1")),
        (m[2], FlowState.S1, False, Decimal("0.5")),
        (m[4], FlowState.S1, False, Decimal("0.5")),
    ]
    # The WebSocket saw trading in 0 (a candle with volume) and in 2 (a late trade).
    assert [p.active for p in pending] == [True, True, False]
    assert pending[0].ws is not None and pending[0].ws.close == Decimal("100.5")
    later = [p.flow.minute for p in store.pending_candles(ingest, m[1]) if p.flow.symbol == symbol]
    assert later == [m[2], m[4]]

    for minute, flow in zip(m, ["S2", "S3", "S2", "S1"], strict=False):
        quality(ingest, symbol, "mark-price", minute, flow)
    candle(  # rebuilt
        ingest,
        symbol,
        m[1],
        series="index",
        state="cerrada_propia",
        evidence="reconstruida_classic",
        flow_channel=None,
        flow_state=None,
        samples=60,
        volume=None,
    )
    given_up(ingest, symbol, m[2], "S2")
    index = [i for i in store.pending_index_rebuilds(ingest, m[0]) if i.symbol == symbol]
    assert [(i.minute, i.flow_state) for i in index] == [(m[0], FlowState.S2)]


def test_a_data_minute_needs_its_candle_and_a_gap_needs_its_gap(
    ingest: psycopg.Connection, symbol: str
) -> None:
    status = (
        "INSERT INTO market.minute_status (market, symbol, minute_start, status, candle_state,"
        " flow_channel, flow_state, candle_series, gap_start)"
        " VALUES ('FUTURES', %s, %s, %s, %s, 'trade', %s, %s, %s)"
    )
    quality(ingest, symbol, "trade", T0, "S1")
    with pytest.raises(errors.ForeignKeyViolation):
        ingest.execute(status, (symbol, T0, "datos", "cerrada_cruzada", "S1", "last", None))
    candle(ingest, symbol, T0)
    with pytest.raises(errors.ForeignKeyViolation):  # the candle is cruzada, not rest
        ingest.execute(status, (symbol, T0, "datos", "cerrada_rest", "S1", "last", None))
    with pytest.raises(errors.CheckViolation):  # a data minute cannot also be a gap
        ingest.execute(status, (symbol, T0, "hueco", "ausencia_no_verificable", "S1", None, T0))

    gap_minute = T0 + timedelta(minutes=1)
    quality(ingest, symbol, "trade", gap_minute, "S2")
    with pytest.raises(errors.ForeignKeyViolation):  # the gap is not in data_gaps
        ingest.execute(
            status, (symbol, gap_minute, "hueco", "ausencia_no_verificable", "S2", None, gap_minute)
        )
    ingest.execute(
        "INSERT INTO market.data_gaps (market, channel, symbol, start_at, end_at, cause)"
        " VALUES ('FUTURES', 'trade', %s, %s, %s, 'desconexion')",
        (symbol, gap_minute, gap_minute + timedelta(seconds=20)),
    )
    ingest.execute(
        status, (symbol, gap_minute, "hueco", "ausencia_no_verificable", "S2", None, gap_minute)
    )
    with pytest.raises(errors.CheckViolation):  # no trades is only probable on S1
        ingest.execute(
            status,
            (
                symbol,
                T0 + timedelta(minutes=2),
                "sin_operaciones_probable",
                "ausencia_sin_trades_probable",
                "S2",
                None,
                None,
            ),
        )


def test_closing_a_minute_twice_never_changes_the_first_result(
    ingest: psycopg.Connection, symbol: str
) -> None:
    """Problem 3 acceptance: a first close (continuous flow, data) and a second close after
    a disconnection and a restart (degraded flow, no candle): the first result stays."""
    status = (
        "INSERT INTO market.minute_status (market, symbol, minute_start, status, candle_state,"
        " flow_channel, flow_state, candle_series, gap_start)"
        " VALUES ('FUTURES', %s, %s, %s, %s, 'trade', %s, %s, %s) ON CONFLICT DO NOTHING"
    )
    quality(ingest, symbol, "trade", T0, "S1", conflict=True)
    candle(ingest, symbol, T0, conflict=True)
    ingest.execute(status, (symbol, T0, "datos", "cerrada_cruzada", "S1", "last", None))

    quality(ingest, symbol, "trade", T0, "S2", conflict=True)
    candle(
        ingest,
        symbol,
        T0,
        state="cerrada_rest",
        evidence="vela_rest_suficiente",
        flow_state="S2",
        conflict=True,
    )
    ingest.execute(
        status, (symbol, T0, "sin_explicar", "ausencia_no_verificable", "S2", None, None)
    )

    assert ingest.execute(
        "SELECT flow_state FROM market.data_quality_minute WHERE symbol = %s", (symbol,)
    ).fetchall() == [("S1",)]
    assert ingest.execute(
        "SELECT state FROM market.candles WHERE symbol = %s", (symbol,)
    ).fetchall() == [("cerrada_cruzada",)]
    assert ingest.execute(
        "SELECT status, flow_state FROM market.minute_status WHERE symbol = %s", (symbol,)
    ).fetchall() == [("datos", "S1")]


# --- problem 1: risk tiers ---------------------------------------------------------------


def test_risk_tiers_are_keyed_by_market_and_only_exist_in_futures(
    ingest: psycopg.Connection, symbol: str
) -> None:
    insert = (
        "INSERT INTO market.risk_tiers (market, symbol, observed_at, level, min_risk_limit,"
        " max_risk_limit, max_leverage, initial_margin, maintain_margin)"
        " VALUES (%s, %s, %s, 1, 0, 250000, 125, 0.008, 0.004)"
    )
    ingest.execute(insert, ("FUTURES", symbol, T0))
    ingest.execute(insert + " ON CONFLICT DO NOTHING", ("FUTURES", symbol, T0))
    with pytest.raises(errors.UniqueViolation):
        ingest.execute(insert, ("FUTURES", symbol, T0))
    with pytest.raises(errors.CheckViolation):
        ingest.execute(insert, ("SPOT", symbol, T0))
    key = ingest.execute(
        """
        SELECT array_agg(a.attname::text ORDER BY k.ord)
        FROM pg_constraint c
        CROSS JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
        WHERE c.conrelid = 'market.risk_tiers'::regclass AND c.contype = 'p'
        """
    ).fetchone()
    assert key == (["market", "symbol", "observed_at", "level"],)


def test_settled_funding_is_stored_once(ingest: psycopg.Connection, symbol: str) -> None:
    settled = T0 + timedelta(hours=4)
    insert = (
        "INSERT INTO market.funding_rates (market, symbol, kind, funding_time, observed_at,"
        " rate) VALUES ('FUTURES', %s, %s, %s, %s, 0.0001)"
    )
    with pytest.raises(errors.CheckViolation):
        ingest.execute(insert, (symbol, "liquidado", settled, T0))
    ingest.execute(insert, (symbol, "liquidado", settled, settled))
    ingest.execute(insert, (symbol, "previsto", settled, T0))
    ingest.execute(insert, (symbol, "previsto", settled, T0 + timedelta(minutes=1)))


@pytest.mark.parametrize(
    ("candle_state", "flow", "accepted"),
    [
        ("ausencia_no_verificable", "S1", False),  # S1 without trades is sin_operaciones_probable
        ("ausencia_no_verificable", "S2", True),
        ("ausencia_no_verificable", "S3", True),
        ("abierta", "S1", True),
        ("abierta", "S2", True),
        ("pendiente_estabilizacion", "S1", True),
        ("pendiente_estabilizacion", "S3", True),
    ],
)
def test_an_unexplained_minute_matches_its_flow(
    ingest: psycopg.Connection, symbol: str, candle_state: str, flow: str, accepted: bool
) -> None:
    """Confirmation review of E2: an unverifiable absence is only possible on S2 or S3."""
    quality(ingest, symbol, "trade", T0, flow)
    insert = (
        "INSERT INTO market.minute_status (market, symbol, minute_start, status, candle_state,"
        " flow_channel, flow_state, candle_series, gap_start)"
        " VALUES ('FUTURES', %s, %s, 'sin_explicar', %s, 'trade', %s, NULL, NULL)"
    )
    if accepted:
        ingest.execute(insert, (symbol, T0, candle_state, flow))
    else:
        with pytest.raises(errors.CheckViolation):
            ingest.execute(insert, (symbol, T0, candle_state, flow))


def test_universe_evaluations_are_written_once(ingest: psycopg.Connection, symbol: str) -> None:
    from kbot.market.store import write_universe
    from kbot.market.universe import evaluate

    moment = T0 + timedelta(microseconds=int(secrets.randbelow(10**6)))
    names = [f"{symbol}{i}-USDT" for i in range(12)]
    instruments = [
        {"symbol": n, "baseCurrency": n, "quoteCurrency": "USDT", "tradingStatus": "TradingEnabled"}
        for n in names
    ]
    tickers = [
        {"symbol": n, "quoteVolume": str(100 + i), "bestBidPrice": "1", "bestAskPrice": "1.01"}
        for i, n in enumerate(names)
    ]
    entries = evaluate("SPOT", instruments, tickers, evaluated_at=moment)
    write_universe(ingest, entries)
    write_universe(ingest, entries)  # a rerun changes nothing
    rows = ingest.execute(
        "SELECT symbol, rank, in_universe, l50_candidate FROM market.universe_snapshots"
        " WHERE evaluated_at = %s ORDER BY rank",
        (moment,),
    ).fetchall()
    assert [r[0] for r in rows] == list(reversed(names))
    assert [r[3] for r in rows] == [True] * 10 + [False] * 2
    with pytest.raises(errors.CheckViolation):
        ingest.execute(
            "INSERT INTO market.universe_snapshots (evaluated_at, market, symbol, rank,"
            " in_universe, l50_candidate, quote_volume_24h, criteria_version)"
            " VALUES (%s, 'SPOT', 'Z-USDT', 99, false, true, 1, 'x')",
            (moment,),
        )


def test_a_zero_volume_spot_candle_is_cross_checked_without_a_websocket_candle(
    ingest: psycopg.Connection, symbol: str
) -> None:
    """Spot publishes a flat zero-volume candle for a minute without trades (measured on
    8 November 2026): with a continuous flow that saw nothing, it is cross-checked."""
    insert = (
        "INSERT INTO market.candles (series, market, symbol, open_time, open, high, low, close,"
        " volume, turnover, ws_volume, ws_turnover, state, evidence, flow_channel, flow_state)"
        " VALUES ('last', 'FUTURES', %s, %s, 130.678, 130.678, 130.678, 130.678, %s, %s,"
        " NULL, NULL, 'cerrada_cruzada', 'vela_cruzada_consistente', 'trade', 'S1')"
    )
    quality(ingest, symbol, "trade", T0, "S1")
    ingest.execute(insert, (symbol, T0, 0, 0))
    later = T0 + timedelta(minutes=1)
    quality(ingest, symbol, "trade", later, "S1")
    with pytest.raises(errors.CheckViolation):
        ingest.execute(insert, (symbol, later, 2, 200))  # trades but no WebSocket candle
