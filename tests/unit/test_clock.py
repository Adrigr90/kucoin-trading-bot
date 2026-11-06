"""Injectable clock and RFC 3339 timestamps (spec 02 §0 rule 3, §5.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from kbot.core.clock import Clock, ManualClock, SystemClock, from_rfc3339, to_rfc3339

START = datetime(2026, 11, 6, 0, 0, tzinfo=UTC)


def test_both_clocks_satisfy_the_protocol() -> None:
    assert isinstance(SystemClock(), Clock)
    assert isinstance(ManualClock(START), Clock)


def test_system_clock_returns_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_manual_clock_moves_only_forward() -> None:
    clock = ManualClock(START)
    assert clock.now() == START
    assert clock.advance(timedelta(minutes=1)) == START + timedelta(minutes=1)
    assert clock.set(START + timedelta(hours=1)) == START + timedelta(hours=1)
    with pytest.raises(ValueError, match="back"):
        clock.advance(timedelta(seconds=-1))
    with pytest.raises(ValueError, match="back"):
        clock.set(START)


@pytest.mark.parametrize(
    "bad",
    [datetime(2026, 11, 6), datetime(2026, 11, 6, tzinfo=timezone(timedelta(hours=1)))],
)
def test_manual_clock_only_accepts_utc(bad: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        ManualClock(bad)


def test_rfc3339_has_exactly_six_decimals_and_round_trips() -> None:
    moment = datetime(2026, 11, 6, 9, 30, 0, 5, tzinfo=UTC)
    text = to_rfc3339(moment)
    assert text == "2026-11-06T09:30:00.000005Z"
    assert from_rfc3339(text) == moment


def test_rfc3339_converts_other_offsets_to_utc() -> None:
    madrid_winter = timezone(timedelta(hours=1))
    assert to_rfc3339(datetime(2026, 11, 6, 10, 0, tzinfo=madrid_winter)) == (
        "2026-11-06T09:00:00.000000Z"
    )


@pytest.mark.parametrize(
    "text",
    ["2026-11-06T09:30:00Z", "2026-11-06T09:30:00.000000+00:00", "2026-11-06 09:30:00.000000Z"],
)
def test_rfc3339_parser_is_strict(text: str) -> None:
    with pytest.raises(ValueError):
        from_rfc3339(text)
