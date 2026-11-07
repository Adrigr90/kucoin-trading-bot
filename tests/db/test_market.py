"""The market schema in a real PostgreSQL 18, with real kbot_ingest sessions (02 §3.8).

Phase 2 acceptance A7 (kbot_ingest only reads and inserts in market) and A8 (one terminal
state per depth slot), and the review of E2: the database itself refuses metrics of a slot
that is not valid (problem 2), a candle or a minute status that contradicts its flow
(problem 3), and a second terminal result for the same key (idempotence).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import errors
from psycopg.types.json import Jsonb

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
        " volume, turnover, state, evidence, samples, flow_channel, flow_state)"
        " VALUES (%s, 'FUTURES', %s, %s, 100, 101, 99, 100.5, %s, %s, %s, %s, %s, %s, %s)"
        + (" ON CONFLICT DO NOTHING" if conflict else ""),
        (
            series,
            symbol,
            minute,
            volume,
            "200" if volume is not None else None,
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


# --- A7: privileges in real sessions ---------------------------------------------------


@pytest.mark.parametrize("table", MARKET_TABLES)
@pytest.mark.parametrize(
    "statement",
    ["UPDATE {t} SET recorded_at = recorded_at", "DELETE FROM {t}", "TRUNCATE {t}"],
)
def test_a7_kbot_ingest_can_never_change_or_delete(
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
def test_a7_kbot_ingest_has_nothing_outside_its_rows(
    ingest: psycopg.Connection, statement: str
) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        ingest.execute(statement)


def test_a7_kbot_ingest_reads_and_inserts(ingest: psycopg.Connection, symbol: str) -> None:
    ingest.execute(
        "INSERT INTO market.data_gaps (market, channel, symbol, start_at, end_at, cause)"
        " VALUES ('SPOT', 'obu5', %s, %s, %s, 'desconexion')",
        (symbol, T0, T0 + timedelta(seconds=7)),
    )
    rows = ingest.execute(
        "SELECT cause FROM market.data_gaps WHERE symbol = %s", (symbol,)
    ).fetchall()
    assert rows == [("desconexion",)]


# --- A8 and problem 2: depth slots and their metrics -----------------------------------


def test_a8_one_terminal_state_per_slot_and_the_first_one_stays(
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
