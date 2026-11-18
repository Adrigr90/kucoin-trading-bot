"""Window, eligibility, presence and coverage: the cases of the data contract (02 §12.1,
point 8) that this part answers for (F3-A1): CD-11 and CD-13 to CD-16."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kbot.history.coverage import (
    Eligibility,
    EligibilitySpan,
    Presence,
    Request,
    Window,
    coverage,
    eligibility,
    presence,
)

NOON = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return NOON + timedelta(minutes=minutes)


def test_a_failed_stretch_is_not_downloaded_and_counts_as_absent() -> None:
    """CD-11: of three requests, the middle one failed."""
    window = Window(at(0), at(9))
    requests = [
        Request(at(0), at(2), succeeded=True),
        Request(at(3), at(5), succeeded=False),
        Request(at(6), at(8), succeeded=True),
    ]
    states = presence(window, requests, [at(m) for m in (0, 1, 2, 6, 7, 8)])
    assert [states[at(m)] for m in range(9)] == [Presence.PRESENTE] * 3 + [
        Presence.NO_DESCARGADO
    ] * 3 + [Presence.PRESENTE] * 3
    report = coverage(states, [])
    assert report.failed == [(at(3), at(6))]
    assert (report.numerator, report.denominator) == (6, 9), "they count as absent"


def test_the_ends_of_the_window_are_never_trimmed() -> None:
    """CD-13: [12:00, 12:05) of an instrument trading; the answer brings only 12:01,
    12:02 and 12:03. 12:00 and 12:04 are absent, of unknown eligibility (or eligible with
    evidence): coverage 3/5, never 3/3."""
    window = Window(at(0), at(5))
    states = presence(window, [Request(at(0), at(4), True)], [at(1), at(2), at(3)])
    assert states[at(0)] is Presence.AUSENTE and states[at(4)] is Presence.AUSENTE
    unknown = coverage(states, [])
    assert unknown.counts[(Presence.AUSENTE, Eligibility.DESCONOCIDA)] == 2
    assert (unknown.numerator, unknown.denominator, unknown.ratio) == (3, 5, 0.6)
    listed = EligibilitySpan(at(0), at(5), eligible=True, source="instruments (recorder)")
    known = coverage(states, [listed])
    assert known.counts[(Presence.AUSENTE, Eligibility.ELEGIBLE)] == 2
    assert known.ratio == 0.6


def test_an_empty_correct_answer_makes_every_minute_absent() -> None:
    """CD-14: coverage 0, not "not applicable"."""
    window = Window(at(0), at(5))
    states = presence(window, [Request(at(0), at(4), True)], [])
    assert set(states.values()) == {Presence.AUSENTE}
    assert coverage(states, []).ratio == 0.0


def test_failed_requests_at_both_ends_leave_the_window_whole() -> None:
    """CD-15: the requests of both ends fail; their minutes are not downloaded and the
    window keeps its length."""
    window = Window(at(0), at(9))
    requests = [
        Request(at(0), at(2), False),
        Request(at(3), at(5), True),
        Request(at(6), at(8), False),
    ]
    states = presence(window, requests, [at(4)])
    assert len(states) == 9
    report = coverage(states, [])
    assert report.failed == [(at(0), at(3)), (at(6), at(9))]
    assert report.total(Presence.NO_DESCARGADO) == 6
    assert (report.numerator, report.denominator) == (1, 9)


def test_only_independent_evidence_makes_a_minute_not_eligible() -> None:
    """CD-16: a Futures contract opened at 12:02 (firstOpenDate) and a pair withdrawn at
    12:07 by an announcement. Only the minutes outside those dates are not eligible; a
    candle in one of them is a contradiction, reported apart and counted neither above nor
    below: with four eligible minutes present and that candle, coverage 4/4, never 5/4."""
    opened = EligibilitySpan(at(0), at(2), eligible=False, source="firstOpenDate")
    trading = EligibilitySpan(at(2), at(6), eligible=True, source="firstOpenDate")
    assert eligibility(at(1), [opened, trading]) is Eligibility.NO_ELEGIBLE
    assert eligibility(at(2), [opened, trading]) is Eligibility.ELEGIBLE
    assert eligibility(at(7), [opened, trading]) is Eligibility.DESCONOCIDA

    window = Window(at(0), at(6))
    states = presence(window, [Request(at(0), at(5), True)], [at(1), at(2), at(3), at(4), at(5)])
    report = coverage(states, [opened, trading])
    assert report.contradictions == [at(1)]
    assert states[at(1)] is Presence.PRESENTE, "eligibility never changes presence"
    assert (report.numerator, report.denominator, report.ratio) == (4, 4, 1.0)

    withdrawn = EligibilitySpan(at(0), at(10), eligible=False, source="anuncio https://...")
    whole = coverage(presence(Window(at(0), at(3)), [Request(at(0), at(2), True)], []), [withdrawn])
    assert whole.denominator == 0
    assert whole.ratio is None, "a window entirely not eligible: not applicable"


def test_a_minute_covered_by_a_failed_and_a_successful_request_was_downloaded() -> None:
    """A retry that succeeds settles it: the minute is absent, not undownloaded."""
    window = Window(at(0), at(3))
    requests = [Request(at(0), at(2), False), Request(at(0), at(2), True)]
    assert set(presence(window, requests, []).values()) == {Presence.AUSENTE}


def test_a_window_starts_and_ends_on_the_minute() -> None:
    with pytest.raises(ValueError, match="on the minute"):
        Window(at(0) + timedelta(seconds=1), at(5))
    with pytest.raises(ValueError, match="before it starts"):
        Window(at(5), at(0))
    assert len(Window(at(0), at(1440))) == 1440
