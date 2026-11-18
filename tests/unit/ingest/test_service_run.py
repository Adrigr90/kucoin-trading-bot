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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from structlog.testing import capture_logs

from kbot.ingest import service as service_module
from kbot.ingest.service import Recorder, TaskEnded, _close_streams, _Stream, _supervise
from kbot.ingest.sinks import MemorySink
from kbot.kucoin.ratelimit import RestRateLimiter
from kbot.kucoin.rest import KucoinRestClient
from kbot.market.gaps import GapCause
from tests.unit.ingest.fake_kucoin import FakeKucoin

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


@pytest.fixture
def recorder(test_client: Factory) -> tuple[Recorder, MemorySink]:
    fake = FakeKucoin(spot=["BTC-USDT"], futures=["XBTUSDTM"])
    sink = MemorySink()
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
    assert [e["gaps"] for e in logs if e["event"] == "stop_recorded"] == [len(SPOT_CHANNELS)]


def test_a_stop_with_a_connection_down_records_its_gap_up_to_the_stop(
    recorder: tuple[Recorder, MemorySink], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A systemctl stop (a deploy, a reboot) while Spot is down and retrying: its open gap
    is written up to the stop with its own cause, so the stop loses nothing."""
    rec, sink = recorder
    down: list[datetime] = []

    async def retrying(stream: FakeStream, stop: asyncio.Event) -> None:
        stream.report("connected")
        refused = "InvalidStatus: server rejected WebSocket connection: HTTP 400"
        down.append(stream.report("disconnected", refused))
        await stop.wait()

    scripted(monkeypatch, {"SPOT": retrying, "FUTURES": stays_up})

    async def go() -> datetime:
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(1.5, stop.set)
        await rec.run(stop)
        return datetime.now(UTC)

    finished = asyncio.run(go())
    stop_gaps = [g for g in sink.ticks[-1].gaps if g.market == "SPOT" and g.start_at in down]
    assert {g.channel for g in stop_gaps} == SPOT_CHANNELS
    assert {g.cause for g in stop_gaps} == {GapCause.DESCONEXION}
    assert all(down[0] < g.end_at <= finished for g in stop_gaps)
    futures = [g for t in sink.ticks for g in t.gaps if g.market == "FUTURES"]
    assert {g.detail for g in futures} == {"first connection"}, "Futures was up: no stop gap"


def test_the_start_up_begins_the_gap_of_the_stop_where_the_written_gap_ends(
    recorder: tuple[Recorder, MemorySink],
) -> None:
    """The gap written by the stop (up to 00:00:05) ends after the last minute written
    (00:00:00): the gap of the stop begins at 00:00:05, without overlapping it."""
    rec, sink = recorder
    now = datetime(2026, 11, 18, 0, 0, 15, tzinfo=UTC)
    stopped = now - timedelta(seconds=10)
    keys = [("SPOT", "BTC-USDT", channel) for channel in SPOT_CHANNELS]
    sink.resume = dict.fromkeys(keys, now - timedelta(seconds=15))
    sink.gap_end = dict.fromkeys(keys, stopped)

    async def go() -> None:
        await rec.startup(now)
        core = rec.core
        assert core is not None
        core.on_state("SPOT", "connected", now + timedelta(seconds=1), "wss://...")
        core.on_alive("SPOT", now + timedelta(seconds=5))
        gaps = core.tick(now + timedelta(seconds=5)).gaps
        assert {(g.channel, g.start_at, g.cause) for g in gaps if g.market == "SPOT"} == {
            (channel, stopped, GapCause.PROCESO_DETENIDO) for channel in SPOT_CHANNELS
        }
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
