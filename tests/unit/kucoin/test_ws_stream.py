"""PublicStream against a local WebSocket server that speaks KuCoin's public protocol.

The server reproduces the behaviour observed on the real endpoints on 6 November 2026:
welcome with ``pingInterval``, ``{"id", "result": true}`` acknowledgements and
``{"type": "pong"}`` answers to ``{"id", "type": "ping"}``. It can also refuse handshakes
with HTTP 400, as the Spot endpoint did on 17 November 2026, and answer pings late.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs
from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

from kbot.kucoin import ws as ws_module
from kbot.kucoin.ws import (
    PUBLIC_ENDPOINTS,
    ConnectionPacer,
    PublicStream,
    Subscription,
    check_outgoing,
    reconnect_delay,
)

OBU = {
    "T": "obu.SPOT",
    "P": 1762430000000000000,
    "t": "snapshot",
    "dp": "5",
    "d": {
        "C": 7,
        "O": 7,
        "M": 1762430000000,
        "s": "BTC-USDT",
        "a": [["101.5", "0.2"]],
        "b": [["101.4", "1.5"]],
    },
}


@dataclass
class Server:
    """Scripted behaviour for each accepted connection."""

    ping_interval_ms: int = 1000
    answer_pings: bool = True
    ack_result: Any = True
    welcome: Any = None
    reject_first: int = 0  # handshakes refused with HTTP 400 before the first one accepted
    pong_delay: float = 0.0
    handshakes: int = 0
    received: list[list[dict[str, Any]]] = field(default_factory=list)

    def process_request(self, connection: ServerConnection, _request: Request) -> Response | None:
        self.handshakes += 1
        if self.handshakes <= self.reject_first:
            return connection.respond(HTTPStatus.BAD_REQUEST, "refused (simulated)\n")
        return None

    async def handler(self, connection: ServerConnection) -> None:
        inbox: list[dict[str, Any]] = []
        self.received.append(inbox)
        welcome = self.welcome or {
            "sessionId": "x",
            "message": "welcome",
            "pingInterval": self.ping_interval_ms,
        }
        await connection.send(json.dumps(welcome))
        async for raw in connection:
            message = json.loads(raw)
            inbox.append(message)
            if message.get("type") == "ping":
                if self.answer_pings:
                    await asyncio.sleep(self.pong_delay)
                    await connection.send(
                        json.dumps({"type": "pong", "id": message["id"], "ts": time.time_ns()})
                    )
            elif message.get("action") == "SUBSCRIBE":
                await connection.send(json.dumps({"id": message["id"], "result": self.ack_result}))
                if self.ack_result is True:
                    await connection.send(json.dumps(OBU))


@dataclass
class Observed:
    messages: list[tuple[dict[str, Any], datetime]] = field(default_factory=list)
    states: list[tuple[str, str]] = field(default_factory=list)
    control: list[dict[str, Any]] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    moments: list[tuple[str, datetime]] = field(default_factory=list)

    def on_message(self, message: dict[str, Any], at: datetime) -> None:
        self.messages.append((message, at))

    def on_state(self, state: str, at: datetime, detail: str) -> None:
        self.states.append((state, detail))
        self.moments.append((state, at))

    def on_control(self, message: dict[str, Any], _at: datetime) -> None:
        self.control.append(message)


SUBSCRIPTIONS = (
    Subscription("obu", "SPOT", "BTC-USDT", (("depth", "5"),)),
    Subscription("trade", "SPOT", "BTC-USDT"),
)


def local_transport(port: int, targets: list[str]) -> Any:
    """Controlled substitution, only in tests (R-E1-02): the module function that opens
    connections is patched to record the URL the stream asks for and to reach the local
    server. PublicStream has no parameter for this, so production code cannot do it."""

    def connect(url: str, **options: Any) -> Any:
        targets.append(url)
        return websocket_connect(f"ws://127.0.0.1:{port}", **options)

    return patch.object(ws_module, "_open_connection", connect)


def run_stream(
    server: Server,
    until: Callable[[Observed], bool],
    *,
    deadline: float = 8.0,
    pong_timeout: float = 0.3,
    max_session: float = 60.0,
) -> Observed:
    observed = Observed()

    async def go() -> None:
        with ExitStack() as stack:
            async with serve(
                server.handler, "127.0.0.1", 0, process_request=server.process_request
            ) as ws_server:
                port = ws_server.sockets[0].getsockname()[1]
                stop = asyncio.Event()
                stack.enter_context(local_transport(port, observed.targets))
                stream = PublicStream(
                    "SPOT",
                    SUBSCRIPTIONS,
                    on_message=observed.on_message,
                    on_state=observed.on_state,
                    on_control=observed.on_control,
                    pacer=ConnectionPacer(min_interval=0.0),
                    pong_timeout=pong_timeout,
                    ack_timeout=1.0,
                    reconnect_delay=0.1,
                    max_reconnect_delay=0.4,
                    max_session_seconds=max_session,
                )
                task = asyncio.create_task(stream.run(stop))
                await wait_for(lambda: until(observed), deadline)
                stop.set()
                await asyncio.wait_for(task, timeout=3.0)

    asyncio.run(go())
    return observed


async def wait_for(condition: Callable[[], bool], deadline: float) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), timeout=deadline)


def test_subscribes_receives_and_keeps_alive() -> None:
    server = Server()

    def pinged_twice(observed: Observed) -> bool:
        pings = [m for inbox in server.received for m in inbox if m.get("type") == "ping"]
        return len(pings) >= 2 and len(observed.messages) >= 2

    observed = run_stream(server, pinged_twice)

    assert len(server.received) == 1, "no reconnection while pongs arrive"
    assert observed.targets == [PUBLIC_ENDPOINTS["SPOT"]], "the stream only asks for KuCoin"
    sent = server.received[0]
    for message in sent:
        check_outgoing(message)
    subscribes = [m for m in sent if m.get("action") == "SUBSCRIBE"]
    assert [m["channel"] for m in subscribes] == ["obu", "trade"]
    assert subscribes[0]["depth"] == "5"
    assert len({m["id"] for m in sent}) == len(sent)

    assert observed.states[0][0] == "connected"
    assert all(state != "disconnected" for state, _ in observed.states)
    message, at = observed.messages[0]
    assert message["d"]["a"] == [["101.5", "0.2"]]
    assert message["P"] == 1762430000000000000
    assert at.tzinfo is not None


def test_a_missing_pong_drops_and_reopens_the_connection() -> None:
    server = Server(answer_pings=False)
    observed = run_stream(server, lambda _o: len(server.received) >= 2)
    disconnects = [detail for state, detail in observed.states if state == "disconnected"]
    assert disconnects and "no pong" in disconnects[0]
    # The detail tells a late pong on a live feed from a silent connection.
    assert re.fullmatch(
        r"ProtocolViolation: no pong in time \(0\.3 s; \d+ messages received since the ping,"
        r" the last \d+\.\d s before\)",
        disconnects[0],
    )
    # Reported down from the last message received, not from when the missing pong was
    # noticed (about a second later): nothing in between counts as healthy.
    down_at = next(at for state, at in observed.moments if state == "disconnected")
    last_message_at = max(at for _m, at in observed.messages if at <= down_at)
    assert down_at == last_message_at


def test_a_refused_subscription_drops_the_connection() -> None:
    server = Server(ack_result=False)
    observed = run_stream(server, lambda o: any(state == "disconnected" for state, _ in o.states))
    assert "subscription refused" in observed.states[0][1]
    assert observed.messages == []


@pytest.mark.parametrize(
    "welcome",
    [
        {"message": "hello", "pingInterval": 1000},
        {"message": "welcome", "pingInterval": 0},
        {"message": "welcome", "pingInterval": "18000"},
        {"message": "welcome"},
        ["welcome"],
        {"data": "hello", "pingInterval": 1000},
        {"data": "welcome", "pingInterval": 1000, "pingTimeout": 500},
        {"data": "welcome", "pingInterval": 1000, "pingTimeout": "10000"},
        {"message": "welcome", "pingInterval": 1000, "pingTimeout": True},
    ],
)
def test_an_unexpected_welcome_drops_the_connection(welcome: Any) -> None:
    server = Server(welcome=welcome)
    observed = run_stream(server, lambda o: any(state == "disconnected" for state, _ in o.states))
    assert observed.states[0][0] == "disconnected"
    assert "ProtocolViolation" in observed.states[0][1]


def test_decimals_in_pushed_numbers_are_exact() -> None:
    received: list[dict[str, Any]] = []

    async def handler(connection: ServerConnection) -> None:
        await connection.send('{"message":"welcome","pingInterval":18000}')
        async for raw in connection:
            message = json.loads(raw)
            await connection.send(json.dumps({"id": message["id"], "result": True}))
            await connection.send('{"T":"mark-price","d":{"mp":0.1000000000000000055511}}')

    async def go() -> None:
        with ExitStack() as stack:
            async with serve(handler, "127.0.0.1", 0) as ws_server:
                port = ws_server.sockets[0].getsockname()[1]
                stop = asyncio.Event()
                stack.enter_context(local_transport(port, []))
                stream = PublicStream(
                    "FUTURES",
                    [Subscription("mark-price", "FUTURES", "XBTUSDTM")],
                    on_message=lambda m, _at: received.append(m),
                    pacer=ConnectionPacer(min_interval=0.0),
                )
                task = asyncio.create_task(stream.run(stop))
                await wait_for(lambda: bool(received), 5.0)
                stop.set()
                await asyncio.wait_for(task, timeout=3.0)

    asyncio.run(go())
    assert received[0]["d"]["mp"] == Decimal("0.1000000000000000055511")


def test_control_messages_are_reported() -> None:
    server = Server()
    observed = run_stream(
        server, lambda o: any(m.get("type") == "pong" for m in o.control), deadline=5.0
    )
    assert observed.control[0]["message"] == "welcome"
    acks = [m for m in observed.control if "result" in m]
    assert [m["id"] for m in acks] == ["s1", "s2"]
    assert all(m["result"] is True for m in acks)


def test_the_connection_is_reopened_before_the_24_hour_cut() -> None:
    server = Server(ping_interval_ms=18000)

    def reconnected(o: Observed) -> bool:
        return sum(state == "connected" for state, _ in o.states) >= 2

    observed = run_stream(server, reconnected, max_session=0.5)
    rotations = [d for state, d in observed.states if state == "disconnected"]
    assert rotations and rotations[0].startswith("SessionRotation")
    assert [state for state, _ in observed.states[:3]] == ["connected", "disconnected", "connected"]


def connected(observed: Observed) -> bool:
    return any(state == "connected" for state, _ in observed.states)


def test_a_refused_handshake_is_a_disconnection_and_the_stream_goes_on() -> None:
    """Incident of 17 November 2026: KuCoin refused a reconnection to Spot with HTTP 400,
    and the exception ended the stream in silence for six hours. Each refusal is now one
    more failed attempt, reported and logged, and the stream connects once it may."""
    server = Server(reject_first=3)
    with capture_logs() as logs:
        observed = run_stream(server, connected)
    assert [state for state, _ in observed.states] == ["disconnected"] * 3 + ["connected"]
    refusals = [detail for state, detail in observed.states if state == "disconnected"]
    assert all(d.startswith("InvalidStatus") and "HTTP 400" in d for d in refusals)
    assert server.handshakes == 4
    dropped = [e for e in logs if e["event"] == "ws_disconnected"]
    assert [e["failed_attempts"] for e in dropped] == [1, 2, 3]
    assert all(e["log_level"] == "warning" and "HTTP 400" in e["detail"] for e in dropped)
    assert [e["trade_type"] for e in logs if e["event"] == "ws_connected"] == ["SPOT"]


def test_attempts_that_keep_failing_wait_longer_at_random_up_to_a_cap() -> None:
    """After a connected session and after the first failed attempt, the base wait; then a
    random wait in the upper half of a range that doubles up to the cap. Never less than
    the base: there are no fast retries."""
    rng = random.Random(7)
    assert reconnect_delay(0, base=5, cap=60, rng=rng) == 5
    assert reconnect_delay(1, base=5, cap=60, rng=rng) == 5
    ranges = {2: (5, 10), 3: (10, 20), 4: (20, 40), 5: (30, 60), 12: (30, 60)}
    for failures, (low, high) in ranges.items():
        delays = [reconnect_delay(failures, base=5, cap=60, rng=rng) for _ in range(200)]
        assert low <= min(delays) and max(delays) <= high, failures
        assert max(delays) - min(delays) > (high - low) / 2, "drawn at random"
    # Review GR-02: with the real values, as floats, a long outage never overflows.
    for failures in (1025, 10000, 10**9):
        delays = [reconnect_delay(failures, base=5.0, cap=60.0, rng=rng) for _ in range(50)]
        assert min(delays) >= 30.0 and max(delays) <= 60.0, failures


@pytest.mark.parametrize(
    "welcome",
    [
        {"sessionId": "x", "data": "welcome", "pingInterval": 1000},
        {"sessionId": "x", "data": "welcome", "pingInterval": 1000, "pingTimeout": 1000},
        {"sessionId": "x", "message": "welcome", "pingInterval": 1000, "pingTimeout": 1000},
    ],
    ids=["data", "data and pingTimeout", "message and pingTimeout"],
)
def test_the_announced_welcome_is_accepted(welcome: dict[str, Any]) -> None:
    """KuCoin announced on 25 August 2026, and postponed without a date, a welcome that
    says "data" instead of "message" and carries pingTimeout."""
    observed = run_stream(Server(welcome=welcome), connected)
    assert observed.states[0][0] == "connected"


def test_the_pong_wait_is_never_shorter_than_what_kucoin_announces() -> None:
    welcome = {"data": "welcome", "pingInterval": 1000, "pingTimeout": 1500}
    server = Server(answer_pings=False, welcome=welcome)
    observed = run_stream(server, lambda o: any(s == "disconnected" for s, _ in o.states))
    detail = next(d for state, d in observed.states if state == "disconnected")
    assert detail.startswith("ProtocolViolation: no pong in time (1.5 s;"), detail


def test_slow_pongs_are_logged_with_where_the_time_went(monkeypatch: pytest.MonkeyPatch) -> None:
    """To choose the wait for a pong with real data: the time until KuCoin sent it (its
    ``ts``) and after. This server answers 0.1 s late, so the time is before it sent it."""
    monkeypatch.setattr(ws_module, "SLOW_PONG_SECONDS", 0.05)
    server = Server(pong_delay=0.1)
    with capture_logs() as logs:
        run_stream(server, lambda o: any(m.get("type") == "pong" for m in o.control))
    slow = [e for e in logs if e["event"] == "ws_slow_pong"]
    assert slow, logs
    event = slow[0]
    assert event["trade_type"] == "SPOT"
    assert event["messages_before"] == 0, "this server pushes nothing between ping and pong"
    # The margins allow for coarse timers (Windows: about 16 ms).
    assert event["waited_ms"] >= 80
    assert event["until_sent_ms"] >= 70
    assert event["until_sent_ms"] > 2 * abs(event["after_sent_ms"]), "the time is before KuCoin"
    assert abs(event["until_sent_ms"] + event["after_sent_ms"] - event["waited_ms"]) <= 2


def test_an_error_in_a_handler_ends_the_stream_with_that_error() -> None:
    """The stream recovers from connection failures only. An error in its owner's code ends
    it with that error, so that the owner sees it (the recorder then stops and systemd
    starts it again, ``kbot.ingest.service``)."""
    server = Server()
    states: list[str] = []

    def broken(_message: dict[str, Any], _at: datetime) -> None:
        raise RuntimeError("a bug in the handler")

    async def go() -> None:
        with ExitStack() as stack:
            async with serve(server.handler, "127.0.0.1", 0) as ws_server:
                port = ws_server.sockets[0].getsockname()[1]
                stack.enter_context(local_transport(port, []))
                stream = PublicStream(
                    "SPOT",
                    SUBSCRIPTIONS,
                    on_message=broken,
                    on_state=lambda state, _at, _detail: states.append(state),
                    pacer=ConnectionPacer(min_interval=0.0),
                )
                with pytest.raises(RuntimeError, match="a bug in the handler"):
                    await asyncio.wait_for(stream.run(asyncio.Event()), timeout=5.0)

    asyncio.run(go())
    assert "disconnected" not in states, "not reported as a connection failure"
