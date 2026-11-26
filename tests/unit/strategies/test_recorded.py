"""What the recorder writes as the fichas read it (``02`` §12.6.4): the conversion of each
row, the multiplier of each minute (F3E5B-04 and its residual) and the universe at each
instant (F3E5B-01)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from kbot.history.timeframes import Quality, aggregate
from kbot.indicators.price import EMA
from kbot.indicators.volume import VWAP, RelativeVolume
from kbot.market.last_candles import CandleEvidence
from kbot.strategies.frames import build_frames
from kbot.strategies.recorded import (
    CandleRow,
    Evaluation,
    Observation,
    Universe,
    minute_from_row,
    minutes_from_rows,
    multiplier_at,
)

T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
ONE = Decimal("100")


def row(
    at: datetime,
    state: str = "cerrada_rest",
    *,
    lag: timedelta = timedelta(seconds=15),
    volume: str = "1",
) -> CandleRow:
    v = Decimal(volume)
    return CandleRow(at, ONE, ONE + 1, ONE - 1, ONE, v, v * ONE, state, at + MINUTE + lag)


@pytest.mark.parametrize(
    ("state", "evidence", "qualities"),
    [
        (
            "cerrada_cruzada",
            CandleEvidence.CRUZADA_CONSISTENTE,
            (Quality.CONTRASTADA, Quality.SOLO_REST, Quality.CONTRASTADA),
        ),
        (
            "discrepante",
            CandleEvidence.CRUZADA_DISCREPANTE,
            (Quality.DISCREPANTE, Quality.SOLO_REST, Quality.CONTRASTADA),
        ),
        (
            "cerrada_rest",
            CandleEvidence.REST_SUFICIENTE,
            (Quality.SOLO_REST, Quality.SOLO_REST, Quality.SOLO_REST),
        ),
    ],
)
def test_each_state_becomes_a_minute_of_the_contract(
    state: str, evidence: CandleEvidence, qualities: tuple[Quality, Quality, Quality]
) -> None:
    minute = minute_from_row("FUTURES", "XUSDTM", row(T0, state), Decimal("0.01"))
    assert minute.evidence is evidence
    assert (minute.price, minute.volume, minute.time) == qualities
    assert minute.multiplier == 0.01


def test_a_minute_is_available_when_written_and_never_before_its_end_and_margin() -> None:
    early = minute_from_row("SPOT", "BTC-USDT", row(T0, lag=timedelta(seconds=5)), Decimal(1))
    assert early.available_at == T0 + MINUTE + timedelta(seconds=15)
    late = minute_from_row("SPOT", "BTC-USDT", row(T0, lag=timedelta(minutes=5)), Decimal(1))
    assert late.available_at == T0 + MINUTE + timedelta(minutes=5)


def test_an_unknown_state_stops_the_cycle() -> None:
    with pytest.raises(ValueError, match="state"):
        minute_from_row("SPOT", "BTC-USDT", row(T0, "abierta"), Decimal(1))


def test_the_multiplier_of_each_minute_from_the_visible_observations() -> None:
    a, b, c = T0, T0 + timedelta(hours=1), T0 + timedelta(hours=3)
    same = [Observation(a, Decimal("0.01")), Observation(b, Decimal("0.01"))]
    changed = [*same, Observation(c, Decimal("0.001"))]
    assert multiplier_at(changed, a - MINUTE) is None  # before the first one
    assert multiplier_at(changed, a) == Decimal("0.01")
    assert multiplier_at(changed, b - MINUTE) == Decimal("0.01")  # confirmed by b
    # From b, the last one with 0.01, up to c, which detects the change: unknown.
    assert multiplier_at(changed, b) is None
    assert multiplier_at(changed, c - MINUTE) is None
    assert multiplier_at(changed, c) == Decimal("0.001")
    # Before c is visible, the minutes after b carry 0.01: the declared limit.
    assert multiplier_at(same, c - MINUTE) == Decimal("0.01")


def test_an_unknown_multiplier_leaves_only_the_volume_not_valid() -> None:
    minute = minute_from_row("FUTURES", "XUSDTM", row(T0, "cerrada_cruzada"), None)
    assert (minute.price, minute.volume, minute.time) == (
        Quality.CONTRASTADA,
        Quality.DISCREPANTE,
        Quality.CONTRASTADA,
    )
    assert minute.multiplier is None


def test_an_aggregate_carries_the_multiplier_of_its_minutes() -> None:
    observations = [
        Observation(T0, Decimal("0.01")),
        Observation(T0 + 7 * MINUTE, Decimal("0.001")),
    ]
    rows = [row(T0 + i * MINUTE) for i in range(20)]
    minutes = minutes_from_rows("FUTURES", "XUSDTM", rows, observations)
    bars = aggregate(minutes, "5m", downloaded_until=T0 + 20 * MINUTE)
    assert [b.multiplier for b in bars] == [None, None, 0.001, 0.001]
    assert [b.volume_quality for b in bars] == [
        Quality.DISCREPANTE,  # minutes 0-4: unknown, from T0 up to the detection
        Quality.DISCREPANTE,  # 5-6 unknown, 7-9 known: the worst one
        Quality.SOLO_REST,
        Quality.SOLO_REST,
    ]


def test_f3e5b_04_the_uncertain_interval_resets_the_volume_and_keeps_the_price() -> None:
    """The reviewer's case: a check on day 1 at 23:59 with no change, not stored; the
    change on day 2 at 00:00; failed listings that delay the detection to day 3 at 12:00,
    more than 24 h later. 20 minutes of volume 100 at 0.01, then 1,000 at 0.001."""
    day1 = datetime(2026, 1, 5, tzinfo=UTC)
    first = day1 + timedelta(hours=22)  # the last observation stored, by another change
    change = day1 + timedelta(days=1)
    detected = day1 + timedelta(days=2, hours=12)
    stored = [
        Observation(day1 - timedelta(hours=1), Decimal("0.01")),
        Observation(first, Decimal("0.01")),
    ]
    with_detection = [*stored, Observation(detected, Decimal("0.001"))]
    # Minutes in a row from 21:30 of day 1 to 00:30 of day 2: 100 before the change and
    # 1,000 after it; and 30 minutes of 1,000 from the detection.
    start = first - 30 * MINUTE
    rows = [
        row(start + i * MINUTE, volume="100" if start + i * MINUTE < change else "1000")
        for i in range(180)
    ]
    rows += [row(detected + i * MINUTE, volume="1000") for i in range(30)]
    indicators = {"1m": {"rvol": RelativeVolume(20), "vwap": VWAP(), "ema": EMA(5)}}

    def frame(observations: list[Observation]):  # type: ignore[no-untyped-def]
        minutes = minutes_from_rows("FUTURES", "XUSDTM", rows, observations)
        return build_frames(
            minutes, downloaded_until=detected + 30 * MINUTE, timeframes=(), indicators=indicators
        )["1m"]

    known = frame(with_detection)
    rvol, ema = known.values["rvol"], known.values["ema"]
    uncertain = [r.open_time for r in rows if first <= r.open_time < detected]
    # The whole uncertain interval has V not valid: no volume value in it.
    assert not any(t in rvol for t in uncertain)
    assert all(known.points[t].volume_quality is Quality.DISCREPANTE for t in uncertain)
    # Before it, the volume indicators had values; after the detection, only after warming.
    assert any(t < first for t in rvol)
    after = sorted(t for t in rvol if t >= detected)
    assert after and after[0] == detected + 20 * MINUTE
    assert all(v.outputs[0] == pytest.approx(1.0) for t, v in rvol.items() if t >= detected)
    # The price is not touched: the EMA has a value in every minute of the interval.
    assert all(t in ema for t in uncertain)
    # A cut before the detection carries the old multiplier: the artificial 10 appears.
    before = frame(stored)
    assert before.values["rvol"][change + 0 * MINUTE].outputs[0] == pytest.approx(10.0)


def test_f3e5b_01_who_counts_at_each_instant() -> None:
    day = datetime(2026, 1, 5, tzinfo=UTC)
    x, y = ("FUTURES", "XUSDTM"), ("SPOT", "Y-USDT")
    universe = Universe(
        [
            Evaluation(day, *x, True),
            Evaluation(day, *y, True),
            Evaluation(day + timedelta(minutes=30), *x, False),
            Evaluation(day + timedelta(minutes=30), *y, True),
        ]
    )
    assert universe.at(day + timedelta(minutes=15, seconds=15)) == {x, y}
    assert universe.at(day + timedelta(minutes=30)) == {y}
    assert universe.at(day - timedelta(seconds=1)) == frozenset()
    assert universe.during(day.date()) == {x, y}
    assert universe.during((day + timedelta(days=1)).date()) == {y}


def test_f3e5b_05_a_minute_partly_uncertain_has_an_unknown_multiplier() -> None:
    """The reviewer's case, with seconds: 0.01 at 00:00 and at 11:59:30, and 0.001 at
    14:00:20. The uncertainty starts at 11:59:30, so the minute [11:59, 12:00) is unknown,
    and the detection at 14:00:20 leaves [14:00, 14:01) unknown too."""
    day = datetime(2026, 1, 5, tzinfo=UTC)
    stored = [
        Observation(day, Decimal("0.01")),
        Observation(day + timedelta(hours=11, minutes=59, seconds=30), Decimal("0.01")),
        Observation(day + timedelta(hours=14, seconds=20), Decimal("0.001")),
    ]

    def at(hours: int, minutes: int) -> datetime:
        return day + timedelta(hours=hours, minutes=minutes)

    assert multiplier_at(stored, at(11, 58)) == Decimal("0.01")
    assert multiplier_at(stored, at(11, 59)) is None
    assert multiplier_at(stored, at(13, 59)) is None
    assert multiplier_at(stored, at(14, 0)) is None
    assert multiplier_at(stored, at(14, 1)) == Decimal("0.001")
    # A first observation inside a minute leaves that minute unknown.
    assert multiplier_at([Observation(day + timedelta(seconds=30), Decimal("0.01"))], day) is None
    # The minute and its aggregates lose V and reset the volume; P and T stay.
    rows = [row(at(11, 0) + i * MINUTE) for i in range(60)]
    indicators = {
        "1m": {"rvol": RelativeVolume(5), "ema": EMA(5)},
        "15m": {"vwap": VWAP(), "ema": EMA(2)},
    }
    minutes = minutes_from_rows("FUTURES", "XUSDTM", rows, stored)
    frames = build_frames(
        minutes, downloaded_until=at(12, 0), timeframes=("15m",), indicators=indicators
    )
    one, quarter = frames["1m"], frames["15m"]
    assert one.points[at(11, 59)].volume_quality is Quality.DISCREPANTE
    assert one.points[at(11, 58)].volume_quality is Quality.SOLO_REST
    assert at(11, 59) not in one.values["rvol"] and at(11, 58) in one.values["rvol"]
    assert at(11, 59) in one.values["ema"]  # the price is not touched
    assert quarter.points[at(11, 45)].volume_quality is Quality.DISCREPANTE
    assert quarter.points[at(11, 30)].volume_quality is Quality.SOLO_REST
    assert at(11, 45) not in quarter.values["vwap"]
    assert at(11, 45) in quarter.values["ema"]
    assert (quarter.points[at(11, 45)].price, quarter.points[at(11, 45)].time) == (
        Quality.SOLO_REST,
        Quality.SOLO_REST,
    )


@pytest.mark.parametrize(
    "observations",
    [[], [Observation(T0 + timedelta(seconds=30), Decimal(1))]],
    ids=["sin observaciones", "la primera dentro del minuto"],
)
def test_f3e5b_06_spot_always_has_the_multiplier_1(observations: list[Observation]) -> None:
    (minute,) = minutes_from_rows("SPOT", "BTC-USDT", [row(T0)], observations)
    assert minute.multiplier == 1.0
    assert minute.volume is Quality.SOLO_REST  # that of its state, cerrada_rest
