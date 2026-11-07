"""Order-book depth from the ``obu`` snapshot channel (spec 02 §3.1, §3.3, §3.5 and §3.8).

One ``DepthChannel`` follows one instrument at one depth (5 or 50 levels). It receives the
snapshots, the connection events and the passing of time, and closes every 10-second slot
with **exactly one** terminal state:

* ``desconectado``: the connection was not healthy at some moment of the slot;
* ``no_recibida``: healthy connection, but no snapshot since the last subscription;
* ``libro_inconsistente``: the snapshot in force at the end of the slot is inconsistent
  (crossed, unordered, non-positive, or its sequence ``C`` went back);
* ``fuera_de_plazo``: a snapshot published during the slot arrived after the slot ended,
  so the book in force at its end was not up to date;
* ``valida``: otherwise. KuCoin sends nothing while the book does not change, so silence
  on a healthy connection keeps the last snapshot in force, with its age.

Duplicates (same ``C``) are ignored and never create slots. Metrics are computed only for
``valida`` slots and contain exact values only: no ratio, so nothing is rounded (§0 rule 5).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from kbot.core.decimal import accounting

SLOT_SECONDS = 10
BANDS = (Decimal("0.001"), Decimal("0.005"))  # ±0.1 % and ±0.5 % of the mid price
DEPTHS = frozenset({5, 50})
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

Level = tuple[Decimal, Decimal]


class SlotState(StrEnum):
    VALIDA = "valida"
    LIBRO_INCONSISTENTE = "libro_inconsistente"
    NO_RECIBIDA = "no_recibida"
    DESCONECTADO = "desconectado"
    FUERA_DE_PLAZO = "fuera_de_plazo"


@dataclass(frozen=True)
class Snapshot:
    sequence: int
    published_at: datetime  # P, KuCoin's push time
    exchange_ns: int  # M, meaning not documented (B1); kept as received
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
    snapshot: Snapshot


def slot_start(moment: datetime) -> datetime:
    """Start of the 10-second slot that contains ``moment`` (UTC), computed exactly."""
    step = timedelta(seconds=SLOT_SECONDS)
    return EPOCH + ((moment - EPOCH) // step) * step


def from_nanoseconds(value: int) -> datetime:
    """UTC time of a KuCoin nanosecond timestamp, truncated to the microsecond that
    datetime can hold (rule: floor; the original integer is kept where it matters)."""
    return EPOCH + timedelta(microseconds=value // 1000)


def parse_snapshot(
    message: dict[str, Any], received_at: datetime, previous_sequence: int | None
) -> Snapshot:
    """Read an ``obu`` message. Structural or market problems make it inconsistent."""
    data = message.get("d") or {}
    sequence = data.get("C")
    published = message.get("P")
    exchange = data.get("M")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (sequence, published)):
        raise ValueError("obu message without integer C and P")
    bids, bid_problem = _levels(data.get("b"))
    asks, ask_problem = _levels(data.get("a"))
    problem = bid_problem or ask_problem or _book_problem(bids, asks)
    if problem is None and previous_sequence is not None and sequence < previous_sequence:
        problem = f"sequence went back from {previous_sequence} to {sequence}"
    return Snapshot(
        sequence=sequence,
        published_at=from_nanoseconds(published),
        exchange_ns=exchange if isinstance(exchange, int) else 0,
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
        self.market = market
        self.symbol = symbol
        self.depth = depth
        self._multiplier = multiplier
        # Nothing is healthy before the first subscription is confirmed.
        self._down: list[tuple[datetime, datetime | None]] = [(started_at, None)]
        self._snapshots: deque[Snapshot] = deque()
        self._max_sequence: int | None = None
        # Start of each confirmed subscription. A slot only uses snapshots received in the
        # subscription in force during the whole slot; older ones may describe a book that
        # changed while the connection was down.
        self._subscribed_at: list[datetime] = []
        self._next_slot = slot_start(started_at)
        self._minute_best: Snapshot | None = None
        self._minute_had_valid = False

    @property
    def next_slot(self) -> datetime:
        return self._next_slot

    def connection_up(self, at: datetime) -> None:
        """The subscription is confirmed: from now on the connection counts as healthy."""
        if self._down and self._down[-1][1] is None:
            self._down[-1] = (self._down[-1][0], at)
        self._subscribed_at.append(at)
        self._max_sequence = None

    def connection_down(self, at: datetime) -> None:
        if not self._down or self._down[-1][1] is not None:
            self._down.append((at, None))

    def on_message(self, message: dict[str, Any], received_at: datetime) -> None:
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
        snapshot = parse_snapshot(message, received_at, self._max_sequence)
        if snapshot.consistent and (
            self._max_sequence is None or snapshot.sequence > self._max_sequence
        ):
            self._max_sequence = snapshot.sequence
        self._snapshots.append(snapshot)

    def close_until(
        self, now: datetime, lateness: timedelta
    ) -> tuple[list[SlotResult], list[MinuteSnapshot]]:
        """Close every slot whose end plus ``lateness`` is not after ``now``."""
        slots: list[SlotResult] = []
        minutes: list[MinuteSnapshot] = []
        step = timedelta(seconds=SLOT_SECONDS)
        while self._next_slot + step + lateness <= now:
            start = self._next_slot
            result = self._close(start, start + step)
            slots.append(result)
            if result.state is SlotState.VALIDA:
                self._minute_had_valid = True
                self._minute_best = result.snapshot
            end = start + step
            if end.second == 0:
                if self._minute_had_valid and self._minute_best is not None:
                    minutes.append(
                        MinuteSnapshot(
                            self.market,
                            self.symbol,
                            self.depth,
                            end - timedelta(minutes=1),
                            self._minute_best,
                        )
                    )
                self._minute_best = None
                self._minute_had_valid = False
            self._next_slot = end
            self._forget_before(end)
        return slots, minutes

    def _close(self, start: datetime, end: datetime) -> SlotResult:
        if self._disconnected_during(start, end):
            return self._result(start, SlotState.DESCONECTADO, None, end)
        epoch = max((at for at in self._subscribed_at if at <= start), default=None)
        in_force = None
        for snapshot in self._snapshots:
            if epoch is not None and epoch <= snapshot.received_at < end:
                in_force = snapshot
        if in_force is None:
            return self._result(start, SlotState.NO_RECIBIDA, None, end)
        if not in_force.consistent:
            return self._result(start, SlotState.LIBRO_INCONSISTENTE, in_force, end)
        late = any(start <= s.published_at < end and s.received_at >= end for s in self._snapshots)
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
            age_ms = (end - snapshot.received_at) // timedelta(milliseconds=1)
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
