"""Late books are stored one row per UTC day (02 §3.9; second review of the E3 design)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kbot.market.depth import LateBook
from kbot.market.store import late_book_rows


def book(first: datetime, last: datetime) -> LateBook:
    published, received = first + timedelta(seconds=1), last + timedelta(seconds=5)
    return LateBook("SPOT", "BTC-USDT", 5, first, last, 7, published, received)


def test_a_book_inside_one_day_is_one_row() -> None:
    t = datetime(2026, 11, 10, 12, 0, 10, tzinfo=UTC)
    (row,) = late_book_rows(book(t, t + timedelta(seconds=20)))
    assert row[3:6] == (t, t, t + timedelta(seconds=20))


def test_a_book_across_midnight_is_one_row_per_day_with_the_slot_of_its_p() -> None:
    before = datetime(2026, 11, 10, 23, 59, 50, tzinfo=UTC)
    after = before + timedelta(seconds=20)
    rows = late_book_rows(book(before, after))
    assert [row[3:6] for row in rows] == [
        (before, before, before),
        (before, datetime(2026, 11, 11, tzinfo=UTC), after),
    ]
