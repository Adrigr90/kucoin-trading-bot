"""The partitions of the history and the folds of the development (``02`` §12.5.1 and
§12.5.2; F3-AL-03), fixed before any engine ran over the real history.

* **Partitions,** of UTC calendar and the same for every instrument, at the UTC midnight
  nearest to 60 % and 80 % of the history downloaded, [19 Nov 2024, 19 Nov 2026 22:10):
  development [19 Nov 2024, 1 Feb 2026), validation [1 Feb 2026, 27 Jun 2026) and test
  [27 Jun 2026, 19 Nov 2026 22:10). Spot and Futures share them, although the Futures
  minutes only start on 1 January 2025.
* **The test is sealed until the F8:** no window of the F3 reaches it, and ``segments``
  refuses it.
* **The additional sample** (F3E5-04): only what the system records from the end of the
  fixed history on, 19 Nov 2026 22:10 UTC. What it recorded from 17 to 19 November are the
  same instruments and minutes as the test, by another channel, sealed with it.
* **Folds:** the development in four consecutive windows of the engine, each cut at the UTC
  midnight nearest to its quarter. Each one starts without positions and warms the
  indicators up with the 30 days before it, or from the first minute of the history if
  there are fewer: a declared rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from kbot.backtest.engine import Window

DEVELOPMENT = "desarrollo"
VALIDATION = "validacion"
TEST = "prueba"
ADDITIONAL = "adicional"

HISTORY_START = datetime(2024, 11, 19, tzinfo=UTC)
# The end of the fixed history: the window of the last download, on 19 November 2026.
HISTORY_END = datetime(2026, 11, 19, 22, 10, tzinfo=UTC)
VALIDATION_START = datetime(2026, 2, 1, tzinfo=UTC)
TEST_START = datetime(2026, 6, 27, tzinfo=UTC)
WARMUP = timedelta(days=30)


class SealedPartition(ValueError):
    """A window that touches the test partition, sealed until the F8."""


@dataclass(frozen=True)
class Partition:
    name: str
    start: datetime
    end: datetime


PARTITIONS = (
    Partition(DEVELOPMENT, HISTORY_START, VALIDATION_START),
    Partition(VALIDATION, VALIDATION_START, TEST_START),
    Partition(TEST, TEST_START, HISTORY_END),
)


@dataclass(frozen=True)
class Segment:
    """A window of the engine, and from when its minutes warm the indicators up."""

    name: str
    window: Window
    warmup_from: datetime

    def __post_init__(self) -> None:
        if self.window.end > TEST_START:
            raise SealedPartition(f"{self.name}: its window reaches the test partition")
        if self.warmup_from > self.window.start:
            raise ValueError(f"{self.name}: the warm-up must come before the window")


def segment(name: str, start: datetime, end: datetime, warmup: timedelta = WARMUP) -> Segment:
    """The window [start, end), warmed up with ``warmup`` before it, never before the first
    minute of the history."""
    return Segment(name, Window(start, end), max(start - warmup, min(HISTORY_START, start)))


FOLD_CUTS = (
    HISTORY_START,
    datetime(2025, 3, 9, tzinfo=UTC),
    datetime(2025, 6, 27, tzinfo=UTC),
    datetime(2025, 10, 14, tzinfo=UTC),
    VALIDATION_START,
)
FOLDS = tuple(
    segment(f"tramo {number}", start, end)
    for number, (start, end) in enumerate(pairwise(FOLD_CUTS), start=1)
)
VALIDATION_SEGMENT = segment(VALIDATION, VALIDATION_START, TEST_START)


def segments(partition: str) -> tuple[Segment, ...]:
    """The windows of the engine of a partition: the four folds of the development, or the
    validation whole. The test is never read in the F3."""
    if partition == DEVELOPMENT:
        return FOLDS
    if partition == VALIDATION:
        return (VALIDATION_SEGMENT,)
    if partition == TEST:
        raise SealedPartition("the test partition is sealed until the F8")
    raise ValueError(f"unknown partition {partition!r}")


def partition_of(minute: datetime) -> str:
    """Where an observation of any instrument falls: one partition, or, from the end of the
    fixed history on, the additional sample. The instrument does not matter: the dates are
    the same for all."""
    if minute < HISTORY_START:
        raise ValueError(f"{minute} is before the history")
    for partition in PARTITIONS:
        if partition.start <= minute < partition.end:
            return partition.name
    return ADDITIONAL
