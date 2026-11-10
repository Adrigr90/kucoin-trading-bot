"""The canonical fingerprint of the archive (02 §3.9; review of the E3 design)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kbot.archive.canonical import Fingerprint, canonical_value, decimal_text, json_text


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("1.500", "1.5"),
        ("100", "100"),
        ("1E+2", "100"),
        ("-0", "0"),
        ("-0.000", "0"),
        ("0E-18", "0"),
        ("-1E-18", "-0.000000000000000001"),
        ("12345678901234567890.123456789012345678", "12345678901234567890.123456789012345678"),
    ],
)
def test_a_number_has_one_text(value: str, text: str) -> None:
    assert decimal_text(Decimal(value)) == text


def test_each_value_has_one_canonical_form() -> None:
    moment = datetime(2026, 11, 10, 12, 0, 0, 123456, tzinfo=UTC)
    assert canonical_value(moment) == "2026-11-10T12:00:00.123456Z"
    assert canonical_value(moment.astimezone(timezone(timedelta(hours=1)))) == (
        "2026-11-10T12:00:00.123456Z"
    )
    assert canonical_value(None) is None
    assert canonical_value(True) is True
    assert canonical_value(7) == "7"
    assert canonical_value(date(2026, 11, 10)) == "2026-11-10"
    with pytest.raises(ValueError):
        canonical_value(datetime(2026, 11, 10))  # a moment without its time zone
    with pytest.raises(TypeError):
        canonical_value(1.5)  # floats never reach the archive


def test_the_fingerprint_does_not_depend_on_how_a_value_is_written() -> None:
    a, b = Fingerprint(["x", "t"]), Fingerprint(["x", "t"])
    a.add((Decimal("1.50"), datetime(2026, 11, 10, 12, tzinfo=UTC)))
    b.add(
        (
            Decimal("1.500000000000000000"),
            datetime(2026, 11, 10, 13, tzinfo=timezone(timedelta(hours=1))),
        )
    )
    assert (a.rows, a.hexdigest()) == (1, b.hexdigest())


def test_the_fingerprint_sees_the_order_the_columns_and_the_content() -> None:
    def fingerprint(columns: list[str], rows: list[tuple[Decimal, ...]]) -> str:
        running = Fingerprint(columns)
        for row in rows:
            running.add(row)
        return running.hexdigest()

    rows = [(Decimal(1),), (Decimal(2),)]
    assert fingerprint(["x"], rows) != fingerprint(["x"], rows[::-1])
    assert fingerprint(["x"], rows) != fingerprint(["y"], rows)
    assert fingerprint(["x"], rows) != fingerprint(["x"], [(Decimal(1),), (Decimal(3),)])


def test_jsonb_is_stored_as_its_jcs_text() -> None:
    assert json_text([["100.5", "2"], ["100.4", "1"]]) == '[["100.5","2"],["100.4","1"]]'
    assert json_text({"b": 1, "a": "x"}) == '{"a":"x","b":1}'
