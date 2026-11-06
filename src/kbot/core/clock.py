"""Injectable clock: the same logic runs in backtest and paper trading (spec 02 §0, rule 3).

Only the clock changes between modes. Every time is timezone-aware UTC, and its text form
is RFC 3339 in UTC with exactly 6 decimals (02 §5.7).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

_RFC3339_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")


@runtime_checkable
class Clock(Protocol):
    """Source of the current time. Implementations return aware UTC datetimes."""

    def now(self) -> datetime: ...


class SystemClock:
    """Real time, for paper trading."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Time that only moves when told to, for backtests and tests. It never goes back."""

    def __init__(self, start: datetime) -> None:
        self._now = _require_utc(start)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("the clock cannot go back")
        self._now += delta
        return self._now

    def set(self, when: datetime) -> datetime:
        when = _require_utc(when)
        if when < self._now:
            raise ValueError("the clock cannot go back")
        self._now = when
        return self._now


def to_rfc3339(moment: datetime) -> str:
    """RFC 3339 text in UTC with 6 decimals, e.g. ``2026-11-06T09:30:00.000000Z``."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("naive datetimes are not allowed")
    utc = moment.astimezone(UTC)
    return (
        f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}T"
        f"{utc.hour:02d}:{utc.minute:02d}:{utc.second:02d}.{utc.microsecond:06d}Z"
    )


def from_rfc3339(text: str) -> datetime:
    """Parse the exact form produced by :func:`to_rfc3339`."""
    if not isinstance(text, str) or not _RFC3339_UTC.fullmatch(text):
        raise ValueError(f"not an RFC 3339 UTC timestamp with 6 decimals: {text!r}")
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _require_utc(moment: datetime) -> datetime:
    if not isinstance(moment, datetime):
        raise TypeError("expected a datetime")
    if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
        raise ValueError("the clock only accepts aware UTC datetimes")
    return moment.astimezone(UTC)
