"""``Recorder.run`` with scripted streams: supervision, closing and the record of a stop
(incident of 17 and 18 November 2026).

On 17 November an HTTP 400 on a reconnection ended the Spot stream at 17:45 and the
recorder went on without it, in silence, until the change of universe at midnight: closing
the streams met the failed one and the recorder crashed. The gap open since 17:45 was lost
with it, and so was the last minute of the instruments that left the universe.

The REST side is the fake KuCoin of the other service tests. The WebSocket streams are
replaced where the recorder opens them (``service._open_stream``), so each test scripts what
the stream of each market does. These tests run on the real clock, for a few seconds.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from structlog.testing import capture_logs

from kbot.ingest import service as service_module
from kbot.ingest.core import TickOutput
from kbot.ingest.service import (
    Recorder,
    TaskEnded,
    _close_streams,
    _finish,
    _Stream,
    _supervise,
)
from kbot.ingest.sinks import MemorySink
from kbot.kucoin.ratelimit import RestRateLimiter
from kbot.kucoin.rest import KucoinRestClient
from kbot.market.gaps import DataGap, GapCause
from kbot.market.last_candles import KeptEvidence
from tests.unit.ingest.fake_kucoin import FakeKucoin
from tests.unit.ingest.messages import beat, pong

Factory = Callable[..., KucoinRestClient]
SPOT_CHANNELS = {"obu5", "obu50", "trade", "kline"}  # BTC-USDT is a candidate of level 50
NO_PONG = (
    "ProtocolViolation: no pong in time (10 s; 412 messages received since the ping,"
    " the last 0.0 s before)"
)


class FakeStream:
    """What the recorder sees of a stream: its ``run``, and the states it reports."""

    def __init__(self, market: str, on_state: Any, script: Script) -> None:
        self.market = market
        self.on_state = on_state
        self.script = script
        self.stopped = False

    def report(self, state: str, detail: str = "wss://...") -> datetime:
        moment = datetime.now(UTC)
        self.on_state(state, moment, detail)
        return moment

    async def run(self, stop: asyncio.Event) -> None:
        try:
            await self.script(self, stop)
        finally:
            self.stopped = stop.is_set()


Script = Callable[[FakeStream, asyncio.Event], Awaitable[None]]


async def stays_up(stream: FakeStream, stop: asyncio.Event) -> None:
    stream.report("connected")
    await stop.wait()


class TestSink(MemorySink):
    """MemorySink whose tick writes can fail once or take long, as a database can, and
    that notes whether two writes ever ran at the same time."""

    __test__ = False

    def __init__(self) -> None:
        super().__init__()
        self.fail_next = False
        self.slow = 0.0
        self.active = 0
        self.overlapped = False
        self._lock = threading.Lock()

    def write_tick(self, output: TickOutput) -> list[KeptEvidence]:
        with self._lock:
            self.active += 1
            self.overlapped = self.overlapped or self.active > 1
        try:
            time.sleep(self.slow)
            if self.fail_next:
                self.fail_next = False
                raise OSError("the connection was lost (simulated)")
            return super().write_tick(output)
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def recorder(test_client: Factory) -> tuple[Recorder, TestSink]:
    fake = FakeKucoin(spot=["BTC-USDT"], futures=["XBTUSDTM"])
    sink = TestSink()
    budget = RestRateLimiter(pool_weight=10**6)  # tests never wait for the real budget
    return Recorder(sink, rest=test_client(fake, budget)), sink


def scripted(monkeypatch: pytest.MonkeyPatch, scripts: dict[str, Script]) -> dict[str, FakeStream]:
    opened: dict[str, FakeStream] = {}

    def open_stream(market: str, _subscriptions: Any, **callbacks: Any) -> FakeStream:
        opened[market] = FakeStream(market, callbacks["on_state"], scripts[market])
        return opened[market]

    monkeypatch.setattr(service_module, "_open_stream", open_stream)
    return opened


def test_a_stream_that_ends_by_itself_stops_the_recorder_after_recording_the_stop(
    recorder: tuple[Recorder, MemorySink], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorder stops within a second, with an error so that systemd starts it again,
    after closing the other stream and writing the gap still open up to the stop."""
    rec, sink = recorder
    down: list[datetime] = []

    async def fails(stream: FakeStream, _stop: asyncio.Event) -> None:
        stream.report("connected")
        down.append(stream.report("disconnected", NO_PONG))
        await asyncio.sleep(0.2)
        raise KeyError("an unexpected failure")

    opened = scripted(monkeypatch, {"SPOT": fails, "FUTURES": stays_up})
    with capture_logs() as logs, pytest.raises(TaskEnded, match="stream SPOT ended by itself"):
        asyncio.run(asyncio.wait_for(rec.run(asyncio.Event()), timeout=20))

    assert opened["FUTURES"].stopped, "the other stream was closed"
    ended = [e for e in logs if e["event"] == "task_ended"]
    assert [(e["task"], e["log_level"]) for e in ended] == [("stream SPOT", "error")]
    assert "KeyError" in ended[0]["reason"]
    stop_gaps = [g for g in sink.ticks[-1].gaps if g.cause is GapCause.SIN_PONG]
    assert {(g.market, g.symbol, g.channel) for g in stop_gaps} == {
        ("SPOT", "BTC-USDT", channel) for channel in SPOT_CHANNELS
    }
    assert {g.start_at for g in stop_gaps} == set(down)
    assert all(g.end_at > g.start_at for g in stop_gaps)
    recorded = [e["gaps"] for e in logs if e["event"] == "stop_recorded"]
    assert recorded == [len(sink.ticks[-1].gaps)], "the stop writes only gaps, in one go"
    assert not (sink.ticks[-1].flows or sink.ticks[-1].slots), "and closes nothing"


class JumpingClock:
    """Real time, plus a jump the test makes: what the watchdog sees after the event loop
    stalled for that long."""

    def __init__(self) -> None:
        self.jump = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(UTC) + self.jump


@pytest.mark.parametrize("stall", [False, True], ids=["plain", "stall before the stop"])
def test_a_stop_with_a_connection_down_records_its_gap_up_to_the_stop(
    recorder: tuple[Recorder, MemorySink], monkeypatch: pytest.MonkeyPatch, stall: bool
) -> None:
    """A systemctl stop (a deploy, a reboot) while Spot is down and retrying: its open gap
    is written up to the stop with its own cause, so the stop loses nothing. Futures was
    up, so the stop gives it no gap of its own.

    With ``stall``, the event loop stalls for 10 s just before the stop (review GR-08):
    Futures gets a ``Stall`` gap, closed by the watchdog, maybe at the very moment the open
    gap of Spot closes. It is told apart by what it is, not by when it ends."""
    rec, sink = recorder
    clock = JumpingClock()
    down: list[datetime] = []

    async def retrying(stream: FakeStream, stop: asyncio.Event) -> None:
        stream.report("connected")
        refused = "InvalidStatus: server rejected WebSocket connection: HTTP 400"
        down.append(stream.report("disconnected", refused))
        await stop.wait()

    scripted(monkeypatch, {"SPOT": retrying, "FUTURES": stays_up})

    async def go() -> datetime:
        stop = asyncio.Event()

        def stop_now() -> None:
            if stall:
                clock.jump = timedelta(seconds=10)
            stop.set()

        asyncio.get_running_loop().call_later(1.5, stop_now)
        await rec.run(stop, clock)
        return clock.now()

    finished = asyncio.run(go())
    stop_gaps = [g for g in sink.ticks[-1].gaps if g.market == "SPOT" and g.start_at in down]
    assert {g.channel for g in stop_gaps} == SPOT_CHANNELS
    assert {g.cause for g in stop_gaps} == {GapCause.DESCONEXION}
    assert all(down[0] < g.end_at <= finished for g in stop_gaps)
    futures = [g for t in sink.ticks for g in t.gaps if g.market == "FUTURES"]
    stalls = [g for g in futures if (g.detail or "").startswith("Stall")]
    assert all(g.detail == "first connection" for g in futures if g not in stalls), (
        "no gap of Futures' own: it was up"
    )
    assert all(g.cause is GapCause.PROCESO_DETENIDO and g.end_at <= finished for g in stalls)
    if stall:
        assert {g.channel for g in stalls} == {"obu5", "obu50", "trade", "kline", "mark-price"}


@pytest.mark.parametrize("fails", [False, True], ids=["written", "write failed"])
@pytest.mark.parametrize("at_the_end", [False, True], ids=["while it runs", "as it ends"])
def test_a_write_that_ends_after_its_step_was_cancelled_still_counts(
    test_client: Factory, fails: bool, at_the_end: bool
) -> None:
    """Residual of GR-01, third and fourth reviews: the stop is asked for while the last
    tick of an instrument that left the universe is being written. The step is cancelled,
    the write goes on and the stop waits for it. If it succeeds, it confirms the tick itself
    and the stop adds no gap over what it wrote; if it fails, the stop covers what it did
    not write, up to the moment the instrument left.

    ``at_the_end``: the stop comes in the very turn of the event loop in which the write
    ends, and finds it done (the order the reviewer reproduced): the confirmation must
    already be there, not merely scheduled."""
    fake = FakeKucoin(spot=["BTC-USDT", "ETH-USDT"], futures=["XBTUSDTM"])
    sink = TestSink()
    rec = Recorder(sink, rest=test_client(fake, RestRateLimiter(pool_weight=10**6)))
    start = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    left = start + timedelta(minutes=1)
    eth_channels: tuple[str, ...] = ()

    async def go() -> list[dict[str, Any]]:
        nonlocal eth_channels
        await rec.startup(start)
        core = rec.core
        assert core is not None
        for market in ("SPOT", "FUTURES"):
            core.on_state(market, "connected", start, "wss://...")
        await beat(rec, start + timedelta(seconds=30))
        eth = next(spec for spec in core.specs if spec.symbol == "ETH-USDT")
        eth_channels = eth.channels()
        assert core.apply_universe([spec for spec in core.specs if spec != eth], left)
        sink.slow, sink.fail_next = 0.3, fails
        moment = left + timedelta(seconds=3)
        pong(core, moment)
        step = asyncio.create_task(rec.step(moment))
        while not sink.active:  # its last tick is being written
            await asyncio.sleep(0.01)
        await _finish(step)
        assert step.cancelled()
        if at_the_end:
            call = rec._db_call
            assert call is not None and not call.done()
            while not call.done():  # one turn of the event loop at a time
                await asyncio.sleep(0)
        with capture_logs() as logs:
            await rec._record_stop(moment + timedelta(seconds=1))
        assert rec._rest is not None
        await rec._rest.aclose()
        return logs

    logs = asyncio.run(go())
    eth_flows = [f for t in sink.ticks for f in t.flows if f.symbol == "ETH-USDT"]
    recorded = [e["gaps"] for e in logs if e["event"] == "stop_recorded"]
    if not fails:
        assert eth_flows and max(f.minute for f in eth_flows) == start, "its last minute"
        assert recorded == [0], "nothing over what the write stored"
        return
    assert not eth_flows, "the write of its last tick failed"
    assert {(g.symbol, g.channel, g.start_at, g.end_at) for g in sink.ticks[-1].gaps} == {
        ("ETH-USDT", c, start + timedelta(seconds=20) if c.startswith("obu") else start, left)
        for c in eth_channels
    }


def test_the_start_up_begins_the_gap_of_the_stop_after_the_gaps_the_stop_wrote(
    recorder: tuple[Recorder, MemorySink],
) -> None:
    """Spot was down from 17:45:07 and the stop wrote that gap up to 00:00:05, after the
    last minute written (00:00:00): the gap of the stop begins at 00:00:05, without
    overlapping it. Kline had reconnected at 23:59:58 and gone down again at 00:00:02, so
    its last gap leaves 00:00:00 to 00:00:02 uncovered: its gap of the stop begins at
    00:00:00, the resume point, and no instant is left unexplained."""
    rec, sink = recorder
    now = datetime(2026, 11, 18, 0, 0, 15, tzinfo=UTC)
    resumed = datetime(2026, 11, 18, tzinfo=UTC)
    down = datetime(2026, 11, 17, 17, 45, 7, tzinfo=UTC)
    stopped = now - timedelta(seconds=10)
    sink.resume = {("SPOT", "BTC-USDT", channel): resumed for channel in SPOT_CHANNELS}

    def gap(channel: str, start: datetime) -> DataGap:
        return DataGap("SPOT", channel, "BTC-USDT", start, stopped, GapCause.SIN_PONG, NO_PONG)

    written = [gap(c, down) for c in SPOT_CHANNELS - {"kline"}]
    written.append(gap("kline", resumed + timedelta(seconds=2)))
    sink.ticks.append(TickOutput(gaps=written))

    async def go() -> None:
        await rec.startup(now)
        core = rec.core
        assert core is not None
        core.on_state("SPOT", "connected", now + timedelta(seconds=1), "wss://...")
        core.on_alive("SPOT", now + timedelta(seconds=5))
        gaps = core.tick(now + timedelta(seconds=5)).gaps
        assert {(g.channel, g.start_at, g.cause) for g in gaps if g.market == "SPOT"} == {
            (c, stopped, GapCause.PROCESO_DETENIDO) for c in SPOT_CHANNELS - {"kline"}
        } | {("kline", resumed, GapCause.PROCESO_DETENIDO)}
        assert rec._rest is not None
        await rec._rest.aclose()

    asyncio.run(go())


def test_closing_the_streams_never_fails_because_of_one_that_failed() -> None:
    """At midnight the change of universe closed the streams, met the one that had failed
    at 17:45 and crashed the recorder. A failed stream is now logged, and the others are
    still closed."""

    async def go() -> None:
        async def failed() -> None:
            raise RuntimeError("failed hours ago")

        bad = asyncio.create_task(failed())
        cancelled = asyncio.create_task(asyncio.sleep(10))
        await asyncio.sleep(0)
        cancelled.cancel()
        stop = asyncio.Event()
        good = asyncio.create_task(stop.wait())
        streams = [
            _Stream("SPOT", asyncio.Event(), bad),
            _Stream("FUTURES", stop, good),
            _Stream("OTHER", asyncio.Event(), cancelled),
        ]
        with capture_logs() as logs:
            await _close_streams(streams)
        assert good.done() and not good.cancelled(), "the healthy stream was stopped and awaited"
        assert [e["market"] for e in logs if e["event"] == "stream_failed"] == ["SPOT"]

    asyncio.run(go())


def test_a_watchdog_that_ended_stops_the_recorder_too() -> None:
    async def go() -> None:
        async def returns() -> None:
            return None

        watchdog = asyncio.create_task(returns())
        alive = asyncio.create_task(asyncio.sleep(10))
        await asyncio.sleep(0)
        streams = [_Stream("SPOT", asyncio.Event(), alive)]
        with pytest.raises(TaskEnded, match="watchdog ended by itself: returned"):
            _supervise(streams, watchdog)
        alive.cancel()
        _supervise([], asyncio.create_task(asyncio.sleep(10)))  # all running: nothing

    asyncio.run(go())


def test_a_stop_writes_the_gaps_of_a_tick_that_could_not_be_written(
    recorder: tuple[Recorder, TestSink],
) -> None:
    """Review GR-01: a disconnection from 10 to 100 s gives one gap per channel. The tick
    that hands them out cannot be written (a transient failure), so the recorder stops; the
    stop writes them whole, with their cause, and closes nothing."""
    rec, sink = recorder
    start = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)

    def at(seconds: float) -> datetime:
        return start + timedelta(seconds=seconds)

    async def go() -> None:
        await rec.startup(at(0))
        core = rec.core
        assert core is not None
        for market in ("SPOT", "FUTURES"):
            core.on_state(market, "connected", at(1), "wss://...")
        await beat(rec, at(5))
        core.on_state("SPOT", "disconnected", at(10), NO_PONG)
        core.on_state("SPOT", "connected", at(100), "wss://...")
        sink.fail_next = True
        with pytest.raises(OSError, match="simulated"):
            await beat(rec, at(101))
        await rec._record_stop(at(102))
        assert rec._rest is not None
        await rec._rest.aclose()

    asyncio.run(go())
    stop_write = sink.ticks[-1]
    assert {(g.market, g.channel, g.start_at, g.end_at, g.cause) for g in stop_write.gaps} == {
        ("SPOT", channel, at(10), at(100), GapCause.SIN_PONG) for channel in SPOT_CHANNELS
    }
    assert not (stop_write.flows or stop_write.slots), "only gaps"


def slow_steps(rec: Recorder, monkeypatch: pytest.MonkeyPatch, cancelled: list[datetime]) -> None:
    """Every step does its work and then waits a minute, as a REST call that hangs."""
    real_step = rec.step

    async def step(now: datetime) -> None:
        await real_step(now)
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(now)
            raise

    monkeypatch.setattr(rec, "step", step)


def test_a_stream_that_ends_during_a_long_step_is_caught_at_once(
    recorder: tuple[Recorder, TestSink], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review GR-03: the streams are checked every second also while a step waits, so a
    stream that ends then stops the recorder without waiting for the step, which is
    cancelled at its wait."""
    rec, _sink = recorder
    cancelled: list[datetime] = []
    slow_steps(rec, monkeypatch, cancelled)

    async def fails(stream: FakeStream, _stop: asyncio.Event) -> None:
        stream.report("connected")
        await asyncio.sleep(0.3)
        raise KeyError("an unexpected failure")

    scripted(monkeypatch, {"SPOT": fails, "FUTURES": stays_up})
    started = time.monotonic()
    with capture_logs() as logs, pytest.raises(TaskEnded, match="stream SPOT ended by itself"):
        asyncio.run(asyncio.wait_for(rec.run(asyncio.Event()), timeout=30))
    assert time.monotonic() - started < 10, "not after the step's minute"
    assert len(cancelled) == 1, "the step was cancelled at its wait"
    assert [e["task"] for e in logs if e["event"] == "task_ended"] == ["stream SPOT"]


def test_the_stop_waits_for_the_database_call_of_a_cancelled_step(
    recorder: tuple[Recorder, TestSink], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review GR-03: the step is cancelled while its write runs (1.5 s). The write is not
    abandoned, and the stop writes only after it ends: never two calls at once on the
    connection. The gap still open is written."""
    rec, sink = recorder
    sink.slow = 1.5
    down: list[datetime] = []

    async def fails(stream: FakeStream, _stop: asyncio.Event) -> None:
        stream.report("connected")
        down.append(stream.report("disconnected", NO_PONG))
        await asyncio.sleep(0.3)
        raise KeyError("an unexpected failure")

    scripted(monkeypatch, {"SPOT": fails, "FUTURES": stays_up})
    with pytest.raises(TaskEnded):
        asyncio.run(asyncio.wait_for(rec.run(asyncio.Event()), timeout=30))
    assert not sink.overlapped, "the stop waited for the write of the cancelled step"
    stop_gaps = [g for g in sink.ticks[-1].gaps if g.cause is GapCause.SIN_PONG]
    assert {g.channel for g in stop_gaps} == SPOT_CHANNELS
    assert {g.start_at for g in stop_gaps} == set(down)


def test_a_stop_asked_for_during_a_step_ends_normally(
    recorder: tuple[Recorder, TestSink], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review GR-05: a stop asked for while a step waits ends the watchdog with it, which
    is no failure. The recorder stops at once and normally, without ``task_ended``."""
    rec, _sink = recorder
    cancelled: list[datetime] = []
    slow_steps(rec, monkeypatch, cancelled)
    scripted(monkeypatch, {"SPOT": stays_up, "FUTURES": stays_up})

    async def go() -> None:
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(1.5, stop.set)
        await rec.run(stop)

    started = time.monotonic()
    with capture_logs() as logs:
        asyncio.run(asyncio.wait_for(go(), timeout=30))
    assert time.monotonic() - started < 10
    assert len(cancelled) == 1
    assert not [e for e in logs if e["event"] == "task_ended"]
    assert [e["event"] for e in logs if e["event"] == "recorder_stopped"] == ["recorder_stopped"]
