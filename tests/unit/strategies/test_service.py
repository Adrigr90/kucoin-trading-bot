"""``svc-strategy`` without a database (``02`` §12.6.4 and §12.6.5): the equivalence with the
engine in each cut, the cycles and the recovery, with the recorder in memory."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from kbot.core.jcs import canonicalize
from kbot.strategies.breakout import Breakout
from kbot.strategies.ficha import Ficha
from kbot.strategies.mean_reversion import MeanReversion
from kbot.strategies.service import Service, cut_of, next_cycle, recovery_days
from kbot.strategies.trend import Trend
from tests.unit.strategies.recorder import (
    COMMIT,
    Recorder,
    engine_rows,
    rows_of,
    written_rows,
)
from tests.unit.strategies.support import MINUTES, START, short_fichas, walk_minutes

DAY = timedelta(days=1)
ETH = ("FUTURES", "ETHUSDTM")
BTC = ("SPOT", "BTC-USDT")
D1, D2, D3 = START + DAY, START + 2 * DAY, START + 3 * DAY  # 6, 7 and 8 January 2026


def recorder_with(
    fichas: list[Ficha], instruments: tuple[tuple[str, str], ...] = (ETH, BTC)
) -> Recorder:
    recorder = Recorder()
    if ETH in instruments:
        recorder.add(*ETH, rows_of(MINUTES))
        recorder.observe(*ETH, START - DAY, "0.01")
    if BTC in instruments:
        spot = walk_minutes(4 * 1440, seed=5, market="SPOT", symbol="BTC-USDT", price=200.0)
        recorder.add(*BTC, rows_of(spot))
        recorder.observe(*BTC, START - DAY, "1")
    recorder.evaluate(START - DAY, instruments)
    recorder.approved = {(f.family, f.params_hash()) for f in fichas}
    return recorder


def at(day: datetime, hours: int, minutes: int = 0, seconds: int = 0) -> datetime:
    return day + timedelta(hours=hours, minutes=minutes, seconds=seconds)


def expected(
    recorder: Recorder,
    fichas: list[Ficha],
    day: datetime,
    corte: datetime,
    instruments: tuple[tuple[str, str], ...] = (ETH, BTC),
) -> set[object]:
    return set().union(
        *(engine_rows(recorder, f, m, s, day, corte) for f in fichas for m, s in instruments)
    )


def test_a_cycle_after_the_day_writes_exactly_the_engine_signals() -> None:
    fichas = short_fichas()
    recorder = recorder_with(fichas)
    service = Service(fichas, COMMIT)
    done = service.cycle(recorder.open_view, recorder.write, at(D3, 0, 31, 30))
    assert done.corte == at(D3, 0, 30, 30)
    for day in (D1, D2):
        wanted = expected(recorder, fichas, day, done.corte)
        assert wanted, "the synthetic minutes must give signals"
        assert written_rows(recorder, day=day) == wanted
    rows = list(recorder.written.values())
    assert all(r["code_commit"] == COMMIT and r["signal_at"] <= done.corte for r in rows)
    assert all(r["market"] == "FUTURES" or r["direction"] == 1 for r in rows)
    assert all(0 <= r["rest_only_minutes"] <= r["provenance_minutes"] for r in rows)


def test_cycles_during_the_day_give_the_engine_signals_up_to_their_cut() -> None:
    fichas = short_fichas()[:1]
    recorder = recorder_with(fichas, (ETH,))
    service = Service(fichas, COMMIT)
    for hour in (6, 12, 18):
        done = service.cycle(recorder.open_view, recorder.write, at(D2, hour, 1, 30))
        assert written_rows(recorder, day=D2) == expected(recorder, fichas, D2, done.corte, (ETH,))
    # Without late rows, the later cycles complete the day.
    done = service.cycle(recorder.open_view, recorder.write, at(D3, 0, 1, 30))
    assert written_rows(recorder, day=D2) == expected(recorder, fichas, D2, done.corte, (ETH,))


def test_repeating_a_cycle_computes_nothing_settled_and_adds_nothing() -> None:
    fichas = short_fichas()[:1]
    recorder = recorder_with(fichas, (ETH,))
    service = Service(fichas, COMMIT)
    service.cycle(recorder.open_view, recorder.write, at(D3, 0, 31, 30))
    before = dict(recorder.written)
    again = service.cycle(recorder.open_view, recorder.write, at(D3, 0, 46, 30))
    assert recorder.written == before
    # Only the day of the cut, not ended, is computed again; the settled days are not.
    assert again.computed == 1
    # A new process computes them all again, and writes nothing new either.
    Service(fichas, COMMIT).cycle(recorder.open_view, recorder.write, at(D3, 0, 46, 30))
    assert recorder.written == before


def test_without_an_approved_version_there_are_no_signals() -> None:
    fichas = short_fichas()
    recorder = recorder_with(fichas)
    recorder.approved = {(fichas[0].family, "0" * 64)}  # another version of the trend
    done = Service(fichas, COMMIT).cycle(recorder.open_view, recorder.write, at(D3, 0, 31, 30))
    assert (done.computed, recorder.written) == (0, {})


def test_f3e5b_01_each_signal_counts_with_the_universe_of_its_instant() -> None:
    """ETHUSDTM leaves the universe at 12:00 of the day: its signals before stay, those
    after are not written, the same in every cycle, in the recovery and in a new process."""
    fichas = short_fichas()
    recorder = recorder_with(fichas)
    recorder.evaluate(at(D2, 12), [BTC], out=[ETH])
    incremental = Service(fichas, COMMIT)
    for hour in (6, 12, 18):
        incremental.cycle(recorder.open_view, recorder.write, at(D2, hour, 1, 30))
    incremental.cycle(recorder.open_view, recorder.write, at(D3, 0, 31, 30))
    by_cycles = written_rows(recorder, day=D2)
    eth = [
        r
        for r in recorder.written.values()
        if r["symbol"] == "ETHUSDTM" and D2 <= r["signal_at"] < D3
    ]
    assert eth and all(r["signal_at"] < at(D2, 12) for r in eth)
    # The ones written are exactly the engine's that counted at their D_s: ETHUSDTM only
    # before 12:00, BTC-USDT all day.
    corte = at(D3, 0, 30, 30)
    assert by_cycles == expected(recorder, fichas, D2, corte)
    without = recorder_with(fichas)  # the same minutes, with no change of the universe
    eth_all = expected(without, fichas, D2, corte, (ETH,))
    assert any(dict(r)["signal_at"] >= at(D2, 12) for r in eth_all), "the case needs it"
    # The recovery of a new process, in one cycle, gives the same.
    fresh = recorder_with(fichas)
    fresh.evaluate(at(D2, 12), [BTC], out=[ETH])
    Service(fichas, COMMIT).cycle(fresh.open_view, fresh.write, at(D3, 0, 31, 30))
    assert written_rows(fresh, day=D2) == by_cycles


def test_f3e5b_02_each_cut_is_the_engine_on_its_visible_minutes_and_written_stays() -> None:
    """The reviewer's case: a minute of day 1 at 01:10 becomes visible on day 2 at 12:00.
    The cut of 11:00:30 gives exactly the engine on what it saw; the later cut, the engine
    on what it saw; what was written stays, whatever the recomputation with all the data."""
    fichas = short_fichas()[:1]
    recorder = recorder_with(fichas, (ETH,))
    late = at(D1, 1, 10)
    rows = recorder.candles[ETH]
    index = next(i for i, r in enumerate(rows) if r.open_time == late)
    rows[index] = replace(rows[index], recorded_at=at(D2, 12))
    service = Service(fichas, COMMIT)
    first = service.cycle(recorder.open_view, recorder.write, at(D2, 11, 1, 30))
    written_first = written_rows(recorder)
    assert written_first == set().union(
        *(expected(recorder, fichas, day, first.corte, (ETH,)) for day in (START, D1, D2))
    )
    second = service.cycle(recorder.open_view, recorder.write, at(D2, 13, 1, 30))
    new = written_rows(recorder) - written_first
    assert new <= set().union(
        *(expected(recorder, fichas, day, second.corte, (ETH,)) for day in (START, D1, D2))
    )
    assert written_first <= written_rows(recorder)  # nothing written is ever removed
    # The day of the late minute was computed again: what it uses changed.
    key = (fichas[0].family, *ETH, D1.date())
    assert service.done[key].corte == second.corte


def test_f3e5b_03_failed_cycles_are_recovered_without_a_restart() -> None:
    fichas = short_fichas()[:1]
    recorder = recorder_with(fichas, (ETH,))
    service = Service(fichas, COMMIT)
    service.cycle(recorder.open_view, recorder.write, at(D2, 23, 31, 30))
    recorder.failing = True
    for moment in (at(D2, 23, 46, 30), *(at(D3, 0, m, 30) for m in (1, 16, 31, 46))):
        with pytest.raises(RuntimeError):
            service.cycle(recorder.open_view, recorder.write, moment)
    recorder.failing = False
    done = service.cycle(recorder.open_view, recorder.write, at(D3, 1, 1, 30))
    assert written_rows(recorder, day=D2) == expected(recorder, fichas, D2, done.corte, (ETH,))
    # A row confirmed late, with recorded_at before a cut already used, makes its day be
    # computed again: the number of visible rows changed.
    key = (fichas[0].family, *ETH, D2.date())
    held = service.done[key]
    missing = at(D2, 5, 7)
    rows = recorder.candles[ETH]
    gone = next(r for r in rows if r.open_time == missing)
    rows.remove(gone)  # it was not visible
    service.done[key] = replace(held, counts=(held.counts[0] - 1, *held.counts[1:]))
    rows.append(gone)  # now it is, with its old recorded_at
    service.cycle(recorder.open_view, recorder.write, at(D3, 1, 16, 30))
    assert service.done[key].counts == held.counts
    assert service.done[key].corte == at(D3, 1, 15, 30)


def test_the_cycles_their_cut_and_their_window() -> None:
    assert next_cycle(datetime(2026, 1, 7, 10, 0, tzinfo=UTC)) == datetime(
        2026, 1, 7, 10, 1, 30, tzinfo=UTC
    )
    assert next_cycle(datetime(2026, 1, 7, 10, 1, 30, tzinfo=UTC)) == datetime(
        2026, 1, 7, 10, 16, 30, tzinfo=UTC
    )
    assert next_cycle(datetime(2026, 1, 7, 23, 50, tzinfo=UTC)) == datetime(
        2026, 1, 8, 0, 1, 30, tzinfo=UTC
    )
    assert cut_of(datetime(2026, 1, 7, 0, 1, 30, tzinfo=UTC)) == datetime(
        2026, 1, 7, 0, 0, 30, tzinfo=UTC
    )
    corte = datetime(2026, 1, 7, 0, 0, 30, tzinfo=UTC)
    assert [d.day for d in recovery_days(corte)] == [5, 6, 7]
    corte = datetime(2026, 1, 7, 0, 0, 0, tzinfo=UTC)
    assert [d.day for d in recovery_days(corte)] == [5, 6, 7]


@pytest.mark.parametrize(
    ("ficha", "approved"),
    [
        (Trend(), "8866b51449f119568d5012acb47487d37d411b66fda8d615661564b144fa36d3"),
        (Breakout(), "eac620fee86f"),
        (MeanReversion(), "adee5c8a2a90"),
    ],
)
def test_the_params_text_of_the_approved_fichas_gives_their_hash(
    ficha: Ficha, approved: str
) -> None:
    """The text of ``config.strategy_versions.params`` (02 §12.6.2): its SHA-256 is the
    approved hash of the ficha."""
    text = canonicalize(ficha.params.as_json()).decode("utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert digest == ficha.params_hash() and digest.startswith(approved)
