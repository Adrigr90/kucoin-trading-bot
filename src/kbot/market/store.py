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
