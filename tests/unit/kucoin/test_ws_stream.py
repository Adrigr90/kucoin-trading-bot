"""PublicStream against a local WebSocket server that speaks KuCoin's public protocol.

The server reproduces the behaviour observed on the real endpoints on 6 November 2026:
welcome with ``pingInterval``, ``{"id", "result": true}`` acknowledgements and
``{"type": "pong"}`` answers to ``{"id", "type": "ping"}``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.server import ServerConnection, serve

from kbot.kucoin.ws import (
    PUBLIC_ENDPOINTS,
    ConnectionPacer,
    PublicStream,
    Subscription,
    check_outgoing,
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
    received: list[list[dict[str, Any]]] = field(default_factory=list)

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
                    await connection.send(
                        json.dumps({"type": "pong", "id": message["id"], "ts": 1})
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

    def on_message(self, message: dict[str, Any], at: datetime) -> None:
        self.messages.append((message, at))

    def on_state(self, state: str, _at: datetime, detail: str) -> None:
        self.states.append((state, detail))

    def on_control(self, message: dict[str, Any], _at: datetime) -> None:
        self.control.append(message)


SUBSCRIPTIONS = (
    Subscription("obu", "SPOT", "BTC-USDT", (("depth", "5"),)),
    Subscription("trade", "SPOT", "BTC-USDT"),
)


def local_connector(port: int, targets: list[str]) -> Callable[..., Any]:
    """Test transport: records the URL the stream asks for and connects to the local
    server instead. The stream itself never accepts any URL but KuCoin's (A3-01)."""

    def connect(url: str, **options: Any) -> Any:
        targets.append(url)
        return websocket_connect(f"ws://127.0.0.1:{port}", **options)

    return connect


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
        async with serve(server.handler, "127.0.0.1", 0) as ws_server:
            port = ws_server.sockets[0].getsockname()[1]
            stop = asyncio.Event()
            stream = PublicStream(
                "SPOT",
                SUBSCRIPTIONS,
                on_message=observed.on_message,
                on_state=observed.on_state,
                on_control=observed.on_control,
                pacer=ConnectionPacer(min_interval=0.0),
                connector=local_connector(port, observed.targets),
                pong_timeout=pong_timeout,
                ack_timeout=1.0,
                reconnect_delay=0.1,
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
        async with serve(handler, "127.0.0.1", 0) as ws_server:
            port = ws_server.sockets[0].getsockname()[1]
            stop = asyncio.Event()
            stream = PublicStream(
                "FUTURES",
                [Subscription("mark-price", "FUTURES", "XBTUSDTM")],
                on_message=lambda m, _at: received.append(m),
                pacer=ConnectionPacer(min_interval=0.0),
                connector=local_connector(port, []),
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
