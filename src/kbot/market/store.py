"""Writes the market results to PostgreSQL as kbot_ingest (02 §3.8).

Only ``INSERT ... ON CONFLICT DO NOTHING``: a result is written once and a repeated write
after a restart never replaces it. Each batch runs in one transaction, in the closing
order that the foreign keys require (slot status before its metrics and snapshot).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from kbot.market.depth import SLOT_SECONDS, MinuteSnapshot, SlotResult
from kbot.market.gaps import DataGap
from kbot.market.last_candles import (
    CandleDecision,
    CandleState,
    FlowMinute,
    MinuteStatus,
    day_close,
    state_without_candle,
)
from kbot.market.mark_candles import Evidence, FlowState, OwnCandle
from kbot.market.universe import UniverseEntry


def depth_channel_name(depth: int) -> str:
    return f"obu{depth}"


def resume_point(conn: psycopg.Connection, market: str, symbol: str, depth: int) -> datetime | None:
    """End of the last slot recorded for this channel, or None if there is none."""
    row = conn.execute(
        "SELECT max(slot_start) FROM market.depth_slot_status"
        " WHERE market = %s AND symbol = %s AND depth = %s",
        (market, symbol, depth),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return row[0] + timedelta(seconds=SLOT_SECONDS)


def write_depth(
    conn: psycopg.Connection,
    slots: Iterable[SlotResult],
    minutes: Iterable[MinuteSnapshot],
    gaps: Iterable[DataGap] = (),
) -> None:
    with conn.transaction():
        for gap in gaps:
            write_gap(conn, gap)
        for result in slots:
            conn.execute(
                "INSERT INTO market.depth_slot_status (market, symbol, depth, slot_start,"
                " state, sequence, snapshot_age_ms) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (
                    result.market,
                    result.symbol,
                    result.depth,
                    result.slot_start,
                    result.state.value,
                    result.sequence,
                    result.age_ms,
                ),
            )
            metrics = result.metrics
            if metrics is None:
                continue
            conn.execute(
                "INSERT INTO market.depth_metrics (market, symbol, depth, slot_start, best_bid,"
                " best_ask, mid, spread, bid_levels, ask_levels, bid_quantity, ask_quantity,"
                " bid_notional, ask_notional, bid_notional_10bps, ask_notional_10bps,"
                " bid_notional_50bps, ask_notional_50bps, bid_deepest_price, ask_deepest_price)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, %s) ON CONFLICT DO NOTHING",
                (
                    result.market,
                    result.symbol,
                    result.depth,
                    result.slot_start,
                    metrics.best_bid,
                    metrics.best_ask,
                    metrics.mid,
                    metrics.spread,
                    metrics.bid_levels,
                    metrics.ask_levels,
                    metrics.bid_quantity,
                    metrics.ask_quantity,
                    metrics.bid_notional,
                    metrics.ask_notional,
                    metrics.bid_notional_in_band[0],
                    metrics.ask_notional_in_band[0],
                    metrics.bid_notional_in_band[1],
                    metrics.ask_notional_in_band[1],
                    metrics.bid_deepest_price,
                    metrics.ask_deepest_price,
                ),
            )
        for minute in minutes:
            snapshot = minute.snapshot
            conn.execute(
                "INSERT INTO market.depth_snapshots (market, symbol, depth, minute_start,"
                " slot_start, sequence, published_at, exchange_ns, received_at, bids, asks)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (
                    minute.market,
                    minute.symbol,
                    minute.depth,
                    minute.minute_start,
                    minute.slot_start,
                    snapshot.sequence,
                    snapshot.published_at,
                    snapshot.exchange_ns,
                    snapshot.received_at,
                    Jsonb([[str(p), str(q)] for p, q in snapshot.bids]),
                    Jsonb([[str(p), str(q)] for p, q in snapshot.asks]),
                ),
            )


def write_gap(conn: psycopg.Connection, gap: DataGap) -> None:
    conn.execute(
        "INSERT INTO market.data_gaps (market, channel, symbol, start_at, end_at, cause,"
        " detail) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (
            gap.market,
            gap.channel,
            gap.symbol,
            gap.start_at,
            gap.end_at,
            gap.cause.value,
            gap.detail,
        ),
    )


def write_universe(conn: psycopg.Connection, entries: Iterable[UniverseEntry]) -> None:
    with conn.transaction():
        for entry in entries:
            conn.execute(
                "INSERT INTO market.universe_snapshots (evaluated_at, market, symbol, rank,"
                " in_universe, l50_candidate, quote_volume_24h, best_bid, best_ask,"
                " criteria_version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (
                    entry.evaluated_at,
                    entry.market,
                    entry.symbol,
                    entry.rank,
                    entry.in_universe,
                    entry.l50_candidate,
                    entry.quote_volume_24h,
                    entry.best_bid,
                    entry.best_ask,
                    entry.criteria_version,
                ),
            )


def write_flow(conn: psycopg.Connection, minutes: Iterable[FlowMinute]) -> None:
    """Flow state of each channel and minute: always the first row of a minute's close."""
    with conn.transaction():
        for m in minutes:
            conn.execute(
                "INSERT INTO market.data_quality_minute (market, symbol, channel, minute_start,"
                " flow_state, messages, anomalies, sequence_breaks, reconnections)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s) ON CONFLICT DO NOTHING",
                (
                    m.market,
                    m.symbol,
                    m.channel,
                    m.minute,
                    m.flow_state.value,
                    m.messages,
                    m.anomalies,
                    m.reconnections,
                ),
            )


def fill_missing_flow(
    conn: psycopg.Connection, market: str, symbol: str, channel: str, day: datetime
) -> None:
    """S3 for every minute of ``day`` with no flow row: nothing was recording it."""
    conn.execute(
        "INSERT INTO market.data_quality_minute (market, symbol, channel, minute_start,"
        " flow_state, messages, anomalies, sequence_breaks, reconnections)"
        " SELECT %s, %s, %s, m, 'S3', 0, 0, 0, 0"
        " FROM generate_series(%s::timestamptz, %s::timestamptz, interval '1 minute') AS m"
        " ON CONFLICT DO NOTHING",
        (market, symbol, channel, day, day + timedelta(days=1) - timedelta(minutes=1)),
    )


def write_last_candles(conn: psycopg.Connection, decisions: Iterable[CandleDecision]) -> None:
    with conn.transaction():
        for d in decisions:
            if not d.has_candle or d.rest is None or d.evidence is None:
                continue
            ws = d.ws
            conn.execute(
                "INSERT INTO market.candles (series, market, symbol, open_time, open, high, low,"
                " close, volume, turnover, ws_volume, ws_turnover, state, evidence, samples,"
                " flow_channel, flow_state) VALUES ('last', %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, %s, %s, NULL, 'trade', %s) ON CONFLICT DO NOTHING",
                (
                    d.market,
                    d.symbol,
                    d.minute,
                    d.rest.open,
                    d.rest.high,
                    d.rest.low,
                    d.rest.close,
                    d.rest.volume,
                    d.rest.turnover,
                    ws.volume if ws is not None else None,
                    ws.turnover if ws is not None else None,
                    d.state.value,
                    d.evidence.value,
                    d.flow_state.value,
                ),
            )


def write_own_candles(conn: psycopg.Connection, market: str, candles: Iterable[OwnCandle]) -> None:
    """Mark and index candles built from the flow (needs its mark-price flow row) or rebuilt
    from Classic (index only, no flow row)."""
    with conn.transaction():
        for c in candles:
            from_flow = c.evidence is Evidence.FLOW
            conn.execute(
                "INSERT INTO market.candles (series, market, symbol, open_time, open, high, low,"
                " close, state, evidence, samples, flow_channel, flow_state)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'cerrada_propia', %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (
                    c.series,
                    market,
                    c.symbol,
                    c.open_time,
                    c.open,
                    c.high,
                    c.low,
                    c.close,
                    c.evidence.value,
                    c.samples,
                    "mark-price" if from_flow else None,
                    "S1" if from_flow else None,
                ),
            )


def trade_gaps(
    conn: psycopg.Connection, market: str, symbol: str, day: datetime
) -> list[tuple[datetime, datetime]]:
    rows = conn.execute(
        "SELECT start_at, end_at FROM market.data_gaps WHERE market = %s AND channel = 'trade'"
        " AND symbol = %s AND start_at < %s AND end_at > %s ORDER BY start_at",
        (market, symbol, day + timedelta(days=1), day),
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def write_minute_status(conn: psycopg.Connection, statuses: Iterable[MinuteStatus]) -> None:
    """Terminal status of each minute, last in the closing order (02 §3.8)."""
    with conn.transaction():
        for s in statuses:
            conn.execute(
                "INSERT INTO market.minute_status (market, symbol, minute_start, status,"
                " candle_state, flow_channel, flow_state, candle_series, gap_start)"
                " VALUES (%s, %s, %s, %s, %s, 'trade', %s, %s, %s) ON CONFLICT DO NOTHING",
                (
                    s.market,
                    s.symbol,
                    s.minute,
                    s.status.value,
                    s.candle_state.value,
                    s.flow_state.value,
                    s.candle_series,
                    s.gap_start,
                ),
            )


def close_day(conn: psycopg.Connection, market: str, symbol: str, day: datetime) -> int:
    """Terminal status of every minute of ``day``, only from what the database holds, so
    the close gives the same result after a restart. Returns the minutes written."""
    with conn.transaction():
        for channel in ("trade", "kline"):
            fill_missing_flow(conn, market, symbol, channel, day)
        end = day + timedelta(days=1)
        rows = conn.execute(
            "SELECT channel, minute_start, flow_state, messages FROM market.data_quality_minute"
            " WHERE market = %s AND symbol = %s AND channel IN ('trade', 'kline')"
            " AND minute_start >= %s AND minute_start < %s",
            (market, symbol, day, end),
        ).fetchall()
        flows = {m: FlowState(state) for channel, m, state, _ in rows if channel == "trade"}
        klines = {m: messages for channel, m, _, messages in rows if channel == "kline"}
        states = {
            row[0]: CandleState(row[1])
            for row in conn.execute(
                "SELECT open_time, state FROM market.candles WHERE series = 'last'"
                " AND market = %s AND symbol = %s AND open_time >= %s AND open_time < %s",
                (market, symbol, day, end),
            ).fetchall()
        }
        for minute, flow in flows.items():
            if minute not in states:
                states[minute] = state_without_candle(flow, klines.get(minute, 0))
        statuses = day_close(
            day, flows, states, trade_gaps(conn, market, symbol, day), market=market, symbol=symbol
        )
        write_minute_status(conn, statuses)
    return len(statuses)
