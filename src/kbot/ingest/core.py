"""The recorder's core: routes messages and connection events, and says what to store.

It has no clock, no network and no database. Every time is an argument, so the whole
recorder can be driven through simulated hours in a test. The asynchronous shell
(``kbot.ingest.service``) feeds it from the two public WebSocket connections and the REST
client, and writes what ``tick`` returns.

Per instrument of the universe (02 §3.8):

* depth channels of 5 levels, and of 50 for the candidates (``DepthChannel``);
* the flow of the ``trade`` and ``kline`` channels minute by minute (``EventFlow``) and the
  last WebSocket candle of each minute (``WsCandles``);
* in Futures, the own mark and index candles (``MarkCandleBuilder``);
* one gap tracker per channel (``GapTracker``).

A minute of the last series waits for its REST candle (``candle_requests`` and
``apply_rest_candles``); its WebSocket candle goes to the database with its flow, so a
restart resumes the wait from the database (``restore_awaiting``, audit E2-01). An index
minute without a candle from the flow waits for its rebuild from Classic
(``index_rebuilds`` in the tick output).

The universe changes at a UTC midnight (``apply_universe``), or at the moment of a late
evaluation when the recorder was stopped or suspended across midnight: an instrument that
leaves stops producing rows at that instant, and its pending REST candles are still
decided; one that enters starts at that instant. The day close covers every instrument
that was in the universe at some moment of the day, once the core has closed all its
minutes of the day (``closed_through``).

Closing after a proof of life protects against disconnections but does not prove that
everything arrived (``_proven``). What still arrives for a closed interval is never
discarded: late books (``LateBook``) go to the database with the next tick (review of E2,
fourth round), and so does every trade or WebSocket candle of a closed minute
(``LateTrade``, ``LateWsCandle``). The database keeps the ones that change what the minute
can be decided as, judged from what it holds, so a restart changes nothing (fifth round).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from kbot.kucoin.ws import Subscription
from kbot.market.depth import DepthChannel, LateBook, MinuteSnapshot, SlotResult, slot_start
from kbot.market.gaps import DataGap, GapCause, GapTracker, cause_from_state_detail
from kbot.market.last_candles import (
    STABILIZATION,
    CandleDecision,
    CandleState,
    EventFlow,
    FlowMinute,
    LateEvidence,
    LateTrade,
    LateWsCandle,
    Ohlcv,
    PendingCandle,
    WsCandle,
    WsCandles,
    candle_changes,
    decide,
    trade_changes,
    ws_kline,
)
from kbot.market.mark_candles import (
    FlowState,
    MarkCandleBuilder,
    MinuteResult,
    OwnCandle,
    positive_price,
)
from kbot.market.timeutil import (
    MINUTE,
    as_utc,
    floor_to,
    from_nanoseconds,
    is_int,
)

MARKETS = ("SPOT", "FUTURES")
LATENESS = timedelta(seconds=2)  # provisional until D26
MAX_BACKFILL = timedelta(days=1)
MAX_CANDLE_ATTEMPTS = 5  # answers from KuCoin without the candle, at most
# Minutes from the first to the last of one REST request. The window asked for is one
# minute wider on each side, so at most 102 candles: below the 200 of Futures (02 §3.2).
MAX_MINUTES_PER_REQUEST = 100
# Without a REST candle yet, these may still get one: KuCoin warns that its REST candles
# can be incomplete, so the minute is asked again before it is given up.
RETRIED = frozenset({CandleState.PENDIENTE, CandleState.AUSENCIA_NO_VERIFICABLE})
RETRY_INTERVAL = timedelta(minutes=1)
# A failed request (network, KuCoin error or an unreadable answer) is not an answer and
# never uses up an attempt: the candle is recoverable history (02 §3.7). It is asked again
# with a growing pause, and the minute stays pending for this long after it.
RECOVERY_HORIZON = timedelta(hours=48)
MAX_FAILURE_PAUSE = timedelta(minutes=15)


@dataclass(frozen=True)
class InstrumentSpec:
    market: str
    symbol: str
    tick_size: Decimal
    multiplier: Decimal
    l50: bool
    index_symbol: str | None = None  # Futures: the Classic index of the contract

    def channels(self) -> tuple[str, ...]:
        names = ["obu5", *(["obu50"] if self.l50 else []), "trade", "kline"]
        if self.market == "FUTURES":
            names.append("mark-price")
        return tuple(names)


@dataclass(frozen=True)
class CandleRequest:
    market: str
    symbol: str
    first: datetime  # first minute to decide
    last: datetime  # last minute to decide


@dataclass(frozen=True)
class IndexRebuild:
    symbol: str
    index_symbol: str
    minute: datetime
    flow_state: FlowState  # of the mark-price minute: S2 or S3


@dataclass
class TickOutput:
    """What one tick closed, in the order the foreign keys need it written."""

    flows: list[FlowMinute] = field(default_factory=list)
    ws_candles: list[WsCandle] = field(default_factory=list)
    own_candles: list[OwnCandle] = field(default_factory=list)
    slots: list[SlotResult] = field(default_factory=list)
    minute_snapshots: list[MinuteSnapshot] = field(default_factory=list)
    gaps: list[DataGap] = field(default_factory=list)
    index_rebuilds: list[IndexRebuild] = field(default_factory=list)
    # Evidence for intervals closed in earlier ticks, so their rows are already written. The
    # minute evidence keeps the order of arrival: each one is judged after the previous.
    late_books: list[LateBook] = field(default_factory=list)
    late_minutes: list[LateEvidence] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.flows
            or self.ws_candles
            or self.own_candles
            or self.slots
            or self.minute_snapshots
            or self.gaps
            or self.index_rebuilds
            or self.late_books
            or self.late_minutes
        )


@dataclass
class _Awaiting:
    flow: FlowMinute
    ws: Ohlcv | None  # the latest WebSocket candle of the minute, in time or late
    tick_size: Decimal  # of the instrument when the minute was recorded
    attempts: int = 0  # answers without the candle
    failures: int = 0  # requests without an answer
    next_try: datetime | None = None
    seen_late: bool = False  # a late trade that changes the minute (trade_changes)
    ws_published: datetime | None = None  # P of ``ws``


@dataclass
class _Instrument:
    spec: InstrumentSpec
    depth: dict[int, DepthChannel]
    trade: EventFlow
    kline: EventFlow
    ws_candles: WsCandles
    mark: MarkCandleBuilder | None
    gaps: dict[str, GapTracker]
    retired_at: datetime | None = None

    def finished(self) -> bool:
        """A retired instrument has closed every slot and minute that ends by the moment it
        was retired. The slot and the minute that contain that moment, when a late
        evaluation retires it between their bounds, never close."""
        at = self.retired_at
        if at is None:
            return False
        minute = floor_to(at, MINUTE)
        minutes = [self.trade.next_minute, self.kline.next_minute]
        if self.mark is not None:
            minutes.append(self.mark.next_minute)
        return min(minutes) >= minute and all(
            d.next_slot >= slot_start(at) for d in self.depth.values()
        )


ResumePoints = Mapping[tuple[str, str, str], datetime | None]


def _new_instrument(
    spec: InstrumentSpec,
    *,
    started_at: datetime,
    resume: ResumePoints,
    resume_cause: GapCause,
    max_backfill: timedelta,
) -> _Instrument:
    if spec.market not in MARKETS:
        raise ValueError(f"unknown market: {spec.market!r}")
    oldest = floor_to(started_at - max_backfill, MINUTE)

    def point(channel: str) -> datetime | None:
        value = resume.get((spec.market, spec.symbol, channel))
        return as_utc(value, "resume") if value is not None else None

    def start(channel: str) -> datetime:
        value = point(channel)
        if value is None or value >= started_at:
            return started_at
        return max(value, oldest)

    depth = {
        level: DepthChannel(
            spec.market,
            spec.symbol,
            level,
            multiplier=spec.multiplier,
            started_at=start(f"obu{level}"),
        )
        for level in ((5, 50) if spec.l50 else (5,))
    }
    return _Instrument(
        spec=spec,
        depth=depth,
        trade=EventFlow(spec.market, spec.symbol, "trade", started_at=start("trade")),
        kline=EventFlow(spec.market, spec.symbol, "kline", started_at=start("kline")),
        ws_candles=WsCandles(),
        mark=(
            MarkCandleBuilder(spec.symbol, started_at=start("mark-price"))
            if spec.market == "FUTURES"
            else None
        ),
        gaps={
            channel: GapTracker(
                spec.market,
                channel,
                spec.symbol,
                started_at=started_at,
                resumed_from=point(channel),
                resume_cause=resume_cause,
            )
            for channel in spec.channels()
        },
    )


class RecorderCore:
    def __init__(
        self,
        specs: Iterable[InstrumentSpec],
        *,
        started_at: datetime,
        resume: ResumePoints | None = None,
        resume_cause: GapCause = GapCause.PROCESO_DETENIDO,
        max_backfill: timedelta = MAX_BACKFILL,
        lateness: timedelta = LATENESS,
    ) -> None:
        """``resume`` maps (market, symbol, channel) to where the previous run stopped
        recording that channel; the trackers resume there, so the stop is closed as a gap
        and its slots as ``desconectado``. Trackers go back at most ``max_backfill``: older
        minutes get their S3 rows when their day is closed, and the gap row still covers
        the whole stop."""
        started_at = as_utc(started_at, "started_at")
        self.lateness = lateness
        self.max_backfill = max_backfill
        self.anomalies = 0
        self._instruments: dict[tuple[str, str], _Instrument] = {}
        self._retired: list[_Instrument] = []
        self._closed_gaps: list[DataGap] = []
        # Minutes waiting for their REST candle, per (market, symbol): kept apart from the
        # instruments, so they are decided even after their instrument left the universe.
        self._awaiting: dict[tuple[str, str], dict[datetime, _Awaiting]] = {}
        self._late_minutes: list[LateEvidence] = []
        # Per connection: whether it is up, and its last proof of life (any message or pong
        # received). While it is up, nothing is closed beyond that proof (``_proven``).
        self._up: dict[str, bool] = dict.fromkeys(MARKETS, False)
        self._alive: dict[str, datetime | None] = dict.fromkeys(MARKETS, None)
        for spec in specs:
            key = (spec.market, spec.symbol)
            if key in self._instruments:
                raise ValueError(f"instrument listed twice: {key}")
            self._instruments[key] = _new_instrument(
                spec,
                started_at=started_at,
                resume=resume or {},
                resume_cause=resume_cause,
                max_backfill=max_backfill,
            )

    # --- what to record --------------------------------------------------------------------

    @property
    def specs(self) -> list[InstrumentSpec]:
        return [instrument.spec for instrument in self._instruments.values()]

    def subscriptions(self, market: str) -> list[Subscription]:
        result: list[Subscription] = []
        for instrument in self._instruments.values():
            spec = instrument.spec
            if spec.market != market:
                continue
            result.append(Subscription("obu", market, spec.symbol, (("depth", "5"),)))
            if spec.l50:
                result.append(Subscription("obu", market, spec.symbol, (("depth", "50"),)))
            result.append(Subscription("trade", market, spec.symbol))
            result.append(Subscription("kline", market, spec.symbol, (("interval", "1min"),)))
            if market == "FUTURES":
                result.append(Subscription("mark-price", market, spec.symbol))
        return result

    def apply_universe(self, specs: Iterable[InstrumentSpec], at: datetime) -> bool:
        """Record exactly ``specs`` from ``at`` on: a UTC midnight, or the moment of a late
        evaluation after a stop across midnight. An instrument whose spec changes (for
        example it becomes a depth-50 candidate) is retired and added again. Returns
        whether anything changed, so the caller renews its subscriptions."""
        at = as_utc(at, "at")
        wanted = {(s.market, s.symbol): s for s in specs}
        changed = False
        for key, instrument in list(self._instruments.items()):
            if wanted.get(key) == instrument.spec:
                continue
            changed = True
            instrument.retired_at = at
            for tracker in instrument.gaps.values():
                gap = tracker.up(at)  # an open gap ends where recording ends
                if gap is not None:
                    self._closed_gaps.append(gap)
            self._retired.append(instrument)
            del self._instruments[key]
        for key, spec in wanted.items():
            if key not in self._instruments:
                changed = True
                self._instruments[key] = _new_instrument(
                    spec,
                    started_at=at,
                    resume={},
                    resume_cause=GapCause.DESCONEXION,
                    max_backfill=self.max_backfill,
                )
        return changed

    # --- events ----------------------------------------------------------------------------

    def on_message(self, market: str, message: dict[str, Any], received_at: datetime) -> None:
        received_at = as_utc(received_at, "received_at")
        self._prove_alive(market, received_at)
        topic = str(message.get("T", ""))
        data = message.get("d")
        symbol = data.get("s") if isinstance(data, dict) else None
        instrument = self._instruments.get((market, symbol)) if isinstance(symbol, str) else None
        if instrument is None or not isinstance(data, dict):
            self.anomalies += 1
            return
        if topic == f"obu.{market}":
            level = {"5": 5, "50": 50}.get(str(message.get("dp")))
            depth = instrument.depth.get(level) if level is not None else None
            if depth is None:
                self.anomalies += 1
                return
            depth.on_message(message, received_at)
        elif topic == f"trade.{market}":
            # A trade counts in the minute of its match time M, as a candle does in its own
            # (review of E2, fourth round); a malformed one, where it arrived.
            valid = _valid_trade(data)
            matched = floor_to(from_nanoseconds(data["M"]), MINUTE) if valid else None
            instrument.trade.message(received_at, valid=valid, minute=matched)
            if matched is not None and matched < instrument.trade.next_minute:
                self._late_trade(instrument, matched, received_at)
        elif topic == f"kline.{market}":
            candle, published = ws_kline(message), message.get("P")
            if candle is None or not is_int(published):
                instrument.kline.message(received_at, valid=False)
            else:
                instrument.kline.message(received_at, valid=True, minute=candle.open_time)
                if candle.open_time < instrument.trade.next_minute:
                    self._late_candle(instrument, candle, from_nanoseconds(published), received_at)
                else:
                    instrument.ws_candles.add(message)
        elif topic == "mark-price" and instrument.mark is not None:
            instrument.mark.on_message(message)
        else:
            self.anomalies += 1

    def _late_trade(self, instrument: _Instrument, minute: datetime, received_at: datetime) -> None:
        """A trade of a minute already closed, here or by the previous run. It goes to the
        database, which keeps it if it changes the minute (``trade_changes``) judging from
        what it holds, so the answer is the same after a restart (review of E2, fifth
        round). A minute still waiting for its REST candle is decided with it."""
        spec = instrument.spec
        awaiting = self._awaiting.get((spec.market, spec.symbol), {}).get(minute)
        if awaiting is not None and trade_changes(awaiting.flow, awaiting.ws):
            awaiting.seen_late = True
        self._late_minutes.append(LateTrade(spec.market, spec.symbol, minute, received_at))

    def _late_candle(
        self, instrument: _Instrument, candle: Ohlcv, published: datetime, received_at: datetime
    ) -> None:
        """A WebSocket candle of a minute already closed. Like a late trade, the database
        keeps it if it is newer than, and different from, the latest one it holds for the
        minute (``candle_changes``); a minute still waiting for its REST candle is decided
        with it, as with any later candle of the minute."""
        spec = instrument.spec
        awaiting = self._awaiting.get((spec.market, spec.symbol), {}).get(candle.open_time)
        if awaiting is not None and candle_changes(
            awaiting.flow, awaiting.ws, awaiting.ws_published, candle, published
        ):
            awaiting.ws, awaiting.ws_published = candle, published
        self._late_minutes.append(
            LateWsCandle(spec.market, spec.symbol, candle, published, received_at)
        )

    def on_alive(self, market: str, at: datetime) -> None:
        """A control message (pong, acknowledgement or welcome) received at ``at``: a proof
        of life of the connection even when no market data arrives."""
        self._prove_alive(market, as_utc(at, "at"))

    def on_stall(self, start: datetime, end: datetime, detail: str) -> None:
        """The process did not run from ``start`` to ``end`` (review of E2, third round).

        A connection that was up is recorded down from its last proof of life (never later
        than ``start``) to ``end``, as ``proceso_detenido``, and up again at ``end``; nothing
        after ``end`` counts as healthy until the connection proves itself alive again. A
        connection that was already down stays down: only its stream can say it is back."""
        start, end = as_utc(start, "start"), as_utc(end, "end")
        for market in MARKETS:
            if not self._up[market]:
                continue
            alive = self._alive[market]
            down_from = min(start, alive) if alive is not None else start
            self.on_state(market, "disconnected", down_from, detail)
            self.on_state(market, "connected", end, "stall over")

    def _prove_alive(self, market: str, at: datetime) -> None:
        if self._up.get(market):
            current = self._alive.get(market)
            if current is None or at > current:
                self._alive[market] = at

    def on_state(self, market: str, state: str, at: datetime, detail: str) -> None:
        at = as_utc(at, "at")
        instruments = [i for i in self._instruments.values() if i.spec.market == market]
        if state == "connected":
            self._up[market] = True
            self._alive[market] = at
            for instrument in instruments:
                for depth in instrument.depth.values():
                    depth.connection_up(at)
                instrument.trade.connection_up(at)
                instrument.kline.connection_up(at)
                if instrument.mark is not None:
                    instrument.mark.connection_up(at)
                for tracker in instrument.gaps.values():
                    gap = tracker.up(at)
                    if gap is not None:
                        self._closed_gaps.append(gap)
        elif state == "disconnected":
            self._up[market] = False
            cause = cause_from_state_detail(detail)
            for instrument in instruments:
                for depth in instrument.depth.values():
                    depth.connection_down(at)
                instrument.trade.connection_down(at)
                instrument.kline.connection_down(at)
                if instrument.mark is not None:
                    instrument.mark.connection_down(at)
                for tracker in instrument.gaps.values():
                    tracker.down(at, cause, detail[:200])
        else:
            self.anomalies += 1

    # --- the passing of time -----------------------------------------------------------

    def tick(self, now: datetime) -> TickOutput:
        now = as_utc(now, "now")
        out = TickOutput(gaps=self._closed_gaps, late_minutes=self._late_minutes)
        self._closed_gaps = []
        self._late_minutes = []
        for instrument in [*self._instruments.values(), *self._retired]:
            for depth in instrument.depth.values():  # before closing: their slots are written
                out.late_books += depth.take_late_books()
            until = self._proven(instrument.spec.market, now)
            if instrument.retired_at is not None:
                until = min(until, instrument.retired_at + self.lateness)
            self._close(instrument, until, out)
        self._retired = [i for i in self._retired if not i.finished()]
        return out

    def _proven(self, market: str, now: datetime) -> datetime:
        """How far the trackers of ``market`` may close (review of E2, third round).

        While its connection is up, only up to its last proof of life: a slot or a minute
        closes once a message or a pong arrived at least the lateness margin after its end.
        A disconnection is dated from the last proof of life, so it can never fall on a
        result already written as healthy. While it is down, up to ``now``: nothing healthy
        is claimed.

        It does not prove that everything published in the interval has arrived: KuCoin
        does not document that a pong leaves after everything published before it (review
        of E2, fourth round). What still arrives for a closed interval goes to the database
        (``LateBook``, ``LateTrade``, ``LateWsCandle``) and, where it contradicts what was
        written, lowers what that interval counts as without changing its rows."""
        alive = self._alive.get(market)
        if not self._up.get(market) or alive is None:
            return now
        return min(now, alive)

    def _tracking(self, market: str, symbol: str) -> list[_Instrument]:
        """The instrument recorded now, if any, and any retired one still closing."""
        current = self._instruments.get((market, symbol))
        retired = [i for i in self._retired if (i.spec.market, i.spec.symbol) == (market, symbol)]
        return [current, *retired] if current is not None else retired

    def closed_through(self, market: str, symbol: str, moment: datetime) -> bool:
        """Whether the trade and kline flows of the instrument have closed every minute
        before ``moment`` that they will ever close (review of E2, fourth round).

        The day close fills with S3 the minutes that have no flow row because nothing was
        recording them; it waits for this, so it never takes a minute that the core has not
        closed yet (for example one still waiting for its proof of life) for one that
        nobody recorded. A retired instrument counts up to the minute in which it was
        retired; an instrument the core does not record has nothing left to close."""
        moment = as_utc(moment, "moment")
        for instrument in self._tracking(market, symbol):
            target = moment
            if instrument.retired_at is not None:
                target = min(target, floor_to(instrument.retired_at, MINUTE))
            if min(instrument.trade.next_minute, instrument.kline.next_minute) < target:
                return False
        return True

    def _close(self, instrument: _Instrument, now: datetime, out: TickOutput) -> None:
        spec = instrument.spec
        for depth in instrument.depth.values():
            slots, minutes = depth.close_until(now, self.lateness)
            out.slots += slots
            out.minute_snapshots += minutes
        out.flows += instrument.kline.close_until(now, self.lateness)
        for flow in instrument.trade.close_until(now, self.lateness):
            out.flows.append(flow)
            taken = instrument.ws_candles.take(flow.minute)
            ws, published_at = None, None
            if taken is not None:
                published_at, ws = taken
                out.ws_candles.append(WsCandle(spec.market, spec.symbol, ws, published_at))
            self._awaiting.setdefault((spec.market, spec.symbol), {})[flow.minute] = _Awaiting(
                flow, ws, spec.tick_size, ws_published=published_at
            )
        # A WebSocket candle of a minute whose flow is already closed is late evidence now
        # (``_late_candle``); none is left here.
        closed = instrument.trade.next_minute
        for minute in [m for m in instrument.ws_candles.latest if m < closed]:
            instrument.ws_candles.pop(minute)
        if instrument.mark is not None:
            for result in instrument.mark.close_until(now, self.lateness):
                out.flows.append(_mark_flow(spec, result))
                if result.mark is not None and result.index is not None:
                    out.own_candles += [result.mark, result.index]
                elif spec.index_symbol is not None:
                    out.index_rebuilds.append(
                        IndexRebuild(
                            spec.symbol, spec.index_symbol, result.minute, result.flow_state
                        )
                    )

    # --- REST candles of the last series ---------------------------------------------------

    def restore_awaiting(self, pending: Iterable[PendingCandle]) -> int:
        """Wait again for the minutes the previous run left without a REST candle, as the
        database holds them: flow, latest WebSocket candle, late trade and tick size (audit
        E2-01, review of E2, fifth round). A minute already waiting is kept as it is.
        Returns how many minutes were added."""
        added = 0
        for item in pending:
            flow = item.flow
            minutes = self._awaiting.setdefault((flow.market, flow.symbol), {})
            if flow.minute not in minutes:
                minutes[flow.minute] = _Awaiting(
                    flow,
                    item.ws,
                    item.tick_size,
                    seen_late=item.seen_late,
                    ws_published=item.ws_published,
                )
                added += 1
        return added

    def awaiting_minutes(self, market: str, symbol: str) -> list[datetime]:
        return sorted(self._awaiting.get((market, symbol), {}))

    def candle_requests(self, now: datetime) -> list[CandleRequest]:
        """Requests for the minutes whose stabilization margin has passed. One request
        covers at most ``MAX_MINUTES_PER_REQUEST`` minutes from its first to its last,
        however few of them wait, so minutes far apart never make a window too wide."""
        now = as_utc(now, "now")
        requests = []
        for (market, symbol), pending in self._awaiting.items():
            due = sorted(
                m
                for m, a in pending.items()
                if m + MINUTE + STABILIZATION <= now and (a.next_try is None or a.next_try <= now)
            )
            chunk: list[datetime] = []
            for minute in due:
                if chunk and minute - chunk[0] >= MAX_MINUTES_PER_REQUEST * MINUTE:
                    requests.append(CandleRequest(market, symbol, chunk[0], chunk[-1]))
                    chunk = []
                chunk.append(minute)
            if chunk:
                requests.append(CandleRequest(market, symbol, chunk[0], chunk[-1]))
        return requests

    def apply_rest_candles(
        self, request: CandleRequest, rows: list[Ohlcv] | None, now: datetime
    ) -> list[CandleDecision]:
        """Decide the awaiting minutes of ``request``. ``rows`` is the full REST answer for
        the requested range, or None if the request failed.

        An answer without the candle of a minute that may still get one
        (``pendiente_estabilizacion`` or ``ausencia_no_verificable``) is an attempt: the
        minute is asked again a minute later, and after ``MAX_CANDLE_ATTEMPTS`` answers it
        is decided without a candle. A failed request is not an answer: the minutes are
        asked again after a growing pause (1, 2, 4 and 8 minutes, then every 15), for up to
        ``RECOVERY_HORIZON`` after each minute, and only then given up. Meanwhile they stay
        pending, so their day is not closed (``kbot.ingest.service``)."""
        now = as_utc(now, "now")
        key = (request.market, request.symbol)
        pending = self._awaiting.get(key, {})
        decisions: list[CandleDecision] = []
        by_minute = {row.open_time: row for row in rows} if rows is not None else {}
        for minute in [m for m in sorted(pending) if request.first <= m <= request.last]:
            awaiting = pending[minute]
            if rows is None:
                awaiting.failures += 1
                pause = RETRY_INTERVAL * 2 ** min(awaiting.failures - 1, 4)
                awaiting.next_try = now + min(pause, MAX_FAILURE_PAUSE)
                if now - minute >= RECOVERY_HORIZON:
                    del pending[minute]  # no answer in 48 h: its day closes as it is
                continue
            awaiting.attempts += 1
            awaiting.next_try = now + RETRY_INTERVAL
            decision = decide(
                request.market,
                request.symbol,
                awaiting.flow,
                by_minute.get(minute),
                awaiting.ws,
                awaiting.tick_size,
                seen_late=awaiting.seen_late,
            )
            if decision.state in RETRIED and awaiting.attempts < MAX_CANDLE_ATTEMPTS:
                continue
            del pending[minute]
            decisions.append(decision)
        if not pending:
            self._awaiting.pop(key, None)
        return decisions

    def awaiting_until(self, moment: datetime) -> int:
        """Minutes before ``moment`` still waiting for their REST candle."""
        return sum(
            1 for pending in self._awaiting.values() for minute in pending if minute < moment
        )


def _valid_trade(data: dict[str, Any]) -> bool:
    matched = data.get("M")  # match time, ns [H, E1 recording]
    return (
        positive_price(data.get("p")) is not None
        and positive_price(data.get("q")) is not None
        and data.get("S") in ("buy", "sell")
        and is_int(matched)
        and 0 <= matched < 2**63  # a 64-bit time, so it always gives a date
    )


def _mark_flow(spec: InstrumentSpec, result: MinuteResult) -> FlowMinute:
    clean = result.flow_state is FlowState.S1
    return FlowMinute(
        market=spec.market,
        symbol=spec.symbol,
        channel="mark-price",
        minute=result.minute,
        flow_state=result.flow_state,
        messages=result.samples + result.anomalies,
        anomalies=result.anomalies,
        reconnections=0 if clean or not result.disconnected else 1,
    )
