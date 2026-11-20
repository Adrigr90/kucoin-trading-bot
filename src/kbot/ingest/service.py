"""svc-ingest: the asynchronous shell around ``RecorderCore`` (02 §1.1 and §3.8).

``Recorder.run`` opens the two public WebSocket connections and calls ``step`` once a
second. ``step`` does everything that depends on the clock, from the time it is given:

* every second: close slots, minutes and gaps (``RecorderCore.tick``) and write them;
* 15 s after each minute: ask KuCoin for the REST candles and decide their evidence;
* index minutes without a candle from the flow: rebuild them from Classic, a few requests
  a minute;
* every 10 min the predicted funding, every hour the settled funding;
* at 23:59 UTC: evaluate tomorrow's universe and refresh the reference data (again 30 s
  later if KuCoin's listings are incomplete);
* at 00:00 UTC: switch to that universe (the streams are renewed if it changed); from
  00:00:15, close the day of each instrument once the core has closed all its minutes of
  the day and none of them waits for a REST candle that can still be recovered
  (``RECOVERY_HORIZON``).

At start-up the recorder resumes from the database what the previous run left pending in
the last 48 h: the REST candles of closed minutes and the index rebuilds (audit E2-01 and
E2-02). If it was stopped or suspended across midnight and missed the 23:59 evaluation,
the universe of the day already started is evaluated as soon as it runs again (E2-04).

Every database write runs in a worker thread, so a slow write never stops the WebSocket
connections from answering their pings. Every REST call goes through the guarded client
of E1, inside the machine-wide budget.

A stream or the watchdog that ends by itself stops the recorder, and systemd starts it
again; whatever the reason for stopping, the recorder first writes the gaps it leaves
open (incident of 17 November 2026).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
import structlog

from kbot.core.clock import Clock, SystemClock
from kbot.ingest.core import (
    MARKETS,
    RECOVERY_HORIZON,
    CandleRequest,
    IndexRebuild,
    InstrumentSpec,
    RecorderCore,
    TickOutput,
    candles_by_minute,
)
from kbot.ingest.sinks import Sink
from kbot.kucoin.rest import KucoinApiError, KucoinRestClient
from kbot.kucoin.ws import ConnectionPacer, PublicStream
from kbot.market.last_candles import STABILIZATION, Ohlcv, rest_kline
from kbot.market.mark_candles import IndexMinute, index_candle_from_points
from kbot.market.reference import (
    InstrumentRow,
    index_points,
    instrument_row,
    predicted_funding,
    risk_tier_rows,
    settled_funding,
)
from kbot.market.timeutil import MINUTE, as_utc, floor_to, to_milliseconds
from kbot.market.universe import UniverseEntry, evaluate

log = structlog.get_logger("kbot.ingest")

DAY = timedelta(days=1)
EVALUATION_TIME = timedelta(hours=23, minutes=59)  # tomorrow's universe, from today's data
EVALUATION_RETRY = timedelta(seconds=30)  # after incomplete listings
CLOSE_LOOKBACK = timedelta(days=30)  # how far back start-up looks for days left unclosed
FUNDING_EVERY = timedelta(minutes=10)
SETTLED_EVERY = timedelta(hours=1)
INDEX_DELAY = timedelta(minutes=2)  # Classic history of a minute, asked this long after it
INDEX_MAX_MINUTES = 6  # one Classic page holds 400 values: six whole minutes
INDEX_REQUESTS_PER_MINUTE = 30
INDEX_ATTEMPTS = 3
# The watchdog: if the event loop does not run for this long (the machine was suspended,
# the CPU was starved, a call blocked), that time is an outage, never a healthy connection.
STALL_LIMIT = timedelta(seconds=5)
# How often the streams and the watchdog are checked while a step runs (review GR-03).
SUPERVISION_SECONDS = 1.0
# How long a stop waits for a database call that the step it cancelled had started.
DB_SETTLE_SECONDS = 30.0


@dataclass
class _IndexTask:
    rebuild: IndexRebuild
    due: datetime
    attempts: int = 0
    answered: bool = False  # Classic answered at least once, without a complete minute


class Recorder:
    def __init__(self, sink: Sink, *, rest: KucoinRestClient | None = None) -> None:
        self.sink = sink
        self._rest = rest
        self.core: RecorderCore | None = None
        self.restart_streams = False
        self._index: dict[tuple[str, datetime], _IndexTask] = {}
        self._index_symbols: dict[str, str] = {}  # contract -> its Classic index
        self._index_budget: tuple[datetime, int] | None = None
        self._next_funding: datetime | None = None
        self._next_settled: datetime | None = None
        self._next_evaluation: datetime | None = None
        self._evaluation_for: datetime | None = None  # the midnight it is for
        self._next_midnight: datetime | None = None
        self._tomorrow: tuple[datetime, list[InstrumentSpec]] | None = None
        self._days_to_close: set[datetime] = set()
        self._closing: dict[datetime, set[tuple[str, str]]] = {}  # members still to close
        self._last_beat: datetime | None = None  # set by run(); None in step-driven tests
        # When the stop began, if it had to cancel a running step (``_supervised_step``).
        self._stop_began: datetime | None = None
        self._db_call: asyncio.Future[Any] | None = None  # the last database call (``_db``)

    # --- start ------------------------------------------------------------------------------

    async def startup(self, now: datetime) -> None:
        now = as_utc(now, "now")
        if self._rest is None:
            self._rest = KucoinRestClient()
        day = floor_to(now, DAY)
        entries = await self._db(self.sink.load_universe, day)
        listings = await self._listings()
        if not all(listings[m][0] and listings[m][1] for m in MARKETS):
            # Without the listings nothing can be recorded correctly: fail, and let systemd
            # start the service again later (02 §0 rule 1).
            raise RuntimeError("KuCoin listings unavailable at start-up")
        if not entries:
            entries = self._evaluate(listings, evaluated_at=now)
            await self._db(self.sink.write_universe, entries)
        specs = await self._reference(entries, listings, now)
        resume = await self._db(self.sink.resume_points, specs)
        gap_starts = await self._db(self.sink.stop_gap_starts, resume)
        core = RecorderCore(specs, started_at=now, resume=resume, gap_starts=gap_starts)
        self.core = core
        # The two days before, and any earlier day the database shows recorded but not closed
        # (for example kept open by a candle that could no longer be recovered).
        unclosed = await self._db(self.sink.unclosed_days, day - CLOSE_LOOKBACK, day)
        self._days_to_close = {day - DAY, day - 2 * DAY, *unclosed}
        # What the previous run left pending and can still be recovered.
        oldest = floor_to(now - RECOVERY_HORIZON, MINUTE)
        candles = core.restore_awaiting(await self._db(self.sink.pending_candles, oldest))
        rebuilds = 0
        for minute in await self._db(self.sink.pending_index_rebuilds, oldest):
            index_symbol = self._index_symbols.get(minute.symbol)
            if index_symbol is None:
                log.warning("pending_index_without_contract", symbol=minute.symbol)
                continue
            self._add_index_task(
                IndexRebuild(minute.symbol, index_symbol, minute.minute, minute.flow_state)
            )
            rebuilds += 1
        self._next_funding = now
        self._next_settled = now
        self._next_midnight = day + DAY
        evaluation = day + EVALUATION_TIME
        self._next_evaluation = evaluation if now < evaluation else now
        self._evaluation_for = day + DAY
        log.info(
            "recorder_started",
            instruments=len(specs),
            spot=sum(s.market == "SPOT" for s in specs),
            futures=sum(s.market == "FUTURES" for s in specs),
            pending_candles=candles,
            pending_index_rebuilds=rebuilds,
        )

    # --- one step of the clock -------------------------------------------------------------

    async def step(self, now: datetime) -> None:
        now = as_utc(now, "now")
        core = self._require_core()
        self._check_stall(now)
        # Midnight before anything is closed: after a stall across it, the instruments that
        # leave stop exactly at midnight and the ones that enter start there (audit E2-04).
        while self._next_midnight is not None and now >= self._next_midnight:
            self._switch_day(self._next_midnight)
        output = core.tick(now)
        if output.is_empty():
            core.written()
        else:
            # Late evidence is used only once the database holds it (review of E2, sixth
            # round): a decision never rests on evidence a restart would not find. The write
            # confirms the tick to the core as soon as it succeeds (review GR-01), also if
            # this step is cancelled while it runs: what it wrote is no gap (third review).
            kept = await self._db(self.sink.write_tick, output, on_success=core.written)
            core.late_kept(kept)
        for rebuild in output.index_rebuilds:
            self._add_index_task(rebuild)
        for request in core.candle_requests(now):
            rows = await self._rest_candles(request)
            decisions = core.apply_rest_candles(request, rows, now)
            with_candle = [d for d in decisions if d.has_candle]
            if with_candle:
                await self._db(self.sink.write_last_candles, with_candle)
        await self._rebuild_index(now)
        await self._funding(now)
        if self._next_evaluation is not None and now >= self._next_evaluation:
            await self._evaluate_universe(now)
        await self._close_days(now)

    def _switch_day(self, midnight: datetime) -> None:
        core = self._require_core()
        tomorrow = self._tomorrow
        if tomorrow is not None and tomorrow[0] <= midnight:
            if tomorrow[0] == midnight:
                changed = core.apply_universe(tomorrow[1], midnight)
                self.restart_streams = self.restart_streams or changed
                log.info("universe_switched", at=midnight.isoformat(), changed=changed)
            self._tomorrow = None
        self._days_to_close.add(midnight - DAY)
        self._next_midnight = midnight + DAY

    async def _close_days(self, now: datetime) -> None:
        """Close the day of each instrument once the core has closed all its minutes of the
        day and none of them waits for a REST candle.

        What the close writes is terminal and never changes. It fills with S3 the minutes
        that nobody recorded, so it waits until the core has closed every minute it
        records, including one still waiting for its proof of life (review of E2, fourth
        round). And a minute whose candle can still be recovered keeps its instrument's day
        open: answers without the candle end within minutes, and a minute KuCoin never
        answered for is given up after ``RECOVERY_HORIZON`` (second round). The other
        instruments close on time."""
        core = self._require_core()
        for day in sorted(self._days_to_close):
            end = day + DAY
            if now < end + STABILIZATION:
                continue
            if day not in self._closing:
                self._closing[day] = set(await self._db(self.sink.universe_members, day))
            remaining = self._closing[day]
            for market, symbol in sorted(remaining):
                if not core.closed_through(market, symbol, end):
                    continue
                if any(day <= m < end for m in core.awaiting_minutes(market, symbol)):
                    continue
                if not await self._db(self.sink.day_is_closed, market, symbol, day):
                    minutes = await self._db(self.sink.close_day, market, symbol, day)
                    log.info(
                        "day_closed",
                        market=market,
                        symbol=symbol,
                        day=day.date().isoformat(),
                        minutes=minutes,
                    )
                remaining.discard((market, symbol))
            if not remaining:
                self._days_to_close.discard(day)
                del self._closing[day]

    # --- REST ------------------------------------------------------------------------------

    async def _get(self, route: str, **kwargs: Any) -> Any:
        rest = self._rest
        if rest is None:
            raise RuntimeError("the recorder has not started")
        try:
            return (await rest.get(route, **kwargs)).data
        except (KucoinApiError, httpx.HTTPError) as exc:
            # A failed request is not an answer, and a network error never stops the
            # recorder: whoever asked tries again later.
            log.warning("rest_error", route=route, error=f"{type(exc).__name__}: {exc}")
            return None

    async def _rest_candles(self, request: CandleRequest) -> list[Ohlcv] | None:
        first_s = to_milliseconds(request.first) // 1000
        last_s = to_milliseconds(request.last) // 1000
        data = await self._get(
            "uta.v2.kline",
            params={
                "tradeType": request.market,
                "symbol": request.symbol,
                "interval": "1min",
                "startAt": str(first_s - 60),
                "endAt": str(last_s + 60),
            },
        )
        if not isinstance(data, dict) or data.get("symbol") != request.symbol:
            return None
        rows = data.get("list")
        if not isinstance(rows, list):
            return None
        parsed = [rest_kline(row) for row in rows]
        if any(candle is None for candle in parsed):
            return None  # one unreadable row makes the whole answer unusable: ask again
        kept = [c for c in parsed if c is not None and request.first <= c.open_time <= request.last]
        for minute, candles in candles_by_minute(kept).items():
            if len(candles) > 1:  # none is chosen: the core asks for the minute again
                log.warning(
                    "rest_candle_conflict",
                    market=request.market,
                    symbol=request.symbol,
                    minute=minute.isoformat(),
                    candles=len(candles),
                )
        return kept

    async def _rebuild_index(self, now: datetime) -> None:
        minute = floor_to(now, MINUTE)
        if self._index_budget is None or self._index_budget[0] != minute:
            self._index_budget = (minute, INDEX_REQUESTS_PER_MINUTE)
        due = sorted(
            (task for task in self._index.values() if task.due <= now),
            key=lambda task: (task.rebuild.symbol, task.rebuild.minute),
        )
        while due and self._index_budget[1] > 0:
            first = due.pop(0)
            chunk = [first]
            while (
                due
                and len(chunk) < INDEX_MAX_MINUTES
                and due[0].rebuild.symbol == first.rebuild.symbol
                and due[0].rebuild.minute == chunk[-1].rebuild.minute + MINUTE
            ):
                chunk.append(due.pop(0))
            self._index_budget = (minute, self._index_budget[1] - 1)
            await self._rebuild_chunk(chunk, now)

    async def _rebuild_chunk(self, chunk: list[_IndexTask], now: datetime) -> None:
        rebuild = chunk[0].rebuild
        start = to_milliseconds(rebuild.minute)
        data = await self._get(
            "futures.index",
            params={
                "symbol": rebuild.index_symbol,
                "startAt": str(start),
                "endAt": str(start + len(chunk) * 60_000 - 1),
                "maxCount": str(len(chunk) * 60),
                "reverse": "false",
            },
        )
        points = index_points(data, rebuild.index_symbol) if data is not None else None
        candles = []
        exhausted: list[tuple[IndexMinute, int]] = []
        for task in chunk:
            candle = (
                index_candle_from_points(rebuild.symbol, task.rebuild.minute, points)
                if points is not None
                else None
            )
            key = (task.rebuild.symbol, task.rebuild.minute)
            if candle is not None:
                candles.append(candle)
                del self._index[key]
                continue
            task.attempts += 1
            task.answered = task.answered or points is not None
            task.due = now + INDEX_DELAY
            if task.attempts < INDEX_ATTEMPTS:
                continue
            del self._index[key]
            r = task.rebuild
            if task.answered:
                # Classic has no complete minute: it stays a gap, and the database says so,
                # so no restart asks for it again (audit E2-02).
                exhausted.append((IndexMinute(r.symbol, r.minute, r.flow_state), task.attempts))
            else:
                # Classic never answered: still pending, the next start-up asks again.
                log.warning(
                    "index_rebuild_unanswered", symbol=r.symbol, minute=r.minute.isoformat()
                )
        if candles:
            await self._db(self.sink.write_index_candles, candles)
        if exhausted:
            await self._db(self.sink.write_exhausted_index, exhausted)

    def _add_index_task(self, rebuild: IndexRebuild) -> None:
        key = (rebuild.symbol, rebuild.minute)
        if key not in self._index:
            self._index[key] = _IndexTask(rebuild, rebuild.minute + MINUTE + INDEX_DELAY)

    async def _funding(self, now: datetime) -> None:
        futures = [s for s in self._require_core().specs if s.market == "FUTURES"]
        if self._next_funding is not None and now >= self._next_funding:
            self._next_funding = now + FUNDING_EVERY
            rows = []
            for spec in futures:
                data = await self._get("uta.v2.funding", params={"symbol": spec.symbol})
                rows += predicted_funding(data, now) or []
            if rows:
                await self._db(self.sink.write_funding, rows)
        if self._next_settled is not None and now >= self._next_settled:
            self._next_settled = now + SETTLED_EVERY
            rows = []
            end = to_milliseconds(floor_to(now, MINUTE))
            for spec in futures:
                data = await self._get(
                    "uta.v2.funding_history",
                    params={
                        "symbol": spec.symbol,
                        "startAt": str(end - 86_400_000),
                        "endAt": str(end),
                    },
                )
                rows += settled_funding(data) or []
            if rows:
                await self._db(self.sink.write_funding, rows)

    # --- universe and reference data -------------------------------------------------------

    async def _listings(self) -> dict[str, tuple[list[Any], list[Any]]]:
        """Instruments and tickers of every symbol, per market (weights 4 and 15)."""
        result = {}
        for market in MARKETS:
            instruments = await self._get("uta.v2.instrument", params={"tradeType": market})
            tickers = await self._get("uta.v2.ticker", params={"tradeType": market})
            result[market] = (
                _list_of(instruments),
                _list_of(tickers),
            )
        return result

    @staticmethod
    def _evaluate(
        listings: Mapping[str, tuple[list[Any], list[Any]]], *, evaluated_at: datetime
    ) -> list[UniverseEntry]:
        entries: list[UniverseEntry] = []
        for market in MARKETS:
            instruments, tickers = listings[market]
            entries += evaluate(market, instruments, tickers, evaluated_at=evaluated_at)
        return entries

    async def _reference(
        self,
        entries: list[UniverseEntry],
        listings: Mapping[str, tuple[list[Any], list[Any]]],
        now: datetime,
    ) -> list[InstrumentSpec]:
        """Store the reference data of the members and return their specs. A member whose
        data does not match its contract is left out (fail closed) and logged."""
        members = [e for e in entries if e.in_universe]
        rows: dict[tuple[str, str], InstrumentRow] = {}
        for market in MARKETS:
            for raw in listings[market][0]:
                if not isinstance(raw, dict):
                    continue
                row = instrument_row(market, raw)
                if row is not None:
                    rows[(market, row.symbol)] = row
        contracts = await self._get("futures.contracts")
        index_of = {
            c.get("symbol"): c.get("indexSymbol")
            for c in _list_of(contracts)
            if isinstance(c, dict)
        }
        self._index_symbols.update(
            {s: i for s, i in index_of.items() if isinstance(s, str) and isinstance(i, str)}
        )
        specs: list[InstrumentSpec] = []
        for entry in members:
            row = rows.get((entry.market, entry.symbol))
            index_symbol = index_of.get(entry.symbol) if entry.market == "FUTURES" else None
            if row is None or (entry.market == "FUTURES" and not isinstance(index_symbol, str)):
                log.warning("member_without_reference", market=entry.market, symbol=entry.symbol)
                continue
            specs.append(
                InstrumentSpec(
                    market=entry.market,
                    symbol=entry.symbol,
                    tick_size=row.tick_size,
                    multiplier=row.multiplier,
                    l50=entry.l50_candidate,
                    index_symbol=index_symbol,
                )
            )
        await self._db(
            self.sink.write_instruments,
            [rows[(s.market, s.symbol)] for s in specs],
            now,
        )
        for spec in specs:
            if spec.market != "FUTURES":
                continue
            tiers = risk_tier_rows(
                spec.symbol,
                await self._get("futures.risk_limit", path={"symbol": spec.symbol}),
            )
            if tiers is not None:
                await self._db(self.sink.write_risk_tiers, spec.symbol, tiers, now)
            else:
                log.warning("risk_tiers_unreadable", symbol=spec.symbol)
        return specs

    async def _evaluate_universe(self, now: datetime) -> None:
        """The universe for the midnight that follows the scheduled evaluation (audit E2-04).

        On time (from 23:59 to that midnight) it takes effect at the midnight, and a restart
        in between reuses the evaluation already stored for it. If the recorder was stopped
        or suspended across the midnight, the window was missed: the universe of the day
        already started is evaluated now, stored with the real time of the evaluation and
        applied at once, renewing the subscriptions. A day never runs on a universe that
        was evaluated for another day.

        With incomplete listings nothing is stored, the current universe stays in force
        and the evaluation is tried again ``EVALUATION_RETRY`` later (review of E2, second
        round); once past the midnight, the retry is a late evaluation as above.
        """
        target = self._evaluation_for or floor_to(now, DAY) + DAY
        listings = await self._listings()
        if not all(listings[m][0] and listings[m][1] for m in MARKETS):
            self._next_evaluation = now + EVALUATION_RETRY
            log.warning(
                "universe_not_evaluated",
                reason="incomplete listings",
                retry_at=self._next_evaluation.isoformat(),
            )
            return
        following = max(target, floor_to(now, DAY)) + DAY
        self._evaluation_for = following
        self._next_evaluation = following - DAY + EVALUATION_TIME
        if now < target:
            stored = await self._db(self.sink.load_universe, target)
            entries = [e for e in stored if e.evaluated_at == target]
            if not entries:
                entries = self._evaluate(listings, evaluated_at=target)
                await self._db(self.sink.write_universe, entries)
            self._tomorrow = (target, await self._reference(entries, listings, now))
            return
        log.warning(
            "universe_evaluation_missed",
            for_midnight=target.isoformat(),
            evaluated_at=now.isoformat(),
        )
        entries = self._evaluate(listings, evaluated_at=now)
        await self._db(self.sink.write_universe, entries)
        specs = await self._reference(entries, listings, now)
        changed = self._require_core().apply_universe(specs, now)
        self.restart_streams = self.restart_streams or changed
        log.info("universe_switched", at=now.isoformat(), changed=changed)

    # --- helpers ---------------------------------------------------------------------------

    def _require_core(self) -> RecorderCore:
        if self.core is None:
            raise RuntimeError("the recorder has not started")
        return self.core

    async def _db(
        self, function: Any, *args: Any, on_success: Callable[[], None] | None = None
    ) -> Any:
        """A database call, in a worker thread. It is shielded: a step cancelled while it
        waits here (``_supervised_step``) never abandons the call half-way, and the stop
        waits for it before writing (``_settle_db``), so the connection never serves two
        calls at once (review GR-03).

        ``on_success`` runs in the event loop as soon as the call ends without error,
        whether or not anyone still waits for it, inside the shielded task and before that
        task ends: whoever sees the call done knows it has run. A callback of the task would
        only be scheduled when it ends, and could run after someone saw it done (fourth
        review, residual of GR-01)."""

        async def call_and_confirm() -> Any:
            result = await asyncio.to_thread(function, *args)
            if on_success is not None:
                on_success()
            return result

        call = asyncio.ensure_future(call_and_confirm())
        self._db_call = call
        return await asyncio.shield(call)

    async def _settle_db(self) -> None:
        """Wait for the database call a cancelled step had started, up to
        ``DB_SETTLE_SECONDS``. If it was the write of a tick and it succeeded, it confirmed
        that tick to the core before ending (``_db``, ``on_success``), so once the call is
        done the stop adds no gap over what it wrote; if it failed, the stop covers what it
        did not write (``RecorderCore.stop``). Raises ``TimeoutError`` if it does not end."""
        call = self._db_call
        if call is None or call.done():
            return
        with suppress(Exception):
            await asyncio.wait_for(asyncio.shield(call), timeout=DB_SETTLE_SECONDS)
        if not call.done():
            raise TimeoutError("a database call of the stopped step did not end")

    # --- the running service ---------------------------------------------------------------

    def report_stall(self, start: datetime, end: datetime) -> None:
        """The recorder did not run from ``start`` to ``end``: whatever arrived or did not
        arrive in between cannot be trusted. A connection that was up counts as down for that
        time; one that was already down stays down (``RecorderCore.on_stall``)."""
        core = self._require_core()
        seconds = (end - start).total_seconds()
        log.warning("recorder_stalled", start=start.isoformat(), seconds=round(seconds, 1))
        core.on_stall(start, end, f"Stall: did not run for {seconds:.0f} s")

    def _check_stall(self, now: datetime) -> None:
        """Called by the watchdog every second and by every step before it closes anything,
        so a stall is always recorded before the time it covers can be closed as healthy."""
        if self._last_beat is None:
            return  # driven step by step (tests): no watchdog
        if now - self._last_beat > STALL_LIMIT:
            self.report_stall(self._last_beat, now)
        if now > self._last_beat:
            self._last_beat = now

    async def _watch_stalls(self, stop: asyncio.Event, clock: Clock) -> None:
        while not stop.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=1)
            self._check_stall(clock.now())

    async def run(self, stop: asyncio.Event, clock: Clock | None = None) -> None:
        """Record until ``stop`` is set.

        Every second, also while a step runs, it checks that both streams and the watchdog
        are still running (``_supervised_step``). One that ended by itself stops the
        recorder with ``TaskEnded``, and systemd starts it again (``Restart=on-failure``): a
        recorder never goes on without one of its markets (incident of 17 November 2026:
        the Spot stream ended in silence at 17:45 and nothing noticed until midnight).
        However it stops, it first records what it leaves open (``_record_stop``)."""
        clock = clock or SystemClock()
        self._stop_began = None
        await self.startup(clock.now())
        self._last_beat = clock.now()
        pacer = ConnectionPacer()
        streams = self._open_streams(pacer)
        watchdog = asyncio.create_task(self._watch_stalls(stop, clock))
        try:
            while not stop.is_set():
                await self._supervised_step(clock, stop, streams, watchdog)
                if stop.is_set():
                    break
                if self.restart_streams:
                    await _close_streams(streams)
                    moment = clock.now()
                    for market in MARKETS:
                        self._require_core().on_state(
                            market, "disconnected", moment, "SessionRotation: universe change"
                        )
                    streams = self._open_streams(pacer)
                    self.restart_streams = False
                pause = 1 - clock.now().microsecond / 1_000_000
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=pause)
        finally:
            # A stop that cancelled a running step began before waiting for it (GR2-01).
            stopped_at = self._stop_began or clock.now()
            watchdog.cancel()
            await _close_streams(streams)
            await self._record_stop(stopped_at, clock.now())
            if self._rest is not None:
                await self._rest.aclose()
            log.info("recorder_stopped")

    async def _supervised_step(
        self,
        clock: Clock,
        stop: asyncio.Event,
        streams: list[_Stream],
        watchdog: asyncio.Task[None],
    ) -> None:
        """One ``step``, checking the streams and the watchdog every ``SUPERVISION_SECONDS``
        while it runs and once more when it ends (review GR-03): a REST or database wait
        can make a step last minutes, and a dead stream must not wait for it.

        A stream or the watchdog that ended by itself raises ``TaskEnded``; a stop that was
        asked for returns. Either way the step is cancelled at its current wait, which is
        like the process stopping there, something every step already survives; a database
        call it had started goes on to its end (``_db``). After a stop is asked for nothing
        is checked: the watchdog ends with it, and that is no failure (review GR-05).

        The moment a running step is cancelled is when the stop began (``_stop_began``),
        taken before waiting for the step: a cancelled HTTP request can take seconds to
        close, with the event loop running, and that wait is the stop, never a stall
        (GR2-01)."""
        step = asyncio.create_task(self.step(clock.now()))
        ended: TaskEnded | None = None
        try:
            while not step.done():
                await asyncio.wait({step}, timeout=SUPERVISION_SECONDS)
                if stop.is_set():
                    break
                try:
                    _supervise(streams, watchdog)
                except TaskEnded as exc:
                    ended = exc
                    break
        finally:
            if not step.done() and self._stop_began is None:
                self._stop_began = clock.now()
            failure = await _finish(step)
        if ended is not None:
            if failure is not None:
                log.error("step_failed", error=f"{type(failure).__name__}: {failure}"[:300])
            raise ended
        if failure is not None:
            raise failure

    async def _record_stop(self, stopped_at: datetime, now: datetime) -> None:
        """Write the gaps the recorder leaves unwritten, so that the next start-up and the
        day close find them (incident of 17 November 2026): those closed since the last
        tick, those still open, closed at this moment, and what the instruments that left
        the universe can no longer close (``RecorderCore.stop``). Only gaps: no slot or
        minute is closed here, because after a tick that could not be written that would
        leave time without rows and without a gap. The next start-up begins its own gap
        after these when they cover its resume point (``Sink.stop_gap_starts``).

        A stall is looked for at ``stopped_at``, when the stop began: closing the streams
        and the database after it is the stop itself, not a stall. Looked for at ``now``,
        a stop that took more than ``STALL_LIMIT`` was labelled "Stall" (18 November 2026).

        It is the last thing the recorder writes and only a best effort: if it fails, the
        failure is logged and never hides why the recorder stopped. A stop that does not get
        this far (a killed process, a power cut) leaves what was open as before: the minutes
        written while a connection was down keep their S3 rows, and those without a candle
        stay ``sin_explicar``."""
        core = self.core
        if core is None:
            return
        try:
            await self._settle_db()
            self._check_stall(stopped_at)
            gaps = core.stop(now)
            if gaps:
                await self._db(self.sink.write_tick, TickOutput(gaps=gaps))
        except Exception as exc:  # the reason for stopping must not be hidden
            log.error("stop_not_recorded", error=f"{type(exc).__name__}: {exc}"[:300])
            return
        log.info("stop_recorded", gaps=len(gaps))

    def _open_streams(self, pacer: ConnectionPacer) -> list[_Stream]:
        core = self._require_core()
        streams = []
        for market in MARKETS:
            subscriptions = core.subscriptions(market)
            if not subscriptions:
                continue
            stop = asyncio.Event()
            stream = _open_stream(
                market,
                subscriptions,
                on_message=lambda message, at, m=market: core.on_message(m, message, at),
                on_state=lambda state, at, detail, m=market: core.on_state(m, state, at, detail),
                # Pongs and acknowledgements are proofs of life too (RecorderCore._proven).
                on_control=lambda _message, at, m=market: core.on_alive(m, at),
                pacer=pacer,
            )
            streams.append(_Stream(market, stop, asyncio.create_task(stream.run(stop))))
        return streams


@dataclass(frozen=True)
class _Stream:
    market: str
    stop: asyncio.Event
    task: asyncio.Task[None]


class TaskEnded(RuntimeError):
    """A stream or the watchdog ended by itself: the recorder cannot go on without it."""


def _open_stream(market: str, subscriptions: Any, **callbacks: Any) -> PublicStream:
    """The only place where the recorder opens a connection; tests replace it."""
    return PublicStream(market, subscriptions, **callbacks)


async def _finish(task: asyncio.Task[None]) -> BaseException | None:
    """Cancel ``task`` if it still runs and wait for it. Returns its own exception, if it
    ended with one, already taken, so that asyncio never reports it as lost."""
    if not task.done():
        task.cancel()
        await asyncio.wait({task})
    return None if task.cancelled() else task.exception()


def _supervise(streams: list[_Stream], watchdog: asyncio.Task[None]) -> None:
    """Raise ``TaskEnded`` if a stream or the watchdog is no longer running.

    A stream recovers from every connection failure by itself (``PublicStream.run``), so one
    that ended met something else, such as an error in the recorder's own code; the
    watchdog only ends when the recorder stops."""
    tasks = [(f"stream {s.market}", s.task) for s in streams] + [("watchdog", watchdog)]
    for name, task in tasks:
        if not task.done():
            continue
        error = None if task.cancelled() else task.exception()
        if task.cancelled():
            reason = "cancelled"
        elif error is None:
            reason = "returned"
        else:
            reason = f"{type(error).__name__}: {error}"
        log.error("task_ended", task=name, reason=reason[:300])
        raise TaskEnded(f"{name} ended by itself: {reason}") from error


async def _close_streams(streams: list[_Stream]) -> None:
    """Stop every stream and wait for it. It never raises for a stream: one that already
    failed is logged and the others are still closed (incident of 17 November 2026, when a
    stream that had failed six hours before made the change of universe crash the
    recorder). Only the cancellation of the recorder itself goes through."""
    for stream in streams:
        stream.stop.set()
    for stream in streams:
        try:
            await asyncio.wait_for(stream.task, timeout=10)
        except TimeoutError:
            log.warning("stream_not_closed_in_time", market=stream.market)  # it was cancelled
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        except Exception as exc:  # the stream failed: logged, the others are still closed
            log.error(
                "stream_failed", market=stream.market, error=f"{type(exc).__name__}: {exc}"[:300]
            )


def _list_of(data: Any) -> list[Any]:
    if isinstance(data, dict) and isinstance(data.get("list"), list):
        return data["list"]
    if isinstance(data, list):
        return data
    return []
