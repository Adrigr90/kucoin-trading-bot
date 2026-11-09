"""Acceptance F2-A3 (07 §2 E1): only pings and public market subscriptions leave the process."""

from __future__ import annotations

import asyncio

import pytest
from websockets.asyncio.client import connect as websocket_connect

from kbot.kucoin import ws as ws_module
from kbot.kucoin.ws import (
    CHANNELS,
    MAX_TOPICS_PER_CONNECTION,
    PUBLIC_ENDPOINTS,
    ConnectionPacer,
    ForbiddenMessage,
    MessageBudget,
    ProtocolViolation,
    PublicStream,
    Subscription,
    check_endpoint,
    check_outgoing,
)


def sub(**overrides: str) -> dict[str, str]:
    message = {
        "id": "s1",
        "action": "SUBSCRIBE",
        "channel": "obu",
        "tradeType": "SPOT",
        "symbol": "BTC-USDT",
        "depth": "5",
    }
    message.update(overrides)
    return message


@pytest.mark.parametrize(
    "message",
    [
        sub(),
        sub(action="UNSUBSCRIBE", depth="50", tradeType="FUTURES", symbol="XBTUSDTM"),
        {
            "id": "s2",
            "action": "SUBSCRIBE",
            "channel": "trade",
            "tradeType": "SPOT",
            "symbol": "ETH-USDT",
        },
        {
            "id": "s3",
            "action": "SUBSCRIBE",
            "channel": "kline",
            "tradeType": "FUTURES",
            "symbol": "XBTUSDTM",
            "interval": "1min",
        },
        {
            "id": "s4",
            "action": "SUBSCRIBE",
            "channel": "mark-price",
            "tradeType": "FUTURES",
            "symbol": "XBTUSDTM",
        },
        {
            "id": "s5",
            "action": "SUBSCRIBE",
            "channel": "ticker",
            "tradeType": "SPOT",
            "symbol": "SOL-USDT",
        },
        {"id": "p1", "type": "ping"},
    ],
)
def test_public_messages_are_allowed(message: dict[str, str]) -> None:
    check_outgoing(message)


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ({"id": "a1", "type": "auth", "apiKey": "x"}, "not allowed"),
        ({"id": "a1", "action": "LOGIN", "apiKey": "x"}, "not allowed"),
        ({"id": "a1", "action": "subscribe", "channel": "obu"}, "not allowed"),
        (sub(channel="order"), "channel"),
        (sub(channel="balance"), "channel"),
        (sub(channel="position"), "channel"),
        (sub(channel="execution"), "channel"),
        (sub(channel="/spotMarket/tradeOrders"), "channel"),
        ({**sub(), "privateChannel": "true"}, "fields"),
        ({**sub(), "token": "x"}, "fields"),
        ({**sub(), "response": "true"}, "fields"),
        ({k: v for k, v in sub().items() if k != "depth"}, "fields"),
        (sub(tradeType="MARGIN"), "trade type"),
        (sub(depth="400"), "depth"),
        (sub(symbol="btc-usdt"), "symbol"),
        (sub(symbol="BTC-USDT,ETH-USDT"), "symbol"),
        (sub(symbol="all"), "symbol"),
        (sub(id=""), "id"),
        (sub(id="x" * 41), "id"),
        ({"type": "ping"}, "id"),
        ({"id": "p1", "type": "ping", "action": "SUBSCRIBE"}, "ping"),
        (
            {
                "id": "s1",
                "action": "SUBSCRIBE",
                "channel": "mark-price",
                "tradeType": "SPOT",
                "symbol": "BTC-USDT",
            },
            "trade type",
        ),
        (
            {
                "id": "s1",
                "action": "SUBSCRIBE",
                "channel": "kline",
                "tradeType": "SPOT",
                "symbol": "BTC-USDT",
                "interval": "1hour",
            },
            "interval",
        ),
    ],
)
def test_everything_else_is_refused(message: dict[str, str], reason: str) -> None:
    with pytest.raises(ForbiddenMessage, match=reason):
        check_outgoing(message)


def test_only_market_channels_are_whitelisted() -> None:
    assert set(CHANNELS) == {"obu", "trade", "ticker", "kline", "mark-price"}


@pytest.mark.parametrize(
    ("url", "trade_type"),
    [
        ("wss://x-push-spot.kucoin.com", "SPOT"),
        ("wss://x-push-futures.kucoin.com", "FUTURES"),
    ],
)
def test_public_endpoints_are_allowed(url: str, trade_type: str) -> None:
    check_endpoint(url, trade_type)


@pytest.mark.parametrize(
    ("url", "trade_type"),
    [
        ("wss://x-push-spot.kucoin.com", "FUTURES"),
        ("wss://x-push-spot.kucoin.com/?token=abc", "SPOT"),
        ("wss://ws-api-spot.kucoin.com", "SPOT"),
        ("wss://x-push-spot.kucoin.com.evil.example", "SPOT"),
        ("ws://x-push-spot.kucoin.com", "SPOT"),
        ("ws://10.0.0.1:8765", "SPOT"),
        ("ws://localhost:8765", "SPOT"),
        ("ws://127.0.0.1:8765", "SPOT"),
        ("ws://127.0.0.1:8765", "FUTURES"),
        ("ws://[::1]:8765", "SPOT"),
    ],
)
def test_other_endpoints_are_refused(url: str, trade_type: str) -> None:
    with pytest.raises(ForbiddenMessage, match="endpoint"):
        check_endpoint(url, trade_type)


def test_stream_construction_checks_everything() -> None:
    pacer = ConnectionPacer()
    good = Subscription("trade", "SPOT", "BTC-USDT")
    with pytest.raises(ForbiddenMessage):
        PublicStream(
            "SPOT",
            [Subscription("order", "SPOT", "BTC-USDT")],
            on_message=lambda *_: None,
            pacer=pacer,
        )
    with pytest.raises(ValueError, match="trade type"):
        PublicStream("FUTURES", [good], on_message=lambda *_: None, pacer=pacer)
    with pytest.raises(ValueError, match="at least one"):
        PublicStream("SPOT", [], on_message=lambda *_: None, pacer=pacer)
    many = [Subscription("trade", "SPOT", f"C{i}-USDT") for i in range(101)]
    assert len(many) > MAX_TOPICS_PER_CONNECTION
    with pytest.raises(ValueError, match="topics"):
        PublicStream("SPOT", many, on_message=lambda *_: None, pacer=pacer)
    stream = PublicStream("SPOT", [good], on_message=lambda *_: None, pacer=pacer)
    assert stream._url == PUBLIC_ENDPOINTS["SPOT"]


def _alternate_connector(url: str, **options: object) -> object:
    return websocket_connect("ws://127.0.0.1:8765", **options)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("url", "ws://127.0.0.1:8765"),
        ("url", "wss://ws-api-spot.kucoin.com"),
        ("connector", _alternate_connector),
        ("transport", _alternate_connector),
    ],
)
def test_a_stream_cannot_be_pointed_elsewhere(option: str, value: object) -> None:
    """A3-01 and R-E1-02: neither the endpoint nor the transport is a parameter, so
    nothing that builds a stream can send it to another destination."""
    with pytest.raises(TypeError, match=option):
        PublicStream(  # type: ignore[call-arg]
            "SPOT",
            [Subscription("trade", "SPOT", "BTC-USDT")],
            on_message=lambda *_: None,
            pacer=ConnectionPacer(),
            **{option: value},
        )


def test_production_opens_connections_with_the_official_client() -> None:
    """R-E1-02: unless a test patches it, the transport is websockets' own client."""
    assert ws_module._open_connection is websocket_connect


class FakeTime:
    def __init__(self) -> None:
        self.now = 50.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds


def test_message_budget_never_exceeds_the_limit_per_window() -> None:
    fake = FakeTime()
    budget = MessageBudget(limit=100, window=10.0, clock=fake.clock, sleep=fake.sleep)
    sent: list[float] = []

    async def go() -> None:
        for _ in range(350):
            await budget.reserve()
            sent.append(fake.now)
            fake.now += 0.01

    asyncio.run(go())
    for start in sent:
        assert sum(1 for t in sent if start <= t < start + 10.0) <= 100
    assert sent[-1] - sent[0] >= 30.0


def test_connection_pacer_spaces_and_caps_connections() -> None:
    fake = FakeTime()
    pacer = ConnectionPacer(max_connections=2, min_interval=5.0, clock=fake.clock, sleep=fake.sleep)
    opened: list[float] = []

    async def go() -> None:
        async with pacer.slot():
            opened.append(fake.now)
            async with pacer.slot():
                opened.append(fake.now)
                assert pacer.open_connections == 2
                with pytest.raises(ProtocolViolation, match="more than 2"):
                    async with pacer.slot():
                        pass
        assert pacer.open_connections == 0
        async with pacer.slot():
            opened.append(fake.now)

    asyncio.run(go())
    assert opened[1] - opened[0] == pytest.approx(5.0)
    assert opened[2] - opened[1] == pytest.approx(5.0)
