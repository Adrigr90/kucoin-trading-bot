"""Candles of the last price: flow, evidence and the terminal status of each minute.

Spec 02 §3.4 and §3.5, and the owner decision of 8 November 2026 (provisional until D26):

* the official candle is the REST one, asked for 15 s after the minute ends;
* with a continuous flow (S1) it is compared with the labelled WebSocket candle of the same
  minute: any price more than one tick apart makes it ``discrepante``; volume and turnover
  of both are kept but do not judge the candle until D26 sets their tolerances;
* with a degraded flow (S2 or S3) the REST candle stands alone (``cerrada_rest``);
* without a REST candle, the minute is ``ausencia_sin_trades_probable`` only on S1 with
  neither a WebSocket candle nor a trade, not even one that arrived after the minute was
  closed (``LateTrade``, ``LateWsCandle``), ``ausencia_no_verificable`` on S2 or S3, and
  stays pending (``pendiente_estabilizacion``) on S1 when the WebSocket saw trading: KuCoin
  warns that its REST candles may be incomplete, so the bot waits and never builds it.

``day_close`` turns the decisions of a day into the terminal ``minute_status`` of §3.5.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from kbot.market.mark_candles import FlowState, positive_price
from kbot.market.timeutil import MINUTE, as_utc, floor_to, from_nanoseconds, is_int

STABILIZATION = timedelta(seconds=15)  # provisional until D26
DAY = timedelta(days=1)


class CandleState(StrEnum):
    CERRADA_REST = "cerrada_rest"
    CERRADA_CRUZADA = "cerrada_cruzada"
    DISCREPANTE = "discrepante"
    AUSENCIA_SIN_TRADES = "ausencia_sin_trades_probable"
    AUSENCIA_NO_VERIFICABLE = "ausencia_no_verificable"
    ABIERTA = "abierta"
    PENDIENTE = "pendiente_estabilizacion"


WITH_CANDLE = frozenset({"cerrada_rest", "cerrada_cruzada", "discrepante"})


class CandleEvidence(StrEnum):
    CRUZADA_CONSISTENTE = "vela_cruzada_consistente"
    CRUZADA_DISCREPANTE = "vela_cruzada_discrepante"
    REST_SUFICIENTE = "vela_rest_suficiente"


class MinuteTerminal(StrEnum):
    DATOS = "datos"
    HUECO = "hueco"
    SIN_OPERACIONES = "sin_operaciones_probable"
    SIN_EXPLICAR = "sin_explicar"


@dataclass(frozen=True)
class Ohlcv:
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal


@dataclass(frozen=True)
class FlowMinute:
    """One row of ``data_quality_minute`` for an event channel (trade or kline)."""

    market: str
    symbol: str
    channel: str
    minute: datetime
    flow_state: FlowState
    messages: int
    anomalies: int
    reconnections: int


@dataclass(frozen=True)
class WsCandle:
    """The last labelled WebSocket candle of a minute, stored with the trade flow of that
    minute, so a restart decides its REST candle from the same evidence (audit E2-01)."""

    market: str
    symbol: str
    candle: Ohlcv
    published_at: datetime  # P of the message that carried it


@dataclass(frozen=True)
class LateTrade:
    """A trade of a minute (by its match time ``M``) received after the minute was closed.

    The recorder hands every one to the database, which keeps it (``trading_seen_late``)
    only if it changes what the minute can be decided as (``trade_changes``), from what the
    database holds: the same answer after a restart (reviews of E2, fourth and fifth)."""

    market: str
    symbol: str
    minute: datetime
    received_at: datetime


@dataclass(frozen=True)
class LateWsCandle:
    """A WebSocket candle of a minute received after the minute was closed. The database
    keeps it (``late_ws_candles``) only if it is newer than, and different from, the latest
    candle it holds for the minute (``candle_changes``)."""

    market: str
    symbol: str
    candle: Ohlcv
    published_at: datetime  # P of the message that carried it
    received_at: datetime


LateEvidence = LateTrade | LateWsCandle


def trade_changes(flow: FlowMinute, ws: Ohlcv | None) -> bool:
    """Whether a trade seen after its minute was closed changes what the minute can be
    decided as: the flow was S1, no trade of the minute arrived in time, and the latest
    WebSocket candle of the minute, if any, has volume 0. Otherwise the WebSocket had
    already seen trading (review of E2, fifth round: a candle of volume 0 is no trading)."""
    return flow.flow_state is FlowState.S1 and flow.messages == 0 and (ws is None or ws.volume == 0)


def candle_changes(
    flow: FlowMinute,
    ws: Ohlcv | None,
    ws_published: datetime | None,
    candle: Ohlcv,
    published: datetime,
) -> bool:
    """Whether a WebSocket candle seen after its minute was closed changes the latest one
    known: the flow was S1 (the WebSocket only counts on S1) and there was none, or this
    one is newer and different. KuCoin Futures sends the closing candle of a minute again
    with the first trade of the next one [H, E1 recording]: the same candle changes nothing."""
    if flow.flow_state is not FlowState.S1:
        return False
    if ws is None or ws_published is None:
        return True
    return published > ws_published and candle != ws


@dataclass(frozen=True)
class PendingCandle:
    """A closed minute that still waits for its REST candle, as the database holds it."""

    flow: FlowMinute
    ws: Ohlcv | None  # the latest WebSocket candle of the minute, in time or late
    tick_size: Decimal
    seen_late: bool = False  # a trade kept in trading_seen_late
    ws_published: datetime | None = None  # P of ``ws``


@dataclass(frozen=True)
class CandleDecision:
    market: str
    symbol: str
    minute: datetime
    flow_state: FlowState
    state: CandleState
    evidence: CandleEvidence | None
    rest: Ohlcv | None
    ws: Ohlcv | None
    differences: tuple[str, ...] = ()

    @property
    def has_candle(self) -> bool:
        return self.state in WITH_CANDLE


@dataclass(frozen=True)
class MinuteStatus:
    market: str
    symbol: str
    minute: datetime
    status: MinuteTerminal
    candle_state: CandleState
    flow_state: FlowState
    gap_start: datetime | None

    @property
    def candle_series(self) -> str | None:
        return "last" if self.status is MinuteTerminal.DATOS else None


def ws_kline(message: Mapping[str, Any]) -> Ohlcv | None:
    """The labelled 1-minute candle of a ``kline`` message, or None if it is not valid."""
    data = message.get("d") or {}
    opened, closed = data.get("O"), data.get("C")
    if not (is_int(opened) and is_int(closed)) or closed - opened != 60 or opened % 60:
        return None
    o, h, low, c = (positive_price(data.get(k)) for k in ("o", "h", "l", "c"))
    volume, turnover = _non_negative(data.get("v")), _non_negative(data.get("a"))
    if None in (o, h, low, c, volume, turnover):
        return None
    candle = Ohlcv(from_nanoseconds(opened * 10**9), o, h, low, c, volume, turnover)  # type: ignore[arg-type]
    return candle if _consistent(candle) else None


def rest_kline(row: object) -> Ohlcv | None:
    """A REST V2 row ``[open time (s), o, h, l, c, volume, turnover]`` (columns: E1)."""
    if not (isinstance(row, list) and len(row) == 7 and is_int(row[0]) and row[0] % 60 == 0):
        return None
    o, h, low, c = (positive_price(v) for v in row[1:5])
    volume, turnover = _non_negative(row[5]), _non_negative(row[6])
    if None in (o, h, low, c, volume, turnover):
        return None
    candle = Ohlcv(from_nanoseconds(row[0] * 10**9), o, h, low, c, volume, turnover)  # type: ignore[arg-type]
    return candle if _consistent(candle) else None


class EventFlow:
    """Flow state of an event channel (trades, klines) of one instrument, minute by minute.

    S1: the connection was healthy during the whole minute and no message was malformed;
    S3: it was down during the whole minute; S2: anything in between (02 §3.4). Silence on
    a healthy connection is not a fault in an event channel: nothing happened.
    """

    def __init__(self, market: str, symbol: str, channel: str, *, started_at: datetime) -> None:
        started_at = as_utc(started_at, "started_at")
        self.market, self.symbol, self.channel = market, symbol, channel
        self._down: list[tuple[datetime, datetime | None]] = [(started_at, None)]
        self._counts: dict[datetime, list[int]] = {}
        self._next = floor_to(started_at, MINUTE)

    @property
    def next_minute(self) -> datetime:
        """The first minute not closed yet."""
        return self._next

    def connection_up(self, at: datetime) -> None:
        at = as_utc(at, "at")
        if self._down and self._down[-1][1] is None:
            self._down[-1] = (self._down[-1][0], at)

    def connection_down(self, at: datetime) -> None:
        at = as_utc(at, "at")
        if not self._down or self._down[-1][1] is not None:
            self._down.append((at, None))

    def message(
        self, received_at: datetime, *, valid: bool, minute: datetime | None = None
    ) -> None:
        """Count a message in the minute it was received, or in ``minute`` when its content
        names one (a candle counts in its own minute, a trade in that of its match time). A
        minute already closed is not changed (its row is written); what a late message
        shows is handed to the database by the recorder (``LateTrade``, ``LateWsCandle``)."""
        received_at = as_utc(received_at, "received_at")
        minute = floor_to(received_at if minute is None else as_utc(minute, "minute"), MINUTE)
        if minute < self._next:
            return
        counts = self._counts.setdefault(minute, [0, 0])
        counts[0] += 1
        counts[1] += 0 if valid else 1

    def close_until(self, now: datetime, lateness: timedelta = timedelta(0)) -> list[FlowMinute]:
        """Close every minute whose end plus ``lateness`` is not after ``now``."""
        now = as_utc(now, "now")
        results = []
        while self._next + MINUTE + lateness <= now:
            minute, end = self._next, self._next + MINUTE
            messages, anomalies = self._counts.pop(minute, [0, 0])
            overlaps = [(s, e) for s, e in self._down if s < end and (e is None or e > minute)]
            if not overlaps and anomalies == 0:
                flow = FlowState.S1
            elif any(s <= minute and (e is None or e >= end) for s, e in overlaps):
                flow = FlowState.S3
            else:
                flow = FlowState.S2
            reconnections = sum(1 for s, e in overlaps if e is not None and minute <= e < end)
            results.append(
                FlowMinute(
                    self.market,
                    self.symbol,
                    self.channel,
                    minute,
                    flow,
                    messages,
                    anomalies,
                    reconnections,
                )
            )
            self._next = end
            self._down = [(s, e) for s, e in self._down if e is None or e > end]
        return results


@dataclass
class WsCandles:
    """The last labelled WebSocket candle of each minute (the one with the latest ``P``)."""

    latest: dict[datetime, tuple[int, Ohlcv]] = field(default_factory=dict)

    def add(self, message: Mapping[str, Any]) -> bool:
        candle, published = ws_kline(message), message.get("P")
        if candle is None or not is_int(published):
            return False
        current = self.latest.get(candle.open_time)
        if current is None or published > current[0]:
            self.latest[candle.open_time] = (published, candle)
        return True

    def pop(self, minute: datetime) -> Ohlcv | None:
        entry = self.latest.pop(minute, None)
        return entry[1] if entry else None

    def take(self, minute: datetime) -> tuple[datetime, Ohlcv] | None:
        """Remove the candle of ``minute`` and return it with the time KuCoin published it."""
        entry = self.latest.pop(minute, None)
        return (from_nanoseconds(entry[0]), entry[1]) if entry else None


def price_differences(rest: Ohlcv, ws: Ohlcv, tick: Decimal) -> tuple[str, ...]:
    """Prices more than one tick apart (N1-P, 02 §3.4)."""
    if tick <= 0:
        raise ValueError("tick must be positive")
    pairs = (
        ("open", rest.open, ws.open),
        ("high", rest.high, ws.high),
        ("low", rest.low, ws.low),
        ("close", rest.close, ws.close),
    )
    return tuple(name for name, a, b in pairs if abs(a - b) > tick)


def decide(
    market: str,
    symbol: str,
    flow: FlowMinute,
    rest: Ohlcv | None,
    ws: Ohlcv | None,
    tick: Decimal,
    *,
    seen_late: bool = False,
) -> CandleDecision:
    """The candle of one closed minute. ``flow`` is the minute of the ``trade`` channel;
    ``rest`` is None only when a successful REST answer covering the minute had no candle
    for it; a failed request is not a decision.

    The WebSocket saw trading in the minute if it sent a candle for it or a trade of it (by
    its match time ``M``): either one rules out a probable absence of trades, and either one
    against a REST candle of volume 0 is a contradiction (review of E2, third round). So
    does trading it showed only after the minute was closed (``seen_late``, fourth round)."""
    minute, state_of_flow = flow.minute, flow.flow_state
    for candle in (rest, ws):
        if candle is not None and candle.open_time != minute:
            raise ValueError("candle of another minute")
    # Trades of the minute received before it closed (on S1 all of them valid), or trading
    # seen in it afterwards.
    traded = flow.messages > 0 or seen_late
    differences: tuple[str, ...] = ()
    if rest is not None:
        rest_without_trades = rest.volume == 0 and rest.turnover == 0
        if state_of_flow is not FlowState.S1:
            state, evidence = CandleState.CERRADA_REST, CandleEvidence.REST_SUFICIENTE
            ws = None
        elif ws is None and rest_without_trades and not traded:
            # Spot: KuCoin publishes a flat zero-volume candle for a minute without trades
            # [H, measured 8 Nov 2026]; a continuous flow that saw no trades agrees with it.
            state, evidence = CandleState.CERRADA_CRUZADA, CandleEvidence.CRUZADA_CONSISTENTE
        elif ws is None:
            state, evidence = CandleState.DISCREPANTE, CandleEvidence.CRUZADA_DISCREPANTE
            differences = ("websocket_sin_vela",)
            if rest.volume == 0:
                differences = (*differences, "volumen_rest_cero")
        else:
            differences = price_differences(rest, ws, tick)
            if rest.volume == 0 and (ws.volume > 0 or traded):
                # Not a tolerance (D26): KuCoin says no trade happened, the flow saw some.
                differences = (*differences, "volumen_rest_cero")
            if differences:
                state, evidence = CandleState.DISCREPANTE, CandleEvidence.CRUZADA_DISCREPANTE
            else:
                state, evidence = (
                    CandleState.CERRADA_CRUZADA,
                    CandleEvidence.CRUZADA_CONSISTENTE,
                )
    elif state_of_flow is not FlowState.S1:
        state, evidence = CandleState.AUSENCIA_NO_VERIFICABLE, None
    elif ws is None and not traded:
        state, evidence = CandleState.AUSENCIA_SIN_TRADES, None
    else:
        state, evidence = CandleState.PENDIENTE, None
    return CandleDecision(
        market, symbol, minute, state_of_flow, state, evidence, rest, ws, differences
    )


def day_close(
    day: datetime,
    flows: Mapping[datetime, FlowState],
    candle_states: Mapping[datetime, CandleState],
    gaps: Iterable[tuple[datetime, datetime]],
    *,
    market: str,
    symbol: str,
) -> list[MinuteStatus]:
    """Terminal status of every minute of ``day`` for one instrument (02 §3.5).

    ``flows`` must hold the flow of every minute (the caller records S3 for the minutes in
    which nothing was running); ``candle_states`` the decided state of each minute (a minute
    without one is still ``abierta``); ``gaps`` the (start, end) of the trade-channel rows of
    ``data_gaps`` of the instrument. Everything comes from the database, so the close gives
    the same result after a restart.
    """
    day = as_utc(day, "day")
    if floor_to(day, DAY) != day:
        raise ValueError("day must be a UTC midnight")
    gap_list = sorted(gaps)
    results = []
    for index in range(24 * 60):
        minute = day + index * MINUTE
        flow = flows.get(minute)
        if flow is None:
            raise ValueError(f"no flow state for {minute.isoformat()}")
        candle_state = candle_states.get(minute, CandleState.ABIERTA)
        gap_start = None
        if candle_state in WITH_CANDLE:
            status = MinuteTerminal.DATOS
        elif candle_state is CandleState.AUSENCIA_SIN_TRADES:
            status = MinuteTerminal.SIN_OPERACIONES
        elif candle_state is CandleState.AUSENCIA_NO_VERIFICABLE:
            covering = [s for s, e in gap_list if s < minute + MINUTE and e > minute]
            if covering:
                status, gap_start = MinuteTerminal.HUECO, covering[0]
            else:
                status = MinuteTerminal.SIN_EXPLICAR
        else:
            status = MinuteTerminal.SIN_EXPLICAR
        results.append(MinuteStatus(market, symbol, minute, status, candle_state, flow, gap_start))
    return results


def _non_negative(raw: object) -> Decimal | None:
    """A finite decimal of at least zero from KuCoin text, or None."""
    if not isinstance(raw, str):
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value >= 0 else None


def _consistent(candle: Ohlcv) -> bool:
    return (
        candle.low
        <= min(candle.open, candle.close)
        <= max(candle.open, candle.close)
        <= (candle.high)
    )


def state_without_candle(
    flow: FlowState, kline_messages: int, trade_messages: int = 0, *, seen_late: bool = False
) -> CandleState:
    """State of a minute that has no candle row, from what the database recorded. On S1 the
    minute is still pending if the WebSocket saw anything in it (a candle or a trade, in
    time or after the minute was closed), and a probable absence of trades only when it saw
    nothing (review of E2, third and fourth rounds)."""
    if flow is not FlowState.S1:
        return CandleState.AUSENCIA_NO_VERIFICABLE
    seen = kline_messages > 0 or trade_messages > 0 or seen_late
    return CandleState.PENDIENTE if seen else CandleState.AUSENCIA_SIN_TRADES
