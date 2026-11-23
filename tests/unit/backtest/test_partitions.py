"""The partitions and the folds with their exact dates, the additional sample (F3E5-04) and
the warm-up of 30 days (F3E5-03), ``02`` §12.5.1, §12.5.2 and §12.5.6."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kbot.backtest.partitions import (
    ADDITIONAL,
    DEVELOPMENT,
    FOLDS,
    HISTORY_END,
    HISTORY_START,
    PARTITIONS,
    TEST,
    TEST_START,
    VALIDATION,
    WARMUP,
    SealedPartition,
    partition_of,
    segment,
    segments,
)
from kbot.indicators.price import EMA
from kbot.indicators.series import compute, from_minute
from tests.unit.strategies.support import minute


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def days(start: datetime, end: datetime) -> float:
    return (end - start) / timedelta(days=1)


def test_the_three_partitions_with_their_exact_dates() -> None:
    assert [(p.name, p.start, p.end) for p in PARTITIONS] == [
        (DEVELOPMENT, utc(2024, 11, 19), utc(2026, 2, 1)),
        (VALIDATION, utc(2026, 2, 1), utc(2026, 6, 27)),
        (TEST, utc(2026, 6, 27), utc(2026, 11, 19, 22, 10)),
    ]
    whole = days(HISTORY_START, HISTORY_END)
    shares = [
        (round(days(p.start, p.end), 2), round(100 * days(p.start, p.end) / whole, 2))
        for p in PARTITIONS
    ]
    assert shares == [(439, 60.06), (146, 19.97), (145.92, 19.96)]
    assert PARTITIONS[2].start == TEST_START


def test_the_four_folds_of_the_development_and_their_warm_up() -> None:
    assert [(f.name, f.window.start, f.window.end) for f in FOLDS] == [
        ("tramo 1", utc(2024, 11, 19), utc(2025, 3, 9)),
        ("tramo 2", utc(2025, 3, 9), utc(2025, 6, 27)),
        ("tramo 3", utc(2025, 6, 27), utc(2025, 10, 14)),
        ("tramo 4", utc(2025, 10, 14), utc(2026, 2, 1)),
    ]
    assert [days(f.window.start, f.window.end) for f in FOLDS] == [110, 110, 109, 110]
    assert segments(DEVELOPMENT) == FOLDS
    # The first fold has no 30 days before it: it warms up from the first minute.
    assert [f.warmup_from for f in FOLDS] == [
        HISTORY_START,
        utc(2025, 2, 7),
        utc(2025, 5, 28),
        utc(2025, 9, 14),
    ]
    (validation,) = segments(VALIDATION)
    assert (validation.window.start, validation.window.end) == (utc(2026, 2, 1), TEST_START)
    assert validation.warmup_from == utc(2026, 1, 2)


def test_the_test_partition_is_sealed() -> None:
    with pytest.raises(SealedPartition):
        segments(TEST)
    with pytest.raises(SealedPartition):
        segment("una ventana", utc(2026, 6, 1), TEST_START + timedelta(minutes=1))
    assert segment("hasta la prueba", utc(2026, 6, 1), TEST_START).window.end == TEST_START


def test_f3e5_04_the_additional_sample_shares_no_minute_with_the_partitions() -> None:
    """Two instruments, every 10 minutes from 17 to 21 November 2026: what is counted as
    the additional sample shares no instrument and minute with the partitions."""
    instruments = [("SPOT", "BTC-USDT"), ("FUTURES", "XBTUSDTM")]
    minutes = [utc(2026, 11, 17) + i * timedelta(minutes=10) for i in range(4 * 144)]
    observations = {(market, symbol, m) for market, symbol in instruments for m in minutes}
    additional = {o for o in observations if partition_of(o[2]) == ADDITIONAL}
    partitioned = {o for o in observations if partition_of(o[2]) != ADDITIONAL}
    assert additional and partitioned
    assert not additional & partitioned
    assert min(o[2] for o in additional) == HISTORY_END == utc(2026, 11, 19, 22, 10)
    # What the system recorded from 17 to 19 November is the test, sealed with it.
    assert partition_of(HISTORY_END - timedelta(minutes=1)) == TEST
    assert partition_of(utc(2026, 11, 17, 10, 56)) == TEST
    with pytest.raises(ValueError, match="before the history"):
        partition_of(HISTORY_START - timedelta(minutes=1))


def test_f3e5_03_the_seed_of_an_ema_50_after_the_130_updates_of_30_days() -> None:
    """30 days of 4 h candles are 180; 50 make the seed of the EMA(50), and 130 updates
    leave (49/51)^130 of a difference in the seed."""
    candles = WARMUP // timedelta(hours=4)
    assert (candles, candles - 50) == (180, 130)
    last = []
    for seed in (100.0, 101.0):
        closes = [seed] * 50 + [100.0] * 130
        points = [
            from_minute(
                minute("FUTURES", "X", utc(2026, 1, 5) + i * timedelta(minutes=1), c, c, c, c, 1.0)
            )
            for i, c in enumerate(closes)
        ]
        values = compute(EMA(50), points, timedelta(minutes=1))
        assert len(values) == 131  # the seed and its 130 updates
        last.append(values[-1].outputs[0])
    difference = last[1] - last[0]
    assert difference == pytest.approx((49 / 51) ** 130, rel=1e-9)
    assert round(difference, 8) == 0.00551274
