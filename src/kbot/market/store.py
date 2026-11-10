"""Writes the market results to PostgreSQL as kbot_ingest (02 §3.8).

Only ``INSERT ... ON CONFLICT DO NOTHING``: a result is written once and a repeated write
after a restart never replaces it. Each batch runs in one transaction, in the closing
order that the foreign keys require (slot status before its metrics and snapshot).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

import psycopg
import structlog
from psycopg import errors, sql
from psycopg.types.json import Jsonb

from kbot.market.depth import SLOT_SECONDS, LateBook, MinuteSnapshot, SlotResult
from kbot.market.gaps import DataGap
from kbot.market.last_candles import (
    CandleDecision,
    CandleState,
    FlowMinute,
    KeptEvidence,
    LateEvidence,
    LateTrade,
    LateWsRepeat,
    LateWsTrading,
    MinuteStatus,
    Ohlcv,
    PendingCandle,
    WsCandle,
    candle_kind,
    day_close,
    decide,
    state_without_candle,
    trade_changes,
)
from kbot.market.mark_candles import Evidence, FlowState, IndexMinute, OwnCandle
from kbot.market.reference import FundingRow, InstrumentRow, RiskTierRow, changed_instruments
from kbot.market.universe import UniverseEntry

_log = structlog.get_logger()


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


def late_book_rows(book: LateBook) -> list[tuple[object, ...]]:
    """The rows of a late book: one per UTC day of its range of slots, from the slot of its P
    to the last slot closed when it arrived (02 §3.9). Each row names only slots of its own
    day, so retiring a day never changes what the slots of another day count as."""
    rows: list[tuple[object, ...]] = []
    first = book.slot_start.astimezone(UTC)
    final = book.last_slot_start.astimezone(UTC)
    while True:
        day_end = datetime(first.year, first.month, first.day, tzinfo=UTC) + timedelta(days=1)
        last = min(final, day_end - timedelta(seconds=SLOT_SECONDS))
        rows.append(
            (
                book.market,
                book.symbol,
                book.depth,
                book.slot_start,
                first,
                last,
                book.sequence,
                book.published_at,
                book.received_at,
            )
        )
        if last >= final:
            return rows
        first = day_end


def write_late_books(conn: psycopg.Connection, books: Iterable[LateBook]) -> None:
    """Books that arrived after their slot was closed (review of E2, fourth round), one row
    per day they affect. Every slot they name was closed, and written, before the book
    arrived. A row of a sealed day is not written: the seal forbids it (02 §3.9), also if the
    day is sealed between the check and the insert (``_inserted``, review of E3b)."""
    rows = [row for book in books for row in late_book_rows(book)]
    with conn.transaction():
        sealed = sealed_days(conn, (_day(row[4]) for row in rows))
        for row in rows:
            if _day(row[4]) in sealed:
                _log.warning(
                    "late_book_of_a_sealed_day", market=row[0], symbol=row[1], slot=str(row[4])
                )
            else:
                _inserted(
                    conn,
                    "INSERT INTO market.depth_late_books (market, symbol, depth, slot_start,"
                    " first_slot_start, last_slot_start, sequence, published_at, received_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT DO NOTHING RETURNING 1",
                    row,
                )


def sealed_days(conn: psycopg.Connection, days: Iterable[date]) -> set[date]:
    """The days among ``days`` that are sealed (02 §3.9): their partitions take no new row."""
    wanted = sorted(set(days))
    if not wanted:
        return set()
    rows = conn.execute(
        "SELECT day FROM market.archive_seals WHERE day = ANY(%s)", (wanted,)
    ).fetchall()
    return {row[0] for row in rows}


def _day(moment: object) -> date:
    if not isinstance(moment, datetime):
        raise TypeError(f"not a moment: {moment!r}")
    return moment.astimezone(UTC).date()


def _late_minute(item: LateEvidence) -> datetime:
    return item.minute if isinstance(item, LateTrade) else item.candle.open_time


# What the database holds about the WebSocket in the minute of ``q`` (a trade row of
# data_quality_minute), composed with psycopg.sql from constants only:
# - its candle: the latest by P of the one stored with its flow and those kept late as the
#   latest (late_ws_candles.latest); each of those is newer than all before it, so there is
#   never a tie (review of E2, eighth round);
# - the latest P seen, also of a candle sent again (late_ws_repeats);
# - whether it saw trading besides the trades in time: any candle of the minute with
#   volume, in time (ws_candles.traded, even if a later one had none) or late (also an older
#   one that never became the latest), or a late trade. Once true it stays true, apart from
#   which candle is the latest (review of E2, sixth and seventh rounds).
_LATEST_WS = sql.SQL(
    "SELECT x.open, x.high, x.low, x.close, x.volume, x.turnover FROM ("
    " SELECT w.open, w.high, w.low, w.close, w.volume, w.turnover, w.published_at"
    " FROM market.ws_candles AS w WHERE w.market = q.market AND w.symbol = q.symbol"
    " AND w.open_time = q.minute_start"
    " UNION ALL"
    " SELECT l.open, l.high, l.low, l.close, l.volume, l.turnover, l.published_at"
    " FROM market.late_ws_candles AS l WHERE l.market = q.market AND l.symbol = q.symbol"
    " AND l.open_time = q.minute_start AND l.latest"
    ") AS x ORDER BY x.published_at DESC LIMIT 1"
)
_MAX_PUBLISHED = sql.SQL(
    "SELECT max(x.published_at) FROM ("
    " SELECT w.published_at FROM market.ws_candles AS w WHERE w.market = q.market"
    " AND w.symbol = q.symbol AND w.open_time = q.minute_start"
    " UNION ALL"
    " SELECT l.published_at FROM market.late_ws_candles AS l WHERE l.market = q.market"
    " AND l.symbol = q.symbol AND l.open_time = q.minute_start"
    " UNION ALL"
    " SELECT r.published_at FROM market.late_ws_repeats AS r WHERE r.market = q.market"
    " AND r.symbol = q.symbol AND r.open_time = q.minute_start"
    ") AS x"
)
_ACTIVE = sql.SQL(
    "EXISTS (SELECT 1 FROM market.trading_seen_late AS t WHERE t.market = q.market"
    " AND t.symbol = q.symbol AND t.minute_start = q.minute_start)"
    " OR EXISTS (SELECT 1 FROM market.ws_candles AS w WHERE w.market = q.market"
    " AND w.symbol = q.symbol AND w.open_time = q.minute_start AND w.traded)"
    " OR EXISTS (SELECT 1 FROM market.late_ws_candles AS l WHERE l.market = q.market"
    " AND l.symbol = q.symbol AND l.open_time = q.minute_start AND l.volume > 0)"
)
_LATE_STATE = sql.SQL(
    "SELECT q.flow_state, q.messages, q.anomalies, q.reconnections,"
    " w.open, w.high, w.low, w.close, w.volume, w.turnover,"
    " ({max_published}), ({active})"
    " FROM market.data_quality_minute AS q"
    " LEFT JOIN LATERAL ({latest}) AS w ON true"
    " WHERE q.market = %s AND q.symbol = %s AND q.channel = 'trade' AND q.minute_start = %s"
).format(max_published=_MAX_PUBLISHED, active=_ACTIVE, latest=_LATEST_WS)


def write_late_minutes(
    conn: psycopg.Connection, items: Iterable[LateEvidence]
) -> list[KeptEvidence]:
    """Trades and WebSocket candles of minutes already closed, in the order they arrived
    (reviews of E2, fourth to seventh rounds). Each one is judged against what the database
    holds for its minute, with the rules of ``last_candles`` (``trade_changes`` and
    ``candle_kind``), so a restart gives the same answer as a run without one:

    * a trade is kept if the trade flow of its minute is S1, no trade arrived in time and
      the WebSocket had seen no trading in it;
    * a candle is kept if that flow is S1 and it is newer than every candle seen for the
      minute and different from the latest; newer and the same, only its time is kept
      (``late_ws_repeats``); older, only if its volume is the first sign of trading in the
      minute, and then it never becomes the latest; otherwise nothing.

    Returns what was kept, which the recorder uses only now (``RecorderCore.late_kept``)."""
    kept: list[KeptEvidence] = []
    items = list(items)
    with conn.transaction():
        sealed = sealed_days(conn, (_late_minute(item).astimezone(UTC).date() for item in items))
        for item in items:
            minute = _late_minute(item)
            if minute.astimezone(UTC).date() in sealed:
                _log.warning(
                    "late_evidence_of_a_sealed_day",
                    market=item.market,
                    symbol=item.symbol,
                    minute=minute.isoformat(),
                    kind=type(item).__name__,
                )
                continue
            row = conn.execute(_LATE_STATE, (item.market, item.symbol, minute)).fetchone()
            if row is None:
                continue  # no flow row: the minute was never recorded here
            state, messages, anomalies, reconnections = row[:4]
            flow = FlowMinute(
                item.market,
                item.symbol,
                "trade",
                minute,
                FlowState(state),
                messages,
                anomalies,
                reconnections,
            )
            latest = Ohlcv(minute, *row[4:10]) if row[4] is not None else None
            max_published, active = row[10], row[11]
            if isinstance(item, LateTrade):
                if trade_changes(flow, active) and _inserted(
                    conn,
                    "INSERT INTO market.trading_seen_late (market, symbol, minute_start,"
                    " received_at) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING 1",
                    (item.market, item.symbol, minute, item.received_at),
                ):
                    kept.append(item)
                continue
            kind = candle_kind(flow, latest, max_published, active, item.candle, item.published_at)
            c = item.candle
            if kind in ("candle", "trading") and _inserted(
                conn,
                "INSERT INTO market.late_ws_candles (market, symbol, open_time, published_at,"
                " open, high, low, close, volume, turnover, received_at, latest) VALUES (%s,"
                " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING 1",
                (
                    item.market,
                    item.symbol,
                    minute,
                    item.published_at,
                    c.open,
                    c.high,
                    c.low,
                    c.close,
                    c.volume,
                    c.turnover,
                    item.received_at,
                    kind == "candle",
                ),
            ):
                # A candle that is not newer, also one with the same P as the latest, never
                # becomes the latest (latest = false): it only shows trading (eighth round).
                kept.append(
                    item if kind == "candle" else LateWsTrading(item.market, item.symbol, minute)
                )
            elif kind == "repeat" and _inserted(
                conn,
                "INSERT INTO market.late_ws_repeats (market, symbol, open_time, published_at,"
                " received_at) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING 1",
                (item.market, item.symbol, minute, item.published_at, item.received_at),
            ):
                kept.append(
                    LateWsRepeat(
                        item.market, item.symbol, minute, item.published_at, item.received_at
                    )
                )
    return kept


def _inserted(conn: psycopg.Connection, query: str, params: tuple[object, ...]) -> bool:
    """INSERT ... RETURNING 1 of a piece of late evidence, in its own savepoint. If its day was
    sealed after it was checked (the seal's CHECK, named ``*_sellada``), only that row is
    undone and left out with a warning, and the rest of the tick stays; any other error is
    raised (review of E3b, E3b-01)."""
    try:
        with conn.transaction():
            return conn.execute(query, params).fetchone() is not None  # type: ignore[arg-type]
    except errors.CheckViolation as error:
        constraint = error.diag.constraint_name or ""
        if not constraint.endswith("_sellada"):
            raise
        _log.warning("late_evidence_of_a_day_sealed_meanwhile", constraint=constraint)
        return False


def slot_states(
    conn: psycopg.Connection,
    market: str,
    symbol: str,
    depth: int,
    start: datetime,
    end: datetime,
) -> dict[datetime, str]:
    """What each slot in [start, end) counts as (02 §3.5): its terminal state, except that a
    slot written ``valida`` or ``no_recibida`` between the slot of a late book and the last
    slot closed when it arrived counts as ``fuera_de_plazo`` (``depth.effective_state``).
    Readers of the depth use this, never ``depth_slot_status`` alone."""
    rows = conn.execute(
        "SELECT s.slot_start, CASE WHEN s.state IN ('valida', 'no_recibida') AND EXISTS ("
        "   SELECT 1 FROM market.depth_late_books AS l WHERE l.market = s.market"
        "   AND l.symbol = s.symbol AND l.depth = s.depth AND l.first_slot_start <= s.slot_start"
        "   AND l.last_slot_start >= s.slot_start) THEN 'fuera_de_plazo' ELSE s.state END"
        " FROM market.depth_slot_status AS s WHERE s.market = %s AND s.symbol = %s"
        " AND s.depth = %s AND s.slot_start >= %s AND s.slot_start < %s ORDER BY s.slot_start",
        (market, symbol, depth, start, end),
    ).fetchall()
    return {row[0]: row[1] for row in rows}


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
    """S3 for every minute of ``day`` with no flow row: nothing was recording it. Only for
    a day whose minutes the recorder has all closed (``RecorderCore.closed_through``), so a
    minute still open in memory is never taken for one that nobody recorded."""
    conn.execute(
        "INSERT INTO market.data_quality_minute (market, symbol, channel, minute_start,"
        " flow_state, messages, anomalies, sequence_breaks, reconnections)"
        " SELECT %s, %s, %s, m, 'S3', 0, 0, 0, 0"
        " FROM generate_series(%s::timestamptz, %s::timestamptz, interval '1 minute') AS m"
        " ON CONFLICT DO NOTHING",
        (market, symbol, channel, day, day + timedelta(days=1) - timedelta(minutes=1)),
    )


def write_ws_candles(conn: psycopg.Connection, candles: Iterable[WsCandle]) -> None:
    """The last WebSocket candle of each closed minute, after its trade flow row (E2-01),
    with whether any candle of the minute had volume (review of E2, seventh round)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO market.ws_candles (market, symbol, open_time, open, high, low, close,"
            " volume, turnover, published_at, traded)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
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
                    w.traded,
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
        trades = {m: messages for channel, m, _, messages in rows if channel == "trade"}
        klines = {m: messages for channel, m, _, messages in rows if channel == "kline"}
        late = _late_minutes(conn, market, symbol, day, end)  # the WebSocket saw them late
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
                states[minute] = state_without_candle(
                    flow, klines.get(minute, 0), trades.get(minute, 0), seen_late=minute in late
                )
        statuses = day_close(
            day, flows, states, trade_gaps(conn, market, symbol, day), market=market, symbol=symbol
        )
        write_minute_status(conn, statuses)
    return len(statuses)


def _late_minutes(
    conn: psycopg.Connection, market: str, symbol: str, start: datetime, end: datetime
) -> set[datetime]:
    """Minutes in [start, end) with a trade or a WebSocket candle kept after they closed."""
    rows = conn.execute(
        "SELECT minute_start FROM market.trading_seen_late WHERE market = %s AND symbol = %s"
        " AND minute_start >= %s AND minute_start < %s"
        " UNION SELECT open_time FROM market.late_ws_candles WHERE market = %s AND symbol = %s"
        " AND open_time >= %s AND open_time < %s",
        (market, symbol, start, end, market, symbol, start, end),
    ).fetchall()
    return {row[0] for row in rows}


# What a minute is decided from, as the database holds it: its trade flow, its REST candle
# if it has one, its latest WebSocket candle (in time or late), whether the WebSocket ever
# saw trading in it besides the trades in time, and the tick size in force when it ended.
_EVIDENCE = sql.SQL(
    "SELECT q.flow_state, q.messages, q.anomalies, q.reconnections,"
    " c.open, c.high, c.low, c.close, c.volume, c.turnover,"
    " w.open, w.high, w.low, w.close, w.volume, w.turnover,"
    " ({active}), i.tick_size, q.market, q.symbol, q.minute_start"
    " FROM market.data_quality_minute AS q"
    " LEFT JOIN market.candles AS c ON c.series = 'last' AND c.market = q.market"
    "   AND c.symbol = q.symbol AND c.open_time = q.minute_start"
    " LEFT JOIN LATERAL ({latest}) AS w ON true"
    " LEFT JOIN LATERAL (SELECT n.tick_size FROM market.instruments AS n"
    "   WHERE n.market = q.market AND n.symbol = q.symbol"
    "   ORDER BY n.observed_at < q.minute_start + interval '1 minute' DESC,"
    "   n.observed_at DESC LIMIT 1) AS i ON true"
    " WHERE q.channel = 'trade'"
).format(active=_ACTIVE, latest=_LATEST_WS)
_EVIDENCE_OF_MINUTE = _EVIDENCE + sql.SQL(
    " AND q.market = %s AND q.symbol = %s AND q.minute_start = %s"
)
_PENDING = _EVIDENCE + sql.SQL(
    " AND q.minute_start >= %s AND c.open_time IS NULL"
    " AND NOT EXISTS (SELECT 1 FROM market.minute_status AS s WHERE s.market = q.market"
    "   AND s.symbol = q.symbol AND s.minute_start = q.minute_start)"
    " ORDER BY q.market, q.symbol, q.minute_start"
)


def _evidence(row: tuple) -> tuple[FlowMinute, Ohlcv | None, Ohlcv | None, bool]:
    """Flow, REST candle, latest WebSocket candle and ``active`` of an ``_EVIDENCE`` row
    (the tick size is ``row[17]``)."""
    state, messages, anomalies, reconnections = row[:4]
    market, symbol, minute = row[18:21]
    flow = FlowMinute(
        market, symbol, "trade", minute, FlowState(state), messages, anomalies, reconnections
    )
    rest = Ohlcv(minute, *row[4:10]) if row[4] is not None else None
    ws = Ohlcv(minute, *row[10:16]) if row[10] is not None else None
    return flow, rest, ws, bool(row[16])


def minute_statuses(
    conn: psycopg.Connection, market: str, symbol: str, day: datetime
) -> dict[datetime, tuple[str, str]]:
    """What each minute of a closed day counts as (02 §3.5): its terminal status and candle
    state, as written, except for a minute in which the WebSocket saw a trade or a candle
    after it was closed. Such a minute is decided again with everything the database holds
    (its latest WebSocket candle, and whether the WebSocket ever saw trading in it) and
    counts as the worse of the two results (02 §3.6): a discrepancy found by either stands,
    and a probable absence of trades that the WebSocket contradicts is ``sin_explicar``,
    still pending (reviews of E2, fourth to sixth rounds). Readers use this, never
    ``minute_status`` alone."""
    end = day + timedelta(days=1)
    statuses = {
        row[0]: (row[1], row[2])
        for row in conn.execute(
            "SELECT minute_start, status, candle_state FROM market.minute_status"
            " WHERE market = %s AND symbol = %s AND minute_start >= %s AND minute_start < %s",
            (market, symbol, day, end),
        ).fetchall()
    }
    for minute in sorted(_late_minutes(conn, market, symbol, day, end) & set(statuses)):
        row = conn.execute(_EVIDENCE_OF_MINUTE, (market, symbol, minute)).fetchone()
        if row is None or row[17] is None:
            continue  # no flow row or no tick size: nothing to decide it again with
        flow, rest, ws, active = _evidence(row)
        again = decide(market, symbol, flow, rest, ws, row[17], active=active)
        status, candle_state = statuses[minute]
        if again.has_candle and CandleState.DISCREPANTE.value in (candle_state, again.state):
            statuses[minute] = (status, CandleState.DISCREPANTE.value)
        elif again.state is CandleState.PENDIENTE and status == "sin_operaciones_probable":
            statuses[minute] = ("sin_explicar", CandleState.PENDIENTE.value)
    return statuses


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
    trade flow row since ``since`` with neither a candle nor a terminal status, with its
    latest WebSocket candle (stored with it or arrived after it closed), whether the
    WebSocket ever saw trading in it besides the trades in time, and the tick size in force
    when the minute ended (reviews of E2, fourth to sixth rounds)."""
    rows = conn.execute(_PENDING, (since,)).fetchall()
    pending = []
    for row in rows:
        tick_size = row[17]
        if tick_size is None:
            continue  # never recorded: the recorder stores the reference data first
        flow, _rest, ws, active = _evidence(row)
        pending.append(PendingCandle(flow, ws, tick_size, active=active))
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


def unclosed_days(conn: psycopg.Connection, since: datetime, before: datetime) -> list[datetime]:
    """UTC days in [since, before) with a recorded trade minute that has no terminal status
    yet: their close was interrupted or was still waiting for a candle when the process
    stopped (review of E2, third round)."""
    rows = conn.execute(
        "SELECT DISTINCT date_trunc('day', q.minute_start, 'UTC') AS day"
        " FROM market.data_quality_minute AS q"
        " WHERE q.channel = 'trade' AND q.minute_start >= %s AND q.minute_start < %s"
        " AND NOT EXISTS (SELECT 1 FROM market.minute_status AS s WHERE s.market = q.market"
        "   AND s.symbol = q.symbol AND s.minute_start = q.minute_start)"
        " ORDER BY day",
        (since, before),
    ).fetchall()
    return [row[0].astimezone(UTC) for row in rows]


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
