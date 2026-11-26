"""The requests to Object Storage: counted, and never above a cap (02 §13.1, point 4,
E4c-2, point 5; E4C2-03 and its residual).

Always Free gives 50,000 requests of the Object Storage API a month. Oracle counts by
month, and any month, wherever it starts and in any time zone, fits in 32 consecutive UTC
days, so the cap holds whenever Oracle's month begins: **at most 45,000 requests in any
window of 32 UTC days**. The 5,000 left are the margin for what the service does not see
(the owner's console and the lifecycle rule).

Every request is reserved before it is sent: one more in the counter of its UTC day, made
durable (``durable.write_json``), and only then the request goes out. A reservation that
would pass the cap raises ``BudgetExhausted``; one that cannot be made durable raises
``PersistenceError``; in both cases nothing is sent. A crash after a reservation leaves the
request counted although it never left: the count errs to the safe side, never below.
Without its file, or with one that cannot be read, the counter refuses every reservation:
05_copias.sh creates it, empty, when it installs the service.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from kbot.upload import durable

CAP = 45_000
SAVING = 40_000  # above it, only the essential: the listing and the sealed-day copies
WINDOW_DAYS = 32
FORMAT = "kbot-peticiones-1"


class BudgetExhausted(Exception):
    """The next request would pass the cap: it is not sent."""


class CounterUnreadable(Exception):
    """The counter is missing or cannot be read: no request can be sent."""


def empty() -> dict[str, object]:
    return {"formato": FORMAT, "dias": {}}


class RequestBudget:
    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ops: durable.FileOps = durable.REAL,
    ) -> None:
        self.path = path
        self._clock = clock
        self._ops = ops
        self._days: dict[date, int] | None = None

    def _load(self) -> dict[date, int]:
        if self._days is None:
            try:
                value = durable.read_json(self.path)
                if not isinstance(value, dict) or value.get("formato") != FORMAT:
                    raise ValueError("not a request counter")
                days = {date.fromisoformat(k): int(v) for k, v in value["dias"].items()}
                if any(v < 0 for v in days.values()):
                    raise ValueError("a negative count")
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                raise CounterUnreadable(f"{self.path}: {error}") from error
            self._days = days
        return self._days

    def _window(self, today: date) -> dict[date, int]:
        first = today - timedelta(days=WINDOW_DAYS - 1)
        return {d: n for d, n in self._load().items() if first <= d <= today}

    def used(self) -> int:
        """The requests of the last 32 UTC days, today included."""
        return sum(self._window(self._clock().astimezone(UTC).date()).values())

    def remaining(self) -> int:
        return max(0, CAP - self.used())

    def saving(self) -> bool:
        return self.used() >= SAVING

    def covers(self, requests: int) -> bool:
        return self.remaining() >= requests

    def reserve(self) -> None:
        """One request, counted durably before it is sent; raises if it may not be sent."""
        today = self._clock().astimezone(UTC).date()
        days = self._window(today)
        if sum(days.values()) + 1 > CAP:
            raise BudgetExhausted(f"{CAP} requests in {WINDOW_DAYS} days")
        days[today] = days.get(today, 0) + 1
        durable.write_json(
            self.path,
            {"formato": FORMAT, "dias": {d.isoformat(): n for d, n in sorted(days.items())}},
            self._ops,
        )
        self._days = days
