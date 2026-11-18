"""Public WebSocket client for KuCoin UTA market data (spec 02 §3.1).

Every outgoing message passes ``check_outgoing``: only pings and SUBSCRIBE or UNSUBSCRIBE
of the public market channels listed in ``CHANNELS`` are sent. Authentication, private
channels, order channels and any other message are refused before they leave the process.

Protocol facts confirmed against the live endpoints on 6 November 2026: the welcome
message carries ``pingInterval`` in ms (18000 that day), ``{"id", "type": "ping"}`` is
answered with ``{"type": "pong", ...}``, and acknowledgements are ``{"id", "result": true}``.
KuCoin announced on 25 August 2026 that the welcome would say ``"data": "welcome"`` instead
of ``"message": "welcome"`` and carry ``pingTimeout``, and postponed the change on 27 August
without a new date: both forms are accepted.

Every failure of a connection, from the handshake on, is a disconnection that the stream
reports and recovers from by itself (incident of 17 November 2026: an HTTP 400 on a
reconnection ended the Spot stream in silence for six hours). Attempts that fail in a row
wait longer each time, never less than ``RECONNECT_DELAY_SECONDS``. Anything else, such as
an error in a handler, ends the stream with that error, so that its owner sees it.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import math
import random
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from kbot.core.clock import Clock, SystemClock
from kbot.kucoin.jsonparse import loads

log = structlog.get_logger("kbot.kucoin.ws")

PUBLIC_ENDPOINTS: Mapping[str, str] = {
    "SPOT": "wss://x-push-spot.kucoin.com",
    "FUTURES": "wss://x-push-futures.kucoin.com",
}
ACTIONS = frozenset({"SUBSCRIBE", "UNSUBSCRIBE"})
MESSAGE_ID = re.compile(r"[A-Za-z0-9_.\-]{1,40}")
SYMBOL = re.compile(r"[A-Z0-9]{1,20}(-[A-Z0-9]{1,20})?")

MAX_TOPICS_PER_CONNECTION = 100
MAX_CONNECTIONS = 5
MIN_SECONDS_BETWEEN_CONNECTIONS = 5.0
# The documented ceiling is 300 client messages every 10 s; the same page recommends 100.
MAX_MESSAGES_PER_WINDOW = 100
MESSAGE_WINDOW_SECONDS = 10.0
# KuCoin gives 3 s only as an example ("such as 3 second"). With 3 s the Spot connection
# was dropped 30 times in under 7 hours on the server (17 November 2026), so the client
# waits longer, and never less than a pingTimeout the welcome may carry.
PONG_TIMEOUT_SECONDS = 10.0
# Slower pongs are logged with where the time went, to measure the wait with real data.
SLOW_PONG_SECONDS = 1.0
ACK_TIMEOUT_SECONDS = 10.0
RECONNECT_DELAY_SECONDS = 5.0
# Upper bound of the wait between attempts that keep failing; there are never fast retries.
MAX_RECONNECT_DELAY_SECONDS = 60.0
# 2**30 times the base wait is far beyond any upper bound: the doublings stop there.
MAX_DOUBLINGS = 30
# KuCoin closes every connection after 24 h; reconnecting earlier keeps the gap planned.
MAX_SESSION_SECONDS = 23.5 * 3600


class ForbiddenMessage(RuntimeError):
    """An outgoing WebSocket message outside the whitelist."""


class ProtocolViolation(RuntimeError):
    """The server answered in a way the client does not accept."""


@dataclass(frozen=True)
class ChannelSpec:
    trade_types: frozenset[str]
    extra: Mapping[str, frozenset[str]] = field(default_factory=dict)


CHANNELS: Mapping[str, ChannelSpec] = {
    "obu": ChannelSpec(frozenset({"SPOT", "FUTURES"}), {"depth": frozenset({"5", "50"})}),
    "trade": ChannelSpec(frozenset({"SPOT", "FUTURES"})),
    "ticker": ChannelSpec(frozenset({"SPOT", "FUTURES"})),
    "kline": ChannelSpec(frozenset({"SPOT", "FUTURES"}), {"interval": frozenset({"1min"})}),
    "mark-price": ChannelSpec(frozenset({"FUTURES"})),
}


@dataclass(frozen=True)
class Subscription:
    channel: str
    trade_type: str
    symbol: str
    options: tuple[tuple[str, str], ...] = ()

    def message(self, action: str, message_id: str) -> dict[str, str]:
        message = {
            "id": message_id,
            "action": action,
            "channel": self.channel,
            "tradeType": self.trade_type,
            "symbol": self.symbol,
            **dict(self.options),
        }
        check_outgoing(message)
        return message


def check_endpoint(url: str, trade_type: str) -> None:
    """Only the public endpoint of the trade type; there are no exceptions."""
    if PUBLIC_ENDPOINTS.get(trade_type) != url:
        raise ForbiddenMessage(f"endpoint not allowed for {trade_type}: {url}")


def check_outgoing(message: Mapping[str, object]) -> None:
    """Raise ForbiddenMessage unless ``message`` is a ping or a public (un)subscription."""
    keys = set(message)
    message_id = message.get("id")
    if not isinstance(message_id, str) or not MESSAGE_ID.fullmatch(message_id):
        raise ForbiddenMessage("every message needs a plain id")
    if message.get("type") == "ping":
        if keys != {"id", "type"}:
            raise ForbiddenMessage("a ping carries only id and type")
        return
    if message.get("action") not in ACTIONS:
        raise ForbiddenMessage(f"message not allowed: {sorted(keys)}")
    channel = message.get("channel")
    spec = CHANNELS.get(channel) if isinstance(channel, str) else None
    if spec is None:
        raise ForbiddenMessage(f"channel not in the whitelist: {channel!r}")
    expected = {"id", "action", "channel", "tradeType", "symbol", *spec.extra}
    if keys != expected:
        raise ForbiddenMessage(f"{channel}: fields must be exactly {sorted(expected)}")
    if message["tradeType"] not in spec.trade_types:
        raise ForbiddenMessage(f"{channel}: trade type not allowed: {message['tradeType']!r}")
    symbol = message["symbol"]
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise ForbiddenMessage(f"{channel}: symbol not allowed: {symbol!r}")
    for name, allowed in spec.extra.items():
        if message[name] not in allowed:
            raise ForbiddenMessage(f"{channel}: {name} not allowed: {message[name]!r}")


class MessageBudget:
    """At most ``limit`` client messages in any window of ``window`` seconds."""

    def __init__(
        self,
        *,
        limit: int = MAX_MESSAGES_PER_WINDOW,
        window: float = MESSAGE_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()

    async def reserve(self) -> None:
        while True:
            now = self._clock()
            while self._sent and now - self._sent[0] >= self._window:
                self._sent.popleft()
            if len(self._sent) < self._limit:
                self._sent.append(now)
                return
            await self._sleep(self._window - (now - self._sent[0]))


class ConnectionPacer:
    """At most ``max_connections`` open at once, opened at least ``min_interval`` apart."""

    def __init__(
        self,
        *,
        max_connections: int = MAX_CONNECTIONS,
        min_interval: float = MIN_SECONDS_BETWEEN_CONNECTIONS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._max = max_connections
        self._interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last_open = -math.inf
        self._open = 0
        self._lock = asyncio.Lock()

    @property
    def open_connections(self) -> int:
        return self._open

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._lock:
            if self._open >= self._max:
                raise ProtocolViolation(f"more than {self._max} WebSocket connections")
            wait = self._last_open + self._interval - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._last_open = self._clock()
            self._open += 1
        try:
            yield
        finally:
            self._open -= 1


MessageHandler = Callable[[dict[str, Any], datetime], None]
StateHandler = Callable[[str, datetime, str], None]
# The only way to open a connection. It is not a parameter of PublicStream; the protocol
# tests replace it with pytest.monkeypatch, which production code never does.
_open_connection = websocket_connect


class SessionRotation(Exception):
    """The connection reached its planned lifetime and is reopened."""


def reconnect_delay(failures: int, *, base: float, cap: float, rng: random.Random) -> float:
    """Seconds to wait before the next connection attempt.

    ``failures`` counts the attempts in a row that never got connected: 0 after a session
    that was connected, 1 after the first attempt that failed. Both wait ``base``. Each
    further failure doubles the upper bound, up to ``cap``, and the wait is drawn at random
    from the upper half of the range, as the REST backoff does
    (``RestRateLimiter.overload``). The doublings stop counting at ``MAX_DOUBLINGS``, far
    beyond any cap, so a long outage never overflows the power (review GR-02)."""
    if failures <= 1:
        return base
    doublings = min(failures - 1, MAX_DOUBLINGS)
    upper = max(base, min(cap, base * 2.0**doublings))
    return rng.uniform(max(base, upper / 2), upper)


class PublicStream:
    """One public connection: subscriptions, keep-alive and reconnection.

    ``on_state`` receives ``connected`` and ``disconnected`` with the time, so that the
    data layer can record every gap (02 §3.5 and §3.7). ``on_control`` receives the
    server's welcome, acknowledgements and pongs. KuCoin closes connections after 24 h, so
    the stream reopens its connection itself before then. Every disconnection and every
    reconnection goes to the log as well.
    """

    def __init__(
        self,
        trade_type: str,
        subscriptions: Iterable[Subscription],
        *,
        on_message: MessageHandler,
        on_state: StateHandler | None = None,
        on_control: MessageHandler | None = None,
        pacer: ConnectionPacer,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        wall_clock: Clock | None = None,
        pong_timeout: float = PONG_TIMEOUT_SECONDS,
        ack_timeout: float = ACK_TIMEOUT_SECONDS,
        reconnect_delay: float = RECONNECT_DELAY_SECONDS,
        max_reconnect_delay: float = MAX_RECONNECT_DELAY_SECONDS,
        max_session_seconds: float = MAX_SESSION_SECONDS,
        rng: random.Random | None = None,
    ) -> None:
        self._subscriptions = tuple(subscriptions)
        if not self._subscriptions:
            raise ValueError("a stream needs at least one subscription")
        if len(self._subscriptions) > MAX_TOPICS_PER_CONNECTION:
            raise ValueError(f"more than {MAX_TOPICS_PER_CONNECTION} topics on one connection")
        for subscription in self._subscriptions:
            if subscription.trade_type != trade_type:
                raise ValueError("every subscription must use the stream's trade type")
            subscription.message("SUBSCRIBE", "check")
        if trade_type not in PUBLIC_ENDPOINTS:
            raise ValueError(f"unknown trade type: {trade_type!r}")
        # The URL is fixed and the transport is not a parameter: nothing that builds a
        # stream can point it elsewhere.
        self._url = PUBLIC_ENDPOINTS[trade_type]
        check_endpoint(self._url, trade_type)
        self._trade_type = trade_type
        self._on_message = on_message
        self._on_state = on_state or (lambda _state, _at, _detail: None)
        self._on_control = on_control or (lambda _message, _at: None)
        self._pacer = pacer
        self._clock = clock
        self._sleep = sleep
        self._wall = wall_clock or SystemClock()
        self._pong_timeout = pong_timeout
        self._ack_timeout = ack_timeout
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._max_session = max_session_seconds
        self._rng = rng or random.SystemRandom()
        self._ids = itertools.count(1)
        # Wall time of the last message received in the current session: the last proof
        # that the connection was alive.
        self._last_alive: datetime | None = None
        # Whether the current attempt got every subscription confirmed.
        self._connected = False

    async def run(self, stop: asyncio.Event) -> None:
        failures = 0  # attempts in a row that never got connected
        while not stop.is_set():
            self._connected = False
            try:
                await self._session(stop)
            except (
                ConnectionClosed,
                # The server refused the handshake: HTTP 400 on a reconnection to Spot ended
                # the stream on 17 November 2026; it is one more failed attempt.
                InvalidHandshake,
                OSError,
                TimeoutError,
                ProtocolViolation,
                SessionRotation,
            ) as exc:
                # The connection is reported down from its last proof of life, not from the
                # moment the failure was noticed: nothing in between may count as healthy.
                down_since = self._last_alive or self._wall.now()
                self._last_alive = None
                detail = f"{type(exc).__name__}: {exc}"
                self._on_state("disconnected", down_since, detail)
                failures = 0 if self._connected else failures + 1
                delay = reconnect_delay(
                    failures,
                    base=self._reconnect_delay,
                    cap=self._max_reconnect_delay,
                    rng=self._rng,
                )
                log.warning(
                    "ws_disconnected",
                    trade_type=self._trade_type,
                    detail=detail[:200],
                    down_since=down_since.isoformat(),
                    failed_attempts=failures,
                    retry_in_s=round(delay, 1),
                )
                if stop.is_set():
                    break
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=delay)

    async def _session(self, stop: asyncio.Event) -> None:
        async with (
            self._pacer.slot(),
            _open_connection(
                self._url,
                open_timeout=15,
                close_timeout=5,
                ping_interval=None,
                max_size=2**22,
            ) as socket,
        ):
            opened_at = self._clock()
            welcome = _decode(await asyncio.wait_for(socket.recv(), self._ack_timeout))
            if not _is_welcome(welcome):
                raise ProtocolViolation(f"unexpected first message: {str(welcome)[:200]}")
            ping_interval = _ping_interval_seconds(welcome)
            # Never shorter than what KuCoin says a pong may take, when it says it.
            pong_timeout = max(self._pong_timeout, _ping_timeout_seconds(welcome) or 0.0)
            self._last_alive = self._wall.now()
            last_received = self._clock()
            self._on_control(welcome, self._last_alive)
            budget = MessageBudget(clock=self._clock, sleep=self._sleep)
            pending_acks: set[str] = set()
            for subscription in self._subscriptions:
                message_id = f"s{next(self._ids)}"
                await self._send(socket, budget, subscription.message("SUBSCRIBE", message_id))
                pending_acks.add(message_id)
            ack_deadline = self._clock() + self._ack_timeout
            next_ping = self._clock() + ping_interval
            pong_deadline: float | None = None
            ping_sent: tuple[float, datetime] | None = None  # monotonic and wall time
            # Messages read since the last ping: a pong arrives after everything KuCoin
            # pushed before it on the same connection.
            since_ping = 0
            announced = False

            while not stop.is_set():
                now = self._clock()
                if now - opened_at >= self._max_session:
                    raise SessionRotation(f"planned reconnection after {self._max_session:.0f} s")
                if pending_acks and now >= ack_deadline:
                    raise ProtocolViolation(f"no acknowledgement for {sorted(pending_acks)}")
                if pong_deadline is not None and now >= pong_deadline:
                    # Whether data kept arriving tells a late pong from a silent connection.
                    raise ProtocolViolation(
                        f"no pong in time ({pong_timeout:g} s; {since_ping} messages received"
                        f" since the ping, the last {now - last_received:.1f} s before)"
                    )
                if pong_deadline is None and now >= next_ping:
                    await self._send(socket, budget, {"id": f"p{next(self._ids)}", "type": "ping"})
                    pong_deadline = now + pong_timeout
                    next_ping = now + ping_interval
                    ping_sent = (now, self._wall.now())
                    since_ping = 0
                deadlines = [next_ping if pong_deadline is None else pong_deadline]
                if pending_acks:
                    deadlines.append(ack_deadline)
                # Capped so that ``stop`` takes effect within a second even on a quiet feed.
                timeout = min(1.0, max(0.0, min(deadlines) - self._clock()))
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
                except TimeoutError:
                    continue
                received_at = self._wall.now()
                last_received = self._clock()
                self._last_alive = received_at
                message = _decode(raw)
                if message.get("type") == "pong":
                    if ping_sent is not None:
                        waited = last_received - ping_sent[0]
                        self._measure_pong(message, waited, ping_sent[1], since_ping)
                    pong_deadline = None
                    ping_sent = None
                    self._on_control(message, received_at)
                elif "result" in message:
                    if message.get("result") is not True:
                        raise ProtocolViolation(f"subscription refused: {message!r}")
                    pending_acks.discard(str(message.get("id")))
                    self._on_control(message, received_at)
                elif "T" in message:
                    self._on_message(message, received_at)
                else:
                    self._on_state("unexpected_message", received_at, str(message)[:200])
                since_ping += 1
                if not announced and not pending_acks:
                    announced = True
                    self._connected = True
                    self._on_state("connected", received_at, self._url)
                    log.info(
                        "ws_connected",
                        trade_type=self._trade_type,
                        ping_interval_s=ping_interval,
                        pong_timeout_s=pong_timeout,
                    )

    def _measure_pong(
        self, pong: Mapping[str, Any], waited: float, sent_at: datetime, messages: int
    ) -> None:
        """A pong slower than ``SLOW_PONG_SECONDS`` goes to the log, with the messages read
        before it since the ping and, when its ``ts`` (when KuCoin sent it, in ns [H]) is
        plausible, where the time went: until KuCoin sent it, and from then until it was
        read here (the network, and the messages queued before it)."""
        if waited < SLOW_PONG_SECONDS:
            return
        fields: dict[str, Any] = {
            "trade_type": self._trade_type,
            "waited_ms": round(waited * 1000),
            "messages_before": messages,
        }
        ts = pong.get("ts")
        if isinstance(ts, int) and not isinstance(ts, bool) and ts >= _PLAUSIBLE_NS:
            sent_by_server = _EPOCH + timedelta(microseconds=ts // 1000)
            read_at = sent_at + timedelta(seconds=waited)
            fields["until_sent_ms"] = round((sent_by_server - sent_at) / timedelta(milliseconds=1))
            fields["after_sent_ms"] = round((read_at - sent_by_server) / timedelta(milliseconds=1))
        log.info("ws_slow_pong", **fields)

    async def _send(self, socket: Any, budget: MessageBudget, message: dict[str, str]) -> None:
        check_outgoing(message)
        await budget.reserve()
        await socket.send(_dumps(message))


def _dumps(message: Mapping[str, str]) -> str:
    return json.dumps(message, separators=(",", ":"))


def _decode(raw: str | bytes) -> dict[str, Any]:
    try:
        message = loads(raw)
    except ValueError as exc:
        raise ProtocolViolation(f"invalid JSON from the server: {exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolViolation("messages must be JSON objects")
    return message


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_PLAUSIBLE_NS = 10**18  # a time in ns after 2001: anything smaller is not one


def _is_welcome(message: Mapping[str, Any]) -> bool:
    """The welcome as KuCoin sends it today, or as it announced it will (25 August 2026)."""
    return message.get("message") == "welcome" or message.get("data") == "welcome"


def _ping_interval_seconds(welcome: Mapping[str, Any]) -> float:
    value = welcome.get("pingInterval")
    if not isinstance(value, int) or isinstance(value, bool) or not 1000 <= value <= 120_000:
        raise ProtocolViolation(f"unusable pingInterval in the welcome: {value!r}")
    return value / 1000


def _ping_timeout_seconds(welcome: Mapping[str, Any]) -> float | None:
    """The announced ``pingTimeout``: how long a pong may take, in ms. None if absent."""
    if "pingTimeout" not in welcome:
        return None
    value = welcome["pingTimeout"]
    if not isinstance(value, int) or isinstance(value, bool) or not 1000 <= value <= 120_000:
        raise ProtocolViolation(f"unusable pingTimeout in the welcome: {value!r}")
    return value / 1000
