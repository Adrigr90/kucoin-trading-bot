"""The day files of the history: the columns of candles, exact values, atomic writes, the
record with its SHA-256, and closed days that are never written again (02 §12.1, point 1;
F3-A1)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from kbot.history.coverage import Request, Window
from kbot.history.store import (
    CANDLE_COLUMNS,
    SETTLING,
    Conflict,
    paths,
    read_candles,
    read_record,
    write_day,
)
from kbot.market.last_candles import Ohlcv
from tools.measure_archive import MIGRATION, table_specs

DAY = date(2026, 11, 10)
START = datetime(2026, 11, 10, tzinfo=UTC)
WHOLE = Window(START, START + timedelta(days=1))
END = START + timedelta(days=1)
NOW = END + SETTLING  # the first moment at which the day can close


def candle(minute: int, price: str = "73759.123456789012345678") -> Ohlcv:
    p = Decimal(price)
    return Ohlcv(
        START + timedelta(minutes=minute), p, p + 1, p - 1, p, Decimal("0.00012345"), p * 2
    )


def everything() -> list[Request]:
    return [Request(START, START + timedelta(minutes=1439), True)]


def test_the_columns_are_those_of_candles_in_the_migration() -> None:
    spec = table_specs(MIGRATION.read_text(encoding="utf-8"))["candles"]
    assert [(c.name, c.sql_type) for c in CANDLE_COLUMNS] == [
        (c.name, c.sql_type) for c in spec.columns
    ]


def test_a_day_is_written_and_read_back_exactly(tmp_path: Path) -> None:
    written = [candle(0), candle(1, "0.000000000000000001"), candle(1439)]
    record = write_day(tmp_path, "SPOT", "BTC-USDT", DAY, WHOLE, written, everything(), NOW)
    assert read_candles(tmp_path, "SPOT", "BTC-USDT", DAY) == written
    assert record.rows == 3
    assert read_record(tmp_path, "SPOT", "BTC-USDT", DAY) == record
    target, record_path = paths(tmp_path, "SPOT", "BTC-USDT", DAY)
    assert not list(target.parent.glob("*.tmp")), "no temporary file is left"
    assert record_path.exists()


def test_a_record_that_does_not_match_its_file_does_not_count(tmp_path: Path) -> None:
    write_day(tmp_path, "SPOT", "BTC-USDT", DAY, WHOLE, [candle(0)], everything(), NOW)
    target, _record = paths(tmp_path, "SPOT", "BTC-USDT", DAY)
    target.write_bytes(target.read_bytes() + b"x")
    assert read_record(tmp_path, "SPOT", "BTC-USDT", DAY) is None


def test_a_closed_day_is_never_written_again(tmp_path: Path) -> None:
    """The download is idempotent: a day that ended with every request answered is
    closed, also without any candle (an empty correct answer, CD-14)."""
    record = write_day(tmp_path, "SPOT", "BTC-USDT", DAY, WHOLE, [], everything(), NOW)
    assert record.closed and record.rows == 0
    with pytest.raises(FileExistsError, match="closed"):
        write_day(tmp_path, "SPOT", "BTC-USDT", DAY, WHOLE, [candle(0)], everything(), NOW)


def test_a_day_with_a_failed_request_or_not_over_stays_open(tmp_path: Path) -> None:
    failed = [
        Request(START, START + timedelta(minutes=99), True),
        Request(START + timedelta(minutes=100), START + timedelta(minutes=1439), False),
    ]
    first = write_day(tmp_path, "SPOT", "BTC-USDT", DAY, WHOLE, [candle(0)], failed, NOW)
    assert not first.closed
    again = write_day(tmp_path, "SPOT", "BTC-USDT", DAY, WHOLE, [candle(0)], everything(), NOW)
    assert again.closed, "a later download of the open day closes it"

    partial = Window(START, START + timedelta(hours=10))
    half = [Request(START, START + timedelta(hours=10) - timedelta(minutes=1), True)]
    today = write_day(tmp_path, "FUTURES", "XBTUSDTM", DAY, partial, [], half, NOW)
    assert not today.closed, "the window stops before the end of the day"


def test_a_day_closes_only_48_hours_after_its_end(tmp_path: Path) -> None:
    """CD-20: KuCoin may still change a recent day, as it did with DOT-USDT on 19 November
    2026. Downloaded less than 48 h after its end, a day stays open even with every request
    answered, and the next download writes it again; from 48 h on, it closes."""
    assert timedelta(hours=48) == SETTLING
    early = END + SETTLING - timedelta(microseconds=1)
    first = write_day(tmp_path, "SPOT", "BTC-USDT", DAY, WHOLE, [candle(0)], everything(), early)
    assert not first.closed
    again = write_day(
        tmp_path, "SPOT", "BTC-USDT", DAY, WHOLE, [candle(0), candle(1)], everything(), NOW
    )
    assert again.closed and again.rows == 2, "written again with the new answer, and closed"
    with pytest.raises(FileExistsError, match="closed"):
        write_day(tmp_path, "SPOT", "BTC-USDT", DAY, WHOLE, [], everything(), NOW)


def test_the_minutes_in_conflict_go_to_the_record_with_their_exact_candles(
    tmp_path: Path,
) -> None:
    """CD-19: none of the candles of a minute in conflict is in the Parquet file; all of
    them are in the record, exact, and the day still closes."""
    full, partial = candle(543, "0.7314"), candle(543, "0.000000000000000000000001")
    conflict = Conflict(full.open_time, (full, partial))
    record = write_day(
        tmp_path, "SPOT", "DOT-USDT", DAY, WHOLE, [candle(0)], everything(), NOW, [conflict]
    )
    assert record.closed, "a minute in conflict was answered: it keeps no day open"
    assert record.conflicts == (conflict,) and record.in_conflict == [full.open_time]
    assert read_record(tmp_path, "SPOT", "DOT-USDT", DAY) == record
    assert [c.open_time for c in read_candles(tmp_path, "SPOT", "DOT-USDT", DAY)] == [START]
    _target, record_path = paths(tmp_path, "SPOT", "DOT-USDT", DAY)
    (kept,) = json.loads(record_path.read_text(encoding="utf-8"))["en_conflicto"]
    assert kept["velas"][1][3] == "0.000000000000000000000001", "as text, exact"


def test_a_record_written_before_the_conflicts_has_none(tmp_path: Path) -> None:
    """Records written before 19 November 2026 lack the field: a conflict then stopped the
    download, so they have none."""
    record = write_day(tmp_path, "SPOT", "BTC-USDT", DAY, WHOLE, [candle(0)], everything(), NOW)
    _target, record_path = paths(tmp_path, "SPOT", "BTC-USDT", DAY)
    data = json.loads(record_path.read_text(encoding="utf-8"))
    assert data.pop("en_conflicto") == []
    record_path.write_text(json.dumps(data), encoding="utf-8")
    assert read_record(tmp_path, "SPOT", "BTC-USDT", DAY) == record
    assert record.conflicts == ()


def test_a_conflict_is_two_different_candles_of_an_answered_minute_without_a_candle(
    tmp_path: Path,
) -> None:
    one = candle(10)
    other = candle(10, "1.5")
    wrong = [
        ("two different candles", [], [Conflict(one.open_time, (one,))], everything()),
        ("two different candles", [], [Conflict(one.open_time, (one, one))], everything()),
        ("two different candles", [], [Conflict(START, (one, other))], everything()),
        ("present and in conflict", [one], [Conflict(one.open_time, (one, other))], everything()),
        ("without an answer", [], [Conflict(one.open_time, (one, other))], []),
    ]
    for message, candles, conflicts, requests in wrong:
        with pytest.raises(ValueError, match=message):
            write_day(tmp_path, "SPOT", "BTC-USDT", DAY, WHOLE, candles, requests, NOW, conflicts)
    late = candle(61)
    with pytest.raises(ValueError, match="conflict outside the window"):
        write_day(
            tmp_path,
            "SPOT",
            "BTC-USDT",
            DAY,
            Window(START, START + timedelta(hours=1)),
            [],
            everything(),
            NOW,
            [Conflict(late.open_time, (late, candle(61, "2")))],
        )
    assert not paths(tmp_path, "SPOT", "BTC-USDT", DAY)[0].exists(), "nothing written"


def test_a_candle_outside_the_window_or_a_window_outside_the_day_is_refused(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="outside the window"):
        write_day(
            tmp_path,
            "SPOT",
            "BTC-USDT",
            DAY,
            Window(START, START + timedelta(hours=1)),
            [candle(61)],
            everything(),
            NOW,
        )
    with pytest.raises(ValueError, match="within the day"):
        write_day(
            tmp_path,
            "SPOT",
            "BTC-USDT",
            DAY,
            Window(START, START + timedelta(days=2)),
            [],
            everything(),
            NOW,
        )
