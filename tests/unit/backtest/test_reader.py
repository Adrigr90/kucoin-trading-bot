"""The reader of the evaluation never returns a minute of the test, and never opens one of
its days (``02`` §12.5.1, §12.5.5 and §12.5.6)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from kbot.backtest.partitions import TEST_START, SealedPartition
from kbot.backtest.reader import read_minutes
from kbot.history import store
from kbot.history.coverage import Presence
from tests.unit.backtest.history import write_history
from tests.unit.strategies.support import walk_minutes

FIRST = datetime(2026, 6, 25, tzinfo=UTC)
DAY = timedelta(days=1)


@pytest.fixture
def history(tmp_path: Path) -> Path:
    """Three days of one instrument: 25 and 26 June 2026, of the validation, and 27 June,
    the first of the test."""
    write_history(tmp_path, walk_minutes(3 * 1440, seed=3, start=FIRST))
    return tmp_path


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[date]:
    days: list[date] = []
    read_day = store.read_day

    def spy(root: Path, market: str, symbol: str, day: date):  # type: ignore[no-untyped-def]
        days.append(day)
        return read_day(root, market, symbol, day)

    monkeypatch.setattr(store, "read_day", spy)
    return days


def test_the_reader_stops_at_the_test_and_never_opens_its_days(
    history: Path, opened: list[date]
) -> None:
    read = read_minutes(history, "FUTURES", "ETHUSDTM", FIRST + timedelta(hours=12), TEST_START)
    times = [m.candle.open_time for m in read.minutes]
    assert len(times) == 1440 + 720
    assert times == sorted(times)
    assert times[0] == FIRST + timedelta(hours=12)
    assert max(times) < TEST_START
    assert opened == [date(2026, 6, 25), date(2026, 6, 26)]
    assert read.invalid_days == ()


def test_a_window_that_touches_the_test_is_refused_before_opening_anything(
    history: Path, opened: list[date]
) -> None:
    with pytest.raises(SealedPartition):
        read_minutes(history, "FUTURES", "ETHUSDTM", FIRST, TEST_START + timedelta(minutes=1))
    assert opened == []


def test_a_day_whose_files_are_not_a_valid_pair_gives_no_minute(history: Path) -> None:
    _parquet, record = store.paths(history, "FUTURES", "ETHUSDTM", date(2026, 6, 25))
    record.unlink()
    read = read_minutes(history, "FUTURES", "ETHUSDTM", FIRST, TEST_START)
    assert read.invalid_days == ((date(2026, 6, 25), "sin registro"),)
    assert min(m.candle.open_time for m in read.minutes) == FIRST + timedelta(days=1)
    # A day never downloaded is no fault: it simply has no minute.
    empty = read_minutes(history, "SPOT", "BTC-USDT", FIRST, TEST_START)
    assert (empty.minutes, empty.invalid_days) == ([], ())


def test_the_presence_of_the_minutes_asked(history: Path) -> None:
    """Only the minutes asked have a presence (F3E5-05): the days before what was asked,
    never touched by any request, have none. A day without a valid pair of files leaves its
    minutes asked ``no_descargado``."""
    read = read_minutes(history, "FUTURES", "ETHUSDTM", FIRST - 3 * DAY, TEST_START)
    assert min(read.states) == FIRST and len(read.states) == 2 * 1440
    assert set(read.states.values()) == {Presence.PRESENTE}
    _parquet, record = store.paths(history, "FUTURES", "ETHUSDTM", date(2026, 6, 26))
    record.unlink()
    read = read_minutes(history, "FUTURES", "ETHUSDTM", FIRST - 3 * DAY, TEST_START)
    states = Counter(read.states.values())
    assert states == {Presence.PRESENTE: 1440, Presence.NO_DESCARGADO: 1440}
