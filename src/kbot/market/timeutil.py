"""Exact UTC time helpers for market data (spec 02 §3.5: UTC, microseconds, [start, end))."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MILLISECOND = timedelta(milliseconds=1)
MINUTE = timedelta(minutes=1)


def as_utc(moment: datetime, name: str = "time") -> datetime:
    """Reject a naive datetime and express an aware one in UTC."""
    if not isinstance(moment, datetime):
        raise TypeError(f"{name} must be a datetime")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} must carry a time zone")
    return moment.astimezone(UTC)


def from_milliseconds(value: int) -> datetime:
    return EPOCH + timedelta(milliseconds=value)


def from_nanoseconds(value: int) -> datetime:
    """UTC time of a nanosecond timestamp, floored to the microsecond datetime can hold.
    The original integer is kept wherever it is stored."""
    return EPOCH + timedelta(microseconds=value // 1000)


def to_milliseconds(moment: datetime) -> int:
    """Exact milliseconds since the epoch; the moment must fall on a whole millisecond."""
    elapsed = as_utc(moment) - EPOCH
    if elapsed % MILLISECOND:
        raise ValueError("the moment is not on a whole millisecond")
    return elapsed // MILLISECOND


def floor_to(moment: datetime, step: timedelta) -> datetime:
    """Start of the ``step``-long interval that contains ``moment``."""
    return EPOCH + ((as_utc(moment) - EPOCH) // step) * step


def is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
