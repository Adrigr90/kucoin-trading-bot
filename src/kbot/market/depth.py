"""Order-book depth from the ``obu`` snapshot channel (spec 02 §3.1, §3.3, §3.5 and §3.8).

One ``DepthChannel`` follows one instrument at one depth (5 or 50 levels). It receives the
snapshots, the connection events and the passing of time, and closes every 10-second slot
with **exactly one** terminal state, the first of this list that applies:

* ``desconectado``: the connection was not healthy at some moment of the slot;
* ``libro_inconsistente``: the snapshot in force at the end of the slot is inconsistent
  (crossed, unordered, non-positive, its sequence ``C`` or its publication time ``P``
  went back, or it was published in a slot already closed);
* ``fuera_de_plazo``: a snapshot published during the slot arrived after the slot's
  deadline (its end plus the lateness margin), so the book in force at the deadline, if
  there was one, was not up to date;
* ``no_recibida``: healthy connection, but no snapshot since the last subscription, neither
  in time nor late;
* ``valida``: otherwise. KuCoin sends nothing while the book does not change, so silence
  on a healthy connection keeps the last snapshot in force, with its age.

A snapshot belongs to the slot in which KuCoin published it (``P``); it counts if it arrives
before the deadline, so ordinary network latency never makes a slot late (live run of 8
November 2026). The recorder closes a slot only once the connection has proven itself alive
after the deadline (``kbot.ingest.core``): one connection delivers in order, so by then every
book published in the slot has arrived, in time or late. Duplicates (same ``C``) are ignored
and never create slots. Metrics are computed only for ``valida`` slots and contain exact
values only: no ratio, so nothing is rounded (§0 rule 5).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from kbot.core.decimal import accounting
from kbot.market.timeutil import as_utc, floor_to, from_nanoseconds, is_int

SLOT_SECONDS = 10
BANDS = (Decimal("0.001"), Decimal("0.005"))  # ±0.1 % and ±0.5 % of the mid price
DEPTHS = frozenset({5, 50})

Level = tuple[Decimal, Decimal]


class SlotState(StrEnum):
    VALIDA = "valida"
    LIBRO_INCONSISTENTE = "libro_inconsistente"
    NO_RECIBIDA = "no_recibida"
    DESCONECTADO = "desconectado"
    FUERA_DE_PLAZO = "fuera_de_plazo"


@dataclass(frozen=True)
class Snapshot:
    sequence: int | None  # C; None only for a malformed message
    published_at: datetime | None  # P, KuCoin's push time; None only for a malformed message
    exchange_ns: int | None  # M, meaning not documented (B1); None when absent, never invented
    received_at: datetime
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    problem: str | None  # why the snapshot is inconsistent, or None

    @property
    def consistent(self) -> bool:
        return self.problem is None


@dataclass(frozen=True)
class DepthMetrics:
    best_bid: Decimal
    best_ask: Decimal
    mid: Decimal
    spread: Decimal
    bid_levels: int
    ask_levels: int
    bid_quantity: Decimal  # visible, in the original unit (contracts in Futures)
    ask_quantity: Decimal
    bid_notional: Decimal  # visible, in the quote currency
    ask_notional: Decimal
    bid_notional_in_band: tuple[Decimal, ...]  # one per BANDS entry
    ask_notional_in_band: tuple[Decimal, ...]
    bid_deepest_price: Decimal
    ask_deepest_price: Decimal


@dataclass(frozen=True)
class SlotResult:
    market: str
    symbol: str
    depth: int
    slot_start: datetime
    state: SlotState
    sequence: int | None  # C of the snapshot in force at the end of the slot
    age_ms: int | None  # age of that snapshot at the end of the slot
    metrics: DepthMetrics | None
    snapshot: Snapshot | None


@dataclass(frozen=True)
class MinuteSnapshot:
    """The last valid book of a minute, kept in ``depth_snapshots`` (§3.1)."""

    market: str
    symbol: str
    depth: int
    minute_start: datetime
    slot_start: datetime  # the valid slot whose book this is
    snapshot: Snapshot


def slot_start(moment: datetime) -> datetime:
    """Start of the 10-second slot that contains ``moment`` (UTC), computed exactly."""
    return floor_to(moment, timedelta(seconds=SLOT_SECONDS))


def parse_snapshot(
    message: dict[str, Any],
    received_at: datetime,
    previous_sequence: int | None,
    previous_published: datetime | None = None,
) -> Snapshot:
    """Read an ``obu`` message. Market problems make it inconsistent, and so does a
    sequence ``C`` or a publication time ``P`` that goes back within the subscription
    (02 §3.3 and §3.6). A message without integer ``C`` and ``P`` raises ValueError
    (DepthChannel records it as malformed)."""
    received_at = as_utc(received_at, "received_at")
    data = message.get("d") or {}
    sequence = data.get("C")
    published = message.get("P")
    exchange = data.get("M")
    if not (is_int(sequence) and is_int(published)):
        raise ValueError("obu message without integer C and P")
    published_at = from_nanoseconds(published)
    bids, bid_problem = _levels(data.get("b"))
    asks, ask_problem = _levels(data.get("a"))
    problem = bid_problem or ask_problem or _book_problem(bids, asks)
    if problem is None and previous_sequence is not None and sequence < previous_sequence:
        problem = f"sequence went back from {previous_sequence} to {sequence}"
    if problem is None and previous_published is not None and published_at < previous_published:
        problem = "publication time went back"
    return Snapshot(
        sequence=sequence,
        published_at=published_at,
        exchange_ns=exchange if is_int(exchange) else None,
        received_at=received_at,
        bids=bids,
        asks=asks,
        problem=problem,
    )


def compute_metrics(snapshot: Snapshot, multiplier: Decimal) -> DepthMetrics:
    """Exact metrics of a consistent book. ``multiplier`` is 1 in Spot."""
    if not snapshot.consistent or not snapshot.bids or not snapshot.asks:
        raise ValueError("metrics need a consistent book with both sides")
    with accounting():
        best_bid = snapshot.bids[0][0]
        best_ask = snapshot.asks[0][0]
        mid = (best_bid + best_ask) / 2
        bid_notional_in_band = tuple(
            sum(
                (p * q * multiplier for p, q in snapshot.bids if p >= mid - mid * band),
                Decimal(0),
            )
            for band in BANDS
        )
        ask_notional_in_band = tuple(
            sum(
                (p * q * multiplier for p, q in snapshot.asks if p <= mid + mid * band),
                Decimal(0),
            )
            for band in BANDS
        )
        return DepthMetrics(
            best_bid=best_bid,
            best_ask=best_ask,
            mid=mid,
            spread=best_ask - best_bid,
            bid_levels=len(snapshot.bids),
            ask_levels=len(snapshot.asks),
            bid_quantity=sum((q for _, q in snapshot.bids), Decimal(0)),
            ask_quantity=sum((q for _, q in snapshot.asks), Decimal(0)),
            bid_notional=sum((p * q * multiplier for p, q in snapshot.bids), Decimal(0)),
            ask_notional=sum((p * q * multiplier for p, q in snapshot.asks), Decimal(0)),
            bid_notional_in_band=bid_notional_in_band,
            ask_notional_in_band=ask_notional_in_band,
            bid_deepest_price=snapshot.bids[-1][0],
            ask_deepest_price=snapshot.asks[-1][0],
        )


class DepthChannel:
    """Slots of one instrument at one depth. Not thread-safe: one asyncio task feeds it."""

    def __init__(
        self,
        market: str,
        symbol: str,
        depth: int,
        *,
        multiplier: Decimal,
        started_at: datetime,
    ) -> None:
        if depth not in DEPTHS:
            raise ValueError(f"depth must be one of {sorted(DEPTHS)}")
        if multiplier <= 0:
            raise ValueError("multiplier must be positive")
        started_at = as_utc(started_at, "started_at")
        self.market = market
        self.symbol = symbol
        self.depth = depth
        self._multiplier = multiplier
        # Nothing is healthy before the first subscription is confirmed.
        self._down: list[tuple[datetime, datetime | None]] = [(started_at, None)]
        self._snapshots: deque[Snapshot] = deque()
        self._max_sequence: int | None = None
        self._max_published: datetime | None = None
        # Start of each confirmed subscription. A slot only uses snapshots received in the
        # subscription in force during the whole slot; older ones may describe a book that
        # changed while the connection was down.
        self._subscribed_at: list[datetime] = []
        self._next_slot = slot_start(started_at)
        self._minute_best: Snapshot | None = None
        self._minute_best_slot: datetime | None = None
        self._minute_had_valid = False

    @property
    def next_slot(self) -> datetime:
        return self._next_slot

    def connection_up(self, at: datetime) -> None:
        """The subscription is confirmed: from now on the connection counts as healthy."""
        at = as_utc(at, "at")
        if self._down and self._down[-1][1] is None:
            self._down[-1] = (self._down[-1][0], at)
        self._subscribed_at.append(at)
        self._max_sequence = None
        self._max_published = None

    def connection_down(self, at: datetime) -> None:
        at = as_utc(at, "at")
        if not self._down or self._down[-1][1] is not None:
            self._down.append((at, None))

    def on_message(self, message: dict[str, Any], received_at: datetime) -> None:
        received_at = as_utc(received_at, "received_at")
        if self._down and self._down[-1][1] is None:
            return  # not subscribed: a stray message cannot make the book valid
        sequence = (message.get("d") or {}).get("C")
        last = self._snapshots[-1] if self._snapshots else None
        if (
            last is not None
            and last.received_at >= self._subscribed_at[-1]
            and sequence == last.sequence
        ):
            return  # duplicate: ignored, creates nothing
        try:
            snapshot = parse_snapshot(message, received_at, self._max_sequence, self._max_published)
        except ValueError:
            # Without C and P the book cannot be placed: it is recorded as an inconsistent
            # book in force, with no invented sequence or time.
            snapshot = Snapshot(None, None, None, received_at, (), (), "malformed message")
        if (
            snapshot.consistent
            and snapshot.published_at is not None
            and snapshot.published_at < self._next_slot
        ):
            # Its slot was closed after the connection proved that everything published
            # before the deadline had arrived: a book that still comes is an anomaly, never a
            # valid book for the slots that follow (review of E2, third round).
            snapshot = replace(snapshot, problem="published in a slot already closed")
        if snapshot.consistent and snapshot.sequence is not None:
            if self._max_sequence is None or snapshot.sequence > self._max_sequence:
                self._max_sequence = snapshot.sequence
            published = snapshot.published_at
            if published is not None and (
                self._max_published is None or published > self._max_published
            ):
                self._max_published = published
        self._snapshots.append(snapshot)

    def close_until(
        self, now: datetime, lateness: timedelta
    ) -> tuple[list[SlotResult], list[MinuteSnapshot]]:
        """Close every slot whose end plus ``lateness`` is not after ``now``."""
        now = as_utc(now, "now")
        slots: list[SlotResult] = []
        minutes: list[MinuteSnapshot] = []
        step = timedelta(seconds=SLOT_SECONDS)
        while self._next_slot + step + lateness <= now:
            start = self._next_slot
            result = self._close(start, start + step, lateness)
            slots.append(result)
            if result.state is SlotState.VALIDA:
                self._minute_had_valid = True
                self._minute_best = result.snapshot
                self._minute_best_slot = start
            end = start + step
            if end.second == 0:
                if (
                    self._minute_had_valid
                    and self._minute_best is not None
                    and self._minute_best_slot is not None
                ):
                    minutes.append(
                        MinuteSnapshot(
                            self.market,
                            self.symbol,
                            self.depth,
                            end - timedelta(minutes=1),
                            self._minute_best_slot,
                            self._minute_best,
                        )
                    )
                self._minute_best = None
                self._minute_best_slot = None
                self._minute_had_valid = False
            self._next_slot = end
            self._forget_before(end)
        return slots, minutes

    def _close(self, start: datetime, end: datetime, lateness: timedelta) -> SlotResult:
        if self._disconnected_during(start, end):
            return self._result(start, SlotState.DESCONECTADO, None, end)
        epoch = max((at for at in self._subscribed_at if at <= start), default=None)
        deadline = end + lateness
        current = [s for s in self._snapshots if epoch is not None and s.received_at >= epoch]
        in_force = None
        for snapshot in current:  # one connection keeps the order in which KuCoin sent them
            if _event_time(snapshot) < end and snapshot.received_at <= deadline:
                in_force = snapshot
        # Checked before no_recibida: a book published in the slot that arrived after the
        # deadline makes the slot late even when no earlier book was in force (audit E2-03).
        late = any(start <= _event_time(s) < end and s.received_at > deadline for s in current)
        if in_force is None:
            state = SlotState.FUERA_DE_PLAZO if late else SlotState.NO_RECIBIDA
            return self._result(start, state, None, end)
        if not in_force.consistent:
            return self._result(start, SlotState.LIBRO_INCONSISTENTE, in_force, end)
        if late:
            return self._result(start, SlotState.FUERA_DE_PLAZO, in_force, end)
        return self._result(start, SlotState.VALIDA, in_force, end)

    def _result(
        self, start: datetime, state: SlotState, snapshot: Snapshot | None, end: datetime
    ) -> SlotResult:
        metrics = None
        if state is SlotState.VALIDA and snapshot is not None:
            metrics = compute_metrics(snapshot, self._multiplier)
        age_ms = None
        if snapshot is not None:
            # Age at the end of the slot, from the moment KuCoin published the book.
            age_ms = max(0, (end - _event_time(snapshot)) // timedelta(milliseconds=1))
        return SlotResult(
            market=self.market,
            symbol=self.symbol,
            depth=self.depth,
            slot_start=start,
            state=state,
            sequence=snapshot.sequence if snapshot is not None else None,
            age_ms=age_ms,
            metrics=metrics,
            snapshot=snapshot if state is SlotState.VALIDA else None,
        )

    def _disconnected_during(self, start: datetime, end: datetime) -> bool:
        for down_start, down_end in self._down:
            if down_start < end and (down_end is None or down_end > start):
                return True
        return False

    def _forget_before(self, moment: datetime) -> None:
        """Drop what can no longer affect a slot that starts at ``moment`` or later."""
        while len(self._snapshots) > 1 and self._snapshots[1].received_at < moment:
            self._snapshots.popleft()
        self._down = [(start, end) for start, end in self._down if end is None or end > moment]
        earlier = [at for at in self._subscribed_at if at <= moment]
        if len(earlier) > 1:
            self._subscribed_at = self._subscribed_at[len(earlier) - 1 :]


def _event_time(snapshot: Snapshot) -> datetime:
    """When KuCoin published the book (``P``); the arrival time for a malformed message."""
    return snapshot.published_at if snapshot.published_at is not None else snapshot.received_at


def _levels(raw: Any) -> tuple[tuple[Level, ...], str | None]:
    if not isinstance(raw, list):
        return (), "a book side is not a list"
    levels: list[Level] = []
    for item in raw:
        if not (
            isinstance(item, list) and len(item) == 2 and all(isinstance(v, str) for v in item)
        ):
            return (), "a book level is not [price, size] as text"
        try:
            price, size = Decimal(item[0]), Decimal(item[1])
        except InvalidOperation:
            return (), "a book level is not numeric"
        if not (price.is_finite() and size.is_finite()):
            return (), "a book level is not finite"
        levels.append((price, size))
    return tuple(levels), None


def _book_problem(bids: tuple[Level, ...], asks: tuple[Level, ...]) -> str | None:
    if any(p <= 0 or q <= 0 for p, q in bids + asks):
        return "non-positive price or size"
    bid_prices = [p for p, _ in bids]
    ask_prices = [p for p, _ in asks]
    if bid_prices != sorted(set(bid_prices), reverse=True):
        return "bids not strictly descending"
    if ask_prices != sorted(set(ask_prices)):
        return "asks not strictly ascending"
    if bids and asks and bids[0][0] >= asks[0][0]:
        return "crossed book"
    if not bids or not asks:
        return "a side of the book is empty"
    return None
