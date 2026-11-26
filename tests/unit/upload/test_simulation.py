"""The simulation of the budget of the bucket (02 §13.1, point 4, E4c-2, point 6)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tools import simular_copias as sim


def test_a_small_case_counted_by_hand() -> None:
    """Two days of one hot copy a day (every 24 h), nothing sealed, no previa, no growth:
    the first day holds one copy; on the second a new one comes, the old one is no longer
    among the hourly ones but is the first of its day, so it stays."""
    p = sim.Parameters(
        days=2,
        start=date(2026, 12, 7),  # a Monday
        first_day_with_data=date(2026, 12, 7),
        day_mb=24.0,
        small_mb=1.0,
        hot_manifest_kb=0,
        hot_upload_every_hours=24,
        hourly_keep=1,
        previa_every_days=0,
    )
    result = sim.simulate(p)
    first = int(1e6) + int(24e6 * 40 / 60 / 24)  # the small tables and 40 minutes of the day
    assert result.daily_peak[date(2026, 12, 7)] == first
    # On the second day the first copy stays (the first of its day and of its week) and the
    # new one carries the whole 7 Dec, not sealed yet, and 40 minutes of the 8th.
    second = int(1e6) + int(24e6) + int(24e6 * 40 / 60 / 24)
    assert result.daily_peak[date(2026, 12, 8)] == first + second
    assert result.first_refusal is None
    # Each cycle: the versioning and one page of the listing; and one upload of 4 requests.
    assert result.requests_per_day == {date(2026, 12, 7): 24 * 2 + 4, date(2026, 12, 8): 24 * 2 + 4}


def test_a_deleted_copy_counts_until_the_lifecycle_rule_removes_it() -> None:
    p = sim.Parameters(
        days=6,
        start=date(2026, 12, 7),
        first_day_with_data=date(2026, 12, 7),
        day_mb=24.0,
        small_mb=0.0,
        hot_manifest_kb=0,
        hot_upload_every_hours=24,
        hourly_keep=1,
        daily_keep=1,
        weekly_keep=0,
        previa_every_days=0,
        lifecycle_delay_days=2.0,
    )
    result = sim.simulate(p)
    # From the second day one copy is deleted a day and stays 1 + 2 days as a version.
    assert result.daily_peak[date(2026, 12, 12)] > 3 * result.daily_peak[date(2026, 12, 7)]


def test_the_measured_case_runs_120_days_and_reports_the_cap_of_requests() -> None:
    result = sim.simulate(sim.Parameters())
    assert len(result.daily_peak) == 120
    assert 0 < result.max_requests_window() < sim.REQUEST_CAP
    text = sim.summary(result)
    assert "Máximo del cubo antes de una subida" in text and "peticiones en 32 días" in text


def test_the_simulation_uses_the_retention_of_the_service() -> None:
    first = datetime(2026, 11, 30, 0, 40, tzinfo=UTC)
    names = [(f"caliente-{first:%Y%m%dT%H%M}", first)]
    assert sim.keep_hot(names, sim.Parameters()) == {names[0][0]}


def test_an_eligible_day_still_in_postgresql_counts_in_the_previa() -> None:
    """E4C2-05: the previa of the code carries every day still in PostgreSQL; a day leaves
    it only when eligible and older than 90 days."""
    p = sim.Parameters(eligible_after_days=14)
    moment = datetime(2027, 3, 1, 12, 40, tzinfo=UTC)
    assert sim._eligible(p, date(2027, 1, 30), moment)
    assert sim._present(p, date(2027, 1, 30), moment)  # eligible, 30 days old: still there
    assert not sim._present(p, date(2026, 11, 30), moment)  # eligible and 91 days old
    assert sim._present(sim.Parameters(), date(2026, 11, 17), moment)  # never eligible
    with_all = sim.simulate(sim.Parameters(days=60, eligible_after_days=14))
    without = sim.simulate(
        sim.Parameters(days=60, eligible_after_days=14, previa_excludes_eligible=True)
    )
    assert with_all.peak > without.peak


@pytest.mark.parametrize(
    ("arguments", "peak_gb", "refused_on_day"),
    [
        (["--subida-caliente-cada", "1"], 19.00, 4),
        ([], 19.00, 50),
        (["--elegible-tras", "14"], 18.48, 64),
        (["--elegible-tras", "14", "--previa-sin-elegibles"], 10.29, None),
        (
            [
                "--elegible-tras",
                "14",
                "--previa-sin-elegibles",
                "--dias",
                "180",
                "--crecimiento",
                "0.1",
            ],
            17.55,
            None,
        ),
    ],
)
def test_the_scenarios_published_in_02_come_from_this_model(
    arguments: list[str],
    peak_gb: float,
    refused_on_day: int | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """E4C2-05: the table of 02 §13.1 («Implementado en E4c-2») is what this model gives."""
    assert sim.main(arguments) == 0
    text = capsys.readouterr().out
    assert f"Máximo del cubo antes de una subida: {peak_gb:.2f} GB" in text
    if refused_on_day is None:
        assert "Todo cabe en los 19 GB" in text
    else:
        assert f"(día {refused_on_day});" in text
