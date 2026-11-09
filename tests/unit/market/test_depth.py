"""Depth slots and metrics (spec 02 §3.3, §3.5 and §3.8; acceptance F2-A5 and F2-A8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kbot.kucoin.jsonparse import loads
from kbot.market.depth import (
    DepthChannel,
    SlotState,
    compute_metrics,
    effective_state,
    parse_snapshot,
    slot_start,
)

T0 = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)
NO_LATENESS = timedelta(0)
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "kucoin"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def book(
    sequence: int,
    published: datetime,
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
) -> dict[str, Any]:
    ns = (published - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1) * 1000
    return {
        "T": "obu.SPOT",
        "dp": "5",
        "t": "snapshot",
        "P": ns,
        "d": {
            "s": "BTC-USDT",
            "C": sequence,
            "O": sequence,
            "M": ns,
            "b": bids if bids is not None else [["100.0", "2"], ["99.9", "1"]],
            "a": asks if asks is not None else [["100.2", "1"], ["100.3", "3"]],
        },
    }


def channel(started: float = 0) -> DepthChannel:
    return DepthChannel("SPOT", "BTC-USDT", 5, multiplier=Decimal(1), started_at=at(started))


def states(results: list[Any]) -> list[str]:
    return [r.state.value for r in results]


def test_valid_book_and_silence_keep_the_slot_valid() -> None:
    ch = channel()
    ch.connection_up(at(0.5))
    ch.on_message(book(1, at(1)), at(1.1))
    slots, _ = ch.close_until(at(40), NO_LATENESS)
    # First slot [0, 10) had no healthy connection at 0..0.5: desconectado.
    assert states(slots) == ["desconectado", "valida", "valida", "valida"]
    # The book did not change for 30 s: still valid, with its age since KuCoin published it.
    assert [r.age_ms for r in slots[1:]] == [19_000, 29_000, 39_000]
    assert all(r.sequence == 1 for r in slots[1:])


def test_no_snapshot_since_the_subscription_is_no_recibida() -> None:
    ch = channel()
    ch.connection_up(at(0))
    slots, _ = ch.close_until(at(20), NO_LATENESS)
    assert states(slots) == ["no_recibida", "no_recibida"]


def test_a_disconnection_marks_every_slot_it_touches() -> None:
    ch = channel()
    ch.connection_up(at(0))
    ch.on_message(book(1, at(1)), at(1))
    ch.connection_down(at(12))
    ch.connection_up(at(31))
    slots, _ = ch.close_until(at(50), NO_LATENESS)
    assert states(slots) == [
        "valida",
        "desconectado",
        "desconectado",
        "desconectado",
        "no_recibida",
    ]


def test_the_old_book_does_not_survive_a_reconnection() -> None:
    ch = channel()
    ch.connection_up(at(0))
    ch.on_message(book(5, at(1)), at(1))
    ch.connection_down(at(10))
    ch.connection_up(at(10.5))
    ch.on_message(book(9, at(25)), at(25))
    slots, _ = ch.close_until(at(30), NO_LATENESS)
    assert states(slots) == ["valida", "desconectado", "valida"]
    assert slots[2].sequence == 9


@pytest.mark.parametrize(
    ("bids", "asks", "problem"),
    [
        ([["100.3", "1"]], [["100.2", "1"]], "crossed"),
        ([["100.0", "1"]], [["100.0", "1"]], "crossed"),
        ([["99.9", "1"], ["100.0", "1"]], [["100.2", "1"]], "descending"),
        ([["100.0", "1"]], [["100.3", "1"], ["100.2", "1"]], "ascending"),
        ([["100.0", "0"]], [["100.2", "1"]], "non-positive"),
        ([["100.0", "-1"]], [["100.2", "1"]], "non-positive"),
        ([], [["100.2", "1"]], "empty"),
        ([["abc", "1"]], [["100.2", "1"]], "numeric"),
        ([[100.0, "1"]], [["100.2", "1"]], "as text"),
    ],
)
def test_inconsistent_books(bids: list[Any], asks: list[Any], problem: str) -> None:
    ch = channel()
    ch.connection_up(at(0))
    ch.on_message(book(1, at(1), bids, asks), at(1))
    slots, _ = ch.close_until(at(10), NO_LATENESS)
    assert states(slots) == ["libro_inconsistente"]
    assert slots[0].metrics is None
    snapshot = parse_snapshot(book(1, at(1), bids, asks), at(1), None)
    assert problem in str(snapshot.problem)


def test_a_sequence_that_goes_back_is_inconsistent_and_a_duplicate_is_ignored() -> None:
    ch = channel()
    ch.connection_up(at(0))
    ch.on_message(book(7, at(1)), at(1))
    ch.on_message(book(7, at(2), bids=[["50", "1"]]), at(2))  # same C: duplicate, ignored
    first, _ = ch.close_until(at(10), NO_LATENESS)
    assert states(first) == ["valida"] and first[0].metrics.best_bid == Decimal("100.0")  # type: ignore[union-attr]
    ch.on_message(book(6, at(11)), at(11))  # C went back
    second, _ = ch.close_until(at(20), NO_LATENESS)
    assert states(second) == ["libro_inconsistente"]
    ch.on_message(book(8, at(21)), at(21))
    third, _ = ch.close_until(at(30), NO_LATENESS)
    assert states(third) == ["valida"]


def test_a_publication_time_that_goes_back_makes_the_book_inconsistent() -> None:
    """Review of E2, third round (I-E2-07): the sequence grows (100, then 101) but KuCoin's
    publication time goes back (5 s, then 4 s). The book is inconsistent, without metrics,
    until a book published later arrives."""
    ch = channel()
    ch.connection_up(at(0))
    ch.on_message(book(100, at(5)), at(5.1))
    ch.on_message(book(101, at(4)), at(5.2))
    slots, _ = ch.close_until(at(10), NO_LATENESS)
    assert states(slots) == ["libro_inconsistente"] and slots[0].metrics is None
    snapshot = parse_snapshot(book(101, at(4)), at(5.2), 100, at(5))
    assert snapshot.problem == "publication time went back"
    ch.on_message(book(102, at(15)), at(15))
    slots, _ = ch.close_until(at(20), NO_LATENESS)
    assert states(slots) == ["valida"]


def test_ordinary_latency_never_makes_a_slot_late() -> None:
    """Live run of 8 November 2026: a book published just before the end of a slot and
    received just after it is on time; it is the book in force of its slot."""
    ch = channel()
    ch.connection_up(at(0))
    ch.on_message(book(1, at(1)), at(1))
    ch.on_message(book(2, at(9.95)), at(10.08))  # 130 ms on the way, within the margin
    slots, _ = ch.close_until(at(12), timedelta(seconds=2))
    assert states(slots) == ["valida"]
    assert slots[0].sequence == 2


def test_a_snapshot_that_arrives_after_the_deadline_makes_its_slot_late() -> None:
    ch = channel()
    ch.connection_up(at(0))
    ch.on_message(book(1, at(1)), at(1))
    ch.on_message(book(2, at(9.5)), at(11.5))  # published in [0, 10), 1.5 s after 10 + 1
    slots, _ = ch.close_until(at(12), timedelta(seconds=1))
    assert states(slots) == ["fuera_de_plazo"]
    assert slots[0].sequence == 1, "the book in force at the deadline was older"


def test_a_late_book_with_no_earlier_one_makes_the_slot_late_not_missing() -> None:
    """Audit E2-03: the only book of the slot was published in it and arrived after the
    deadline. Something was received, too late: fuera_de_plazo, never no_recibida."""
    ch = channel()
    ch.connection_up(at(0))
    ch.on_message(book(1, at(9.5)), at(11.5))  # published in [0, 10), 1.5 s after 10 + 1
    slots, _ = ch.close_until(at(12), timedelta(seconds=1))
    assert states(slots) == ["fuera_de_plazo"]
    assert (slots[0].sequence, slots[0].age_ms) == (None, None), "no book was in force"
    following, _ = ch.close_until(at(21), timedelta(seconds=1))
    assert states(following) == ["valida"] and following[0].sequence == 1


def test_a_late_book_is_stored_and_its_slot_counts_as_late() -> None:
    """Review of E2, fourth round (I-E2-04), the reviewer's sequence: [0, 10) is closed as
    valid at 12 s, a book published at 9.5 s arrives at 13 s and a correct one at 14 s,
    before the next close. [10, 20) is valid with the correct book; the late book is kept
    for the database, so [0, 10) counts as fuera_de_plazo from then on."""
    ch = channel()
    ch.connection_up(at(0))
    ch.on_message(book(1, at(1)), at(1))
    slots, _ = ch.close_until(at(12), timedelta(seconds=2))
    assert states(slots) == ["valida"]
    ch.on_message(book(2, at(9.5)), at(13))
    ch.on_message(book(3, at(14)), at(14))
    (late,) = ch.take_late_books()
    assert (late.slot_start, late.last_slot_start, late.sequence) == (at(0), at(0), 2)
    assert (late.published_at, late.received_at) == (at(9.5), at(13))
    assert ch.take_late_books() == [], "handed over once"
    slots, _ = ch.close_until(at(22), timedelta(seconds=2))
    assert states(slots) == ["valida"] and slots[0].sequence == 3
    assert effective_state(SlotState.VALIDA, True) is SlotState.FUERA_DE_PLAZO
    assert effective_state(SlotState.NO_RECIBIDA, True) is SlotState.FUERA_DE_PLAZO
    for state in (SlotState.DESCONECTADO, SlotState.LIBRO_INCONSISTENTE, SlotState.FUERA_DE_PLAZO):
        assert effective_state(state, True) is state, "already not healthy, or already late"
    assert effective_state(SlotState.VALIDA, False) is SlotState.VALIDA


def test_a_late_book_contradicts_every_slot_closed_since_its_own() -> None:
    ch = channel()
    ch.connection_up(at(0))
    ch.on_message(book(1, at(1)), at(1))
    assert states(ch.close_until(at(32), timedelta(seconds=2))[0]) == ["valida"] * 3
    ch.on_message(book(2, at(9.5)), at(33))
    (late,) = ch.take_late_books()
    assert (late.slot_start, late.last_slot_start) == (at(0), at(20))
    slots, _ = ch.close_until(at(42), timedelta(seconds=2))
    assert states(slots) == ["libro_inconsistente"], "never a valid book for what follows"


def test_a_book_published_before_the_subscription_is_not_late() -> None:
    """A book KuCoin published before the connection subscribed again describes the book it
    had then: it is not late, and it is valid with its age."""
    ch = channel()
    ch.connection_up(at(0))
    ch.connection_down(at(5))
    ch.close_until(at(30), timedelta(seconds=2))  # [0, 10) and [10, 20): desconectado
    ch.connection_up(at(25))
    ch.on_message(book(4, at(15)), at(26))  # published in [10, 20), before subscribing
    assert ch.take_late_books() == []
    slots, _ = ch.close_until(at(42), timedelta(seconds=2))
    assert states(slots) == ["desconectado", "valida"]
    assert (slots[1].sequence, slots[1].age_ms) == (4, 25_000)


def test_a_contradicted_slot_never_gives_the_snapshot_of_its_minute() -> None:
    ch = channel(started=-0.001)
    ch.connection_up(at(0))
    for sequence, moment in ((1, 5), (2, 25), (3, 45)):
        ch.on_message(book(sequence, at(moment)), at(moment))
    ch.close_until(at(50), NO_LATENESS)  # [0, 50) valid; the minute is still open
    ch.on_message(book(4, at(35)), at(50.5))  # published in [30, 40), already closed
    (late,) = ch.take_late_books()
    assert (late.slot_start, late.last_slot_start) == (at(30), at(40))
    slots, minutes = ch.close_until(at(60), NO_LATENESS)
    assert states(slots) == ["libro_inconsistente"]
    (minute,) = minutes
    assert (minute.slot_start, minute.snapshot.sequence) == (at(20), 2), "the last one standing"


def test_a_book_published_after_the_end_belongs_to_the_next_slot() -> None:
    ch = channel()
    ch.connection_up(at(0))
    ch.on_message(book(1, at(1)), at(1))
    ch.on_message(book(2, at(10.2)), at(10.3))
    slots, _ = ch.close_until(at(22), timedelta(seconds=2))
    assert [s.sequence for s in slots] == [1, 2]
    assert states(slots) == ["valida", "valida"]


def test_slots_close_only_after_the_lateness_margin() -> None:
    ch = channel()
    ch.connection_up(at(0))
    assert ch.close_until(at(10.9), timedelta(seconds=1)) == ([], [])
    slots, _ = ch.close_until(at(11), timedelta(seconds=1))
    assert len(slots) == 1


def test_the_last_valid_book_of_each_minute_is_kept() -> None:
    ch = channel(started=-0.001)
    ch.connection_up(at(0))
    ch.on_message(book(1, at(5)), at(5))
    ch.on_message(book(2, at(45)), at(45))
    ch.connection_down(at(55))
    slots, minutes = ch.close_until(at(60), NO_LATENESS)
    assert states(slots)[-1] == "desconectado"
    (minute,) = minutes
    assert minute.minute_start == T0 and minute.snapshot.sequence == 2


def test_no_minute_snapshot_without_a_valid_slot() -> None:
    ch = channel()
    ch.connection_up(at(0))
    _, minutes = ch.close_until(at(60), NO_LATENESS)
    assert minutes == []


def test_messages_before_the_subscription_do_not_count() -> None:
    ch = channel()
    ch.on_message(book(1, at(1)), at(1))
    ch.connection_up(at(2))
    slots, _ = ch.close_until(at(20), NO_LATENESS)
    assert states(slots) == ["desconectado", "no_recibida"]


def test_metrics_are_exact() -> None:
    snapshot = parse_snapshot(
        book(
            1,
            at(1),
            bids=[["100.0", "2"], ["99.95", "1"], ["99.4", "5"]],
            asks=[["100.2", "1.5"], ["100.25", "3"], ["101", "1"]],
        ),
        at(1),
        None,
    )
    metrics = compute_metrics(snapshot, Decimal("0.001"))
    assert metrics.mid == Decimal("100.1")
    assert metrics.spread == Decimal("0.2")
    assert (metrics.bid_levels, metrics.ask_levels) == (3, 3)
    assert metrics.bid_quantity == Decimal(8) and metrics.ask_quantity == Decimal("5.5")
    # Notional with the Futures multiplier: 100*2 + 99.95*1 + 99.4*5, times 0.001.
    assert metrics.bid_notional == Decimal("0.79695")
    # Band ±0.1 % of 100.1 is [99.9999, 100.2001]: bids 100.0 only, asks 100.2 only.
    assert metrics.bid_notional_in_band[0] == Decimal("0.2")
    assert metrics.ask_notional_in_band[0] == Decimal("0.1503")
    # Band ±0.5 % is [99.5995, 100.6005]: bids 100.0 and 99.95; asks 100.2 and 100.25.
    assert metrics.bid_notional_in_band[1] == Decimal("0.29995")
    assert metrics.ask_notional_in_band[1] == Decimal("0.45105")
    assert (metrics.bid_deepest_price, metrics.ask_deepest_price) == (Decimal("99.4"), Decimal(101))


def test_metrics_of_a_real_recorded_book() -> None:
    fixture = loads((FIXTURES / "ws_futures.json").read_bytes())
    message = fixture["samples"]["obu_50"][0]["message"]
    snapshot = parse_snapshot(message, at(0), None)
    assert snapshot.consistent
    metrics = compute_metrics(snapshot, Decimal("0.001"))
    assert metrics.bid_levels == metrics.ask_levels == 50
    assert metrics.best_bid < metrics.mid < metrics.best_ask
    assert (
        metrics.bid_notional_in_band[0] <= metrics.bid_notional_in_band[1] <= metrics.bid_notional
    )


def test_slot_start_is_exact() -> None:
    assert slot_start(at(19.999999)) == at(10)
    assert slot_start(at(20)) == at(20)
    assert slot_start(datetime(2026, 11, 7, 23, 59, 59, 999999, tzinfo=UTC)).second == 50


EVENTS = st.lists(
    st.tuples(
        st.sampled_from(["up", "down", "book", "bad", "dup", "back", "late"]),
        st.integers(min_value=0, max_value=60_000),
    ),
    max_size=40,
)


@settings(max_examples=200, deadline=None)
@given(EVENTS)
def test_every_slot_gets_exactly_one_terminal_state(events: list[tuple[str, int]]) -> None:
    """F2-A8: whatever happens, slots are contiguous and each has exactly one state."""
    ch = channel()
    sequence = 0
    results = []
    for kind, ms in sorted(events, key=lambda e: e[1]):
        moment = at(ms / 1000)
        part, _ = ch.close_until(moment, NO_LATENESS)
        results += part
        if kind == "up":
            ch.connection_up(moment)
        elif kind == "down":
            ch.connection_down(moment)
        elif kind in {"book", "late"}:
            sequence += 1
            published = moment - timedelta(seconds=12) if kind == "late" else moment
            ch.on_message(book(sequence, published), moment)
        elif kind == "bad":
            sequence += 1
            ch.on_message(book(sequence, moment, bids=[["101", "1"]]), moment)
        elif kind == "dup":
            ch.on_message(book(sequence, moment), moment)
        else:
            ch.on_message(book(max(sequence - 3, 0), moment), moment)
    part, _ = ch.close_until(at(70), NO_LATENESS)
    results += part
    starts = [r.slot_start for r in results]
    assert starts == [at(10 * i) for i in range(7)]
    for result in results:
        assert isinstance(result.state, SlotState)
        assert (result.metrics is not None) == (result.state is SlotState.VALIDA)


def test_m_is_kept_exactly_and_never_invented() -> None:
    """Review of E2, problem 4: a missing or malformed M is None, never 0 (1970)."""
    message = book(1, at(1))
    assert parse_snapshot(message, at(1), None).exchange_ns == message["d"]["M"]
    for value in (None, "1794052801000000000", 1.7e18, True):
        variant = {**message, "d": {**message["d"], "M": value}}
        if value is None:
            del variant["d"]["M"]
        assert parse_snapshot(variant, at(1), None).exchange_ns is None


def test_times_must_carry_a_zone_and_are_normalized_to_utc() -> None:
    """Review of E2, problem 5."""
    naive = datetime(2026, 11, 7, 12, 0, 1)
    with pytest.raises(ValueError, match="time zone"):
        DepthChannel("SPOT", "BTC-USDT", 5, multiplier=Decimal(1), started_at=naive)
    ch = channel()
    for call in (
        lambda: ch.connection_up(naive),
        lambda: ch.connection_down(naive),
        lambda: ch.on_message(book(1, at(1)), naive),
        lambda: ch.close_until(naive, NO_LATENESS),
    ):
        with pytest.raises(ValueError, match="time zone"):
            call()
    madrid = timezone(timedelta(hours=1))
    ch.connection_up(datetime(2026, 11, 7, 13, 0, 0, tzinfo=madrid))
    ch.on_message(book(1, at(1)), datetime(2026, 11, 7, 13, 0, 1, tzinfo=madrid))
    slots, _ = ch.close_until(datetime(2026, 11, 7, 13, 0, 10, tzinfo=madrid), NO_LATENESS)
    assert states(slots) == ["valida"] and slots[0].slot_start == T0
    assert slots[0].slot_start.tzinfo is UTC


def test_a_malformed_message_makes_the_book_inconsistent_without_inventing_data() -> None:
    ch = channel()
    ch.connection_up(at(0))
    ch.on_message(book(1, at(1)), at(1))
    ch.on_message({"T": "obu.SPOT", "d": {"b": [], "a": []}}, at(2))
    slots, _ = ch.close_until(at(10), NO_LATENESS)
    assert states(slots) == ["libro_inconsistente"]
    assert slots[0].sequence is None and slots[0].metrics is None
    ch.on_message(book(2, at(11)), at(11))
    slots, _ = ch.close_until(at(20), NO_LATENESS)
    assert states(slots) == ["valida"]
