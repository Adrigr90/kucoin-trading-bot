"""Writes the market results to PostgreSQL as kbot_ingest (02 §3.8).

Only ``INSERT ... ON CONFLICT DO NOTHING``: a result is written once and a repeated write
after a restart never replaces it. Each batch runs in one transaction, in the closing
order that the foreign keys require (slot status before its metrics and snapshot).
"""

from __future__ import annotations

import json
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
    Ohlcv,
    PendingCandle,
    WsCandle,
    day_close,
    state_without_candle,
)
from kbot.market.mark_candles import Evidence, FlowState, IndexMinute, OwnCandle
from kbot.market.reference import FundingRow, InstrumentRow, RiskTierRow, changed_instruments
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
    """Slots, their metrics and the minute snapshots, in batches (``executemany``), in the
    order the foreign keys need: a slot status before its metrics and its snapshot."""
    slots = list(slots)
    with conn.transaction(), conn.cursor() as cur:
        for gap in gaps:
            write_gap(conn, gap)
        cur.executemany(
            "INSERT INTO market.depth_slot_status (market, symbol, depth, slot_start,"
            " state, sequence, snapshot_age_ms) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            [
                (r.market, r.symbol, r.depth, r.slot_start, r.state.value, r.sequence, r.age_ms)
                for r in slots
            ],
        )
        cur.executemany(
            "INSERT INTO market.depth_metrics (market, symbol, depth, slot_start, best_bid,"
            " best_ask, mid, spread, bid_levels, ask_levels, bid_quantity, ask_quantity,"
            " bid_notional, ask_notional, bid_notional_10bps, ask_notional_10bps,"
            " bid_notional_50bps, ask_notional_50bps, bid_deepest_price, ask_deepest_price)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s) ON CONFLICT DO NOTHING",
            [
                (
                    r.market,
                    r.symbol,
                    r.depth,
                    r.slot_start,
                    m.best_bid,
                    m.best_ask,
                    m.mid,
                    m.spread,
                    m.bid_levels,
                    m.ask_levels,
                    m.bid_quantity,
                    m.ask_quantity,
                    m.bid_notional,
                    m.ask_notional,
                    m.bid_notional_in_band[0],
                    m.ask_notional_in_band[0],
                    m.bid_notional_in_band[1],
                    m.ask_notional_in_band[1],
                    m.bid_deepest_price,
                    m.ask_deepest_price,
                )
                for r in slots
                if (m := r.metrics) is not None
            ],
        )
        cur.executemany(
            "INSERT INTO market.depth_snapshots (market, symbol, depth, minute_start,"
            " slot_start, sequence, published_at, exchange_ns, received_at, bids, asks)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [
                (
                    minute.market,
                    minute.symbol,
                    minute.depth,
                    minute.minute_start,
                    minute.slot_start,
                    minute.snapshot.sequence,
                    minute.snapshot.published_at,
                    minute.snapshot.exchange_ns,
                    minute.snapshot.received_at,
                    Jsonb([[str(p), str(q)] for p, q in minute.snapshot.bids]),
                    Jsonb([[str(p), str(q)] for p, q in minute.snapshot.asks]),
                )
                for minute in minutes
            ],
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


def write_ws_candles(conn: psycopg.Connection, candles: Iterable[WsCandle]) -> None:
    """The last WebSocket candle of each closed minute, after its trade flow row (E2-01)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO market.ws_candles (market, symbol, open_time, open, high, low, close,"
            " volume, turnover, published_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            [
                (
                    w.market,
                    w.symbol,
                    w.candle.open_time,
                    w.candle.open,
                    w.candle.high,
                    w.candle.low,
                    w.candle.close,
                    w.candle.volume,
                    w.candle.turnover,
                    w.published_at,
                )
                for w in candles
            ],
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


# --- resuming after a stop --------------------------------------------------------------


def flow_resume_point(
    conn: psycopg.Connection, market: str, symbol: str, channel: str
) -> datetime | None:
    """End of the last minute whose flow was recorded for this channel, or None."""
    row = conn.execute(
        "SELECT max(minute_start) FROM market.data_quality_minute"
        " WHERE market = %s AND symbol = %s AND channel = %s",
        (market, symbol, channel),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return row[0] + timedelta(minutes=1)


def pending_candles(conn: psycopg.Connection, since: datetime) -> list[PendingCandle]:
    """Minutes of the last series that still wait for their REST candle (audit E2-01): a
    trade flow row since ``since`` with neither a candle nor a terminal status, with the
    WebSocket candle stored with it and the tick size in force when the minute ended."""
    rows = conn.execute(
        "SELECT q.market, q.symbol, q.minute_start, q.flow_state, q.messages, q.anomalies,"
        " q.reconnections, w.open, w.high, w.low, w.close, w.volume, w.turnover, i.tick_size"
        " FROM market.data_quality_minute AS q"
        " LEFT JOIN market.ws_candles AS w ON w.market = q.market AND w.symbol = q.symbol"
        "   AND w.open_time = q.minute_start"
        " LEFT JOIN LATERAL (SELECT n.tick_size FROM market.instruments AS n"
        "   WHERE n.market = q.market AND n.symbol = q.symbol"
        "   ORDER BY n.observed_at < q.minute_start + interval '1 minute' DESC,"
        "   n.observed_at DESC LIMIT 1) AS i ON true"
        " WHERE q.channel = 'trade' AND q.minute_start >= %s"
        " AND NOT EXISTS (SELECT 1 FROM market.candles AS c WHERE c.series = 'last'"
        "   AND c.market = q.market AND c.symbol = q.symbol AND c.open_time = q.minute_start)"
        " AND NOT EXISTS (SELECT 1 FROM market.minute_status AS s WHERE s.market = q.market"
        "   AND s.symbol = q.symbol AND s.minute_start = q.minute_start)"
        " ORDER BY q.market, q.symbol, q.minute_start",
        (since,),
    ).fetchall()
    pending = []
    for row in rows:
        market, symbol, minute, state, messages, anomalies, reconnections = row[:7]
        o, h, low, c, volume, turnover, tick_size = row[7:]
        if tick_size is None:
            continue  # never recorded: the recorder stores the reference data first
        flow = FlowMinute(
            market, symbol, "trade", minute, FlowState(state), messages, anomalies, reconnections
        )
        ws = Ohlcv(minute, o, h, low, c, volume, turnover) if o is not None else None
        pending.append(PendingCandle(flow, ws, tick_size))
    return pending


def pending_index_rebuilds(conn: psycopg.Connection, since: datetime) -> list[IndexMinute]:
    """Index minutes still to rebuild from Classic (audit E2-02): a mark-price minute since
    ``since`` whose flow could not build its own candles, with no index candle and not
    given up. Rebuilt, pending and given up are told apart from the database alone."""
    rows = conn.execute(
        "SELECT q.symbol, q.minute_start, q.flow_state FROM market.data_quality_minute AS q"
        " WHERE q.market = 'FUTURES' AND q.channel = 'mark-price' AND q.flow_state <> 'S1'"
        " AND q.minute_start >= %s"
        " AND NOT EXISTS (SELECT 1 FROM market.candles AS c WHERE c.series = 'index'"
        "   AND c.market = 'FUTURES' AND c.symbol = q.symbol AND c.open_time = q.minute_start)"
        " AND NOT EXISTS (SELECT 1 FROM market.exhausted_index_rebuilds AS x"
        "   WHERE x.market = 'FUTURES' AND x.symbol = q.symbol"
        "   AND x.minute_start = q.minute_start)"
        " ORDER BY q.symbol, q.minute_start",
        (since,),
    ).fetchall()
    return [IndexMinute(symbol, minute, FlowState(state)) for symbol, minute, state in rows]


def write_exhausted_index(
    conn: psycopg.Connection, minutes: Iterable[tuple[IndexMinute, int]]
) -> None:
    """Index minutes given up after every attempt: Classic had no complete minute."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO market.exhausted_index_rebuilds (market, symbol, minute_start,"
            " attempts, flow_state) VALUES ('FUTURES', %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [(m.symbol, m.minute, attempts, m.flow_state.value) for m, attempts in minutes],
        )


def day_is_closed(conn: psycopg.Connection, market: str, symbol: str, day: datetime) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM market.minute_status WHERE market = %s AND symbol = %s"
        " AND minute_start >= %s AND minute_start < %s",
        (market, symbol, day, day + timedelta(days=1)),
    ).fetchone()
    return row is not None and row[0] == 24 * 60


# --- universe -----------------------------------------------------------------------------


def load_universe(conn: psycopg.Connection, day: datetime) -> list[UniverseEntry]:
    """The latest evaluation made during ``day``, or an empty list."""
    rows = conn.execute(
        "SELECT evaluated_at, market, symbol, rank, in_universe, l50_candidate,"
        " quote_volume_24h, best_bid, best_ask, criteria_version"
        " FROM market.universe_snapshots WHERE evaluated_at = ("
        "   SELECT max(evaluated_at) FROM market.universe_snapshots"
        "   WHERE evaluated_at >= %s AND evaluated_at < %s)"
        " ORDER BY market, rank",
        (day, day + timedelta(days=1)),
    ).fetchall()
    return [UniverseEntry(*row) for row in rows]


def universe_members(conn: psycopg.Connection, day: datetime) -> set[tuple[str, str]]:
    """Instruments in the universe at some moment of ``day``: the evaluation in force when
    the day started and every evaluation made during it."""
    rows = conn.execute(
        "SELECT DISTINCT market, symbol FROM market.universe_snapshots"
        " WHERE in_universe AND evaluated_at < %s AND evaluated_at >= coalesce("
        "   (SELECT max(evaluated_at) FROM market.universe_snapshots WHERE evaluated_at <= %s),"
        "   %s)",
        (day + timedelta(days=1), day, day),
    ).fetchall()
    return {(row[0], row[1]) for row in rows}


# --- reference data -------------------------------------------------------------------------


def _json_dumps(value: object) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def write_instruments(
    conn: psycopg.Connection, rows: Iterable[InstrumentRow], observed_at: datetime
) -> int:
    """Insert the instruments whose business fields changed since the last stored row."""
    rows = list(rows)
    with conn.transaction():
        latest = {
            (row[0], row[1]): tuple(row[2:])
            for row in conn.execute(
                "SELECT DISTINCT ON (market, symbol) market, symbol, base_currency,"
                " quote_currency, settle_currency, tick_size, quantity_step, multiplier,"
                " min_quantity, min_funds, trading_status, is_trading"
                " FROM market.instruments WHERE symbol = ANY(%s)"
                " ORDER BY market, symbol, observed_at DESC",
                ([row.symbol for row in rows],),
            ).fetchall()
        }
        changed = changed_instruments(rows, latest)
        for row in changed:
            conn.execute(
                "INSERT INTO market.instruments (market, symbol, observed_at, base_currency,"
                " quote_currency, settle_currency, tick_size, quantity_step, multiplier,"
                " min_quantity, min_funds, trading_status, is_trading, raw)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (
                    row.market,
                    row.symbol,
                    observed_at,
                    row.base_currency,
                    row.quote_currency,
                    row.settle_currency,
                    row.tick_size,
                    row.quantity_step,
                    row.multiplier,
                    row.min_quantity,
                    row.min_funds,
                    row.trading_status,
                    row.is_trading,
                    Jsonb(dict(row.raw), dumps=_json_dumps),
                ),
            )
    return len(changed)


def write_risk_tiers(
    conn: psycopg.Connection, symbol: str, tiers: list[RiskTierRow], observed_at: datetime
) -> bool:
    """Insert the tiers of a contract when they differ from the last stored set."""
    fields = [
        (
            t.level,
            t.min_risk_limit,
            t.max_risk_limit,
            t.max_leverage,
            t.initial_margin,
            t.maintain_margin,
        )
        for t in tiers
    ]
    with conn.transaction():
        stored = conn.execute(
            "SELECT level, min_risk_limit, max_risk_limit, max_leverage, initial_margin,"
            " maintain_margin FROM market.risk_tiers WHERE market = 'FUTURES' AND symbol = %s"
            " AND observed_at = (SELECT max(observed_at) FROM market.risk_tiers"
            "   WHERE market = 'FUTURES' AND symbol = %s) ORDER BY level",
            (symbol, symbol),
        ).fetchall()
        if [tuple(row) for row in stored] == fields:
            return False
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO market.risk_tiers (market, symbol, observed_at, level,"
                " min_risk_limit, max_risk_limit, max_leverage, initial_margin, maintain_margin)"
                " VALUES ('FUTURES', %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(symbol, observed_at, *row) for row in fields],
            )
    return True


def write_funding(conn: psycopg.Connection, rows: Iterable[FundingRow]) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO market.funding_rates (market, symbol, kind, funding_time, observed_at,"
            " rate, granularity_ms, rate_cap, rate_floor)"
            " VALUES ('FUTURES', %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [
                (
                    r.symbol,
                    r.kind,
                    r.funding_time,
                    r.observed_at,
                    r.rate,
                    r.granularity_ms,
                    r.rate_cap,
                    r.rate_floor,
                )
                for r in rows
            ],
        )
