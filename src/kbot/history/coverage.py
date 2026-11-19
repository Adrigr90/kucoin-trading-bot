"""Window, eligibility, presence and coverage of the downloaded minutes (``02`` §12.1,
point 2; F3-E1, CD-R01).

Three different things, none deduced from another:

* **The window** asked for an instrument: the minutes requested from KuCoin. It is the
  denominator of the coverage; neither the first nor the last candle obtained trims it.
* **The eligibility** of a minute: ``no_elegible`` only with evidence independent of the
  candles (the opening date of a Futures contract, an official announcement, what the
  recorder observed), ``elegible`` with such evidence, and ``desconocida`` without any. A
  minute of unknown eligibility counts in the denominator.
* **The presence** of each minute: ``presente`` if KuCoin returned its candle; ``ausente``
  if the request that covers it succeeded without it, also when the answer came empty or
  with gaps at its ends; ``en_conflicto`` if that answer brought two different candles for
  it, so that none is used; ``no_descargado`` if that request failed or was never made.

The coverage of a set of minutes is the share of those with eligibility other than
``no_elegible`` (the set E) that are present; with E empty it is not applicable, never 0 or
100 %. A minute in conflict counts in E but is never present. A candle in a ``no_elegible``
minute, one or two different ones, is a **contradiction**: the minute keeps its presence, it
is reported apart and it counts neither above nor below.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from kbot.market.timeutil import MINUTE, as_utc, floor_to


class Presence(StrEnum):
    PRESENTE = "presente"
    AUSENTE = "ausente"
    EN_CONFLICTO = "en_conflicto"
    NO_DESCARGADO = "no_descargado"


# A no_elegible minute with any of these had a candle from KuCoin: a contradiction.
WITH_CANDLES = (Presence.PRESENTE, Presence.EN_CONFLICTO)


class Eligibility(StrEnum):
    ELEGIBLE = "elegible"
    NO_ELEGIBLE = "no_elegible"
    DESCONOCIDA = "desconocida"


@dataclass(frozen=True)
class Window:
    """Minutes ``[start, end)`` asked for one instrument."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for moment in (self.start, self.end):
            if as_utc(moment, "window") != floor_to(moment, MINUTE):
                raise ValueError(f"a window must start and end on the minute: {moment}")
        if self.end < self.start:
            raise ValueError("a window cannot end before it starts")

    def minutes(self) -> Iterator[datetime]:
        moment = self.start
        while moment < self.end:
            yield moment
            moment += MINUTE

    def __len__(self) -> int:
        return (self.end - self.start) // MINUTE


@dataclass(frozen=True)
class Request:
    """One request of the download: the minutes ``[first, last]`` it covers, and whether
    it succeeded (an answer, maybe empty) or failed (an error, nothing known)."""

    first: datetime
    last: datetime
    succeeded: bool

    def covers(self, moment: datetime) -> bool:
        return self.first <= moment <= self.last


@dataclass(frozen=True)
class EligibilitySpan:
    """Evidence independent of the candles that the minutes ``[start, end)`` were, or were
    not, eligible: where it comes from goes with it (an opening date, an announcement with
    its link, the recorder's observation)."""

    start: datetime
    end: datetime
    eligible: bool
    source: str

    def covers(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


def presence(
    window: Window,
    requests: Iterable[Request],
    present: Iterable[datetime],
    in_conflict: Iterable[datetime] = (),
) -> dict[datetime, Presence]:
    """The presence of every minute of the window. A minute covered by a request that
    succeeded is never ``no_descargado``, whatever other requests for it did. The ranges
    of the requests that succeeded are merged and walked once, in order.

    A minute in conflict came in an answer, so a request that succeeded covers it, and it
    has no candle: otherwise its record contradicts itself, and this raises ``ValueError``.
    """
    have = {as_utc(moment, "present") for moment in present}
    clash = {as_utc(moment, "in_conflict") for moment in in_conflict}
    if have & clash:
        raise ValueError(f"present and in conflict at once: {sorted(have & clash)[0]}")
    merged: list[tuple[datetime, datetime]] = []
    for first, last in sorted((r.first, r.last) for r in requests if r.succeeded):
        if merged and first <= merged[-1][1] + MINUTE:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    result = {}
    index = 0
    for moment in window.minutes():
        while index < len(merged) and merged[index][1] < moment:
            index += 1
        answered = index < len(merged) and merged[index][0] <= moment
        if moment in have:
            result[moment] = Presence.PRESENTE
        elif moment in clash:
            if not answered:
                raise ValueError(f"in conflict without an answer that covers it: {moment}")
            result[moment] = Presence.EN_CONFLICTO
        elif answered:
            result[moment] = Presence.AUSENTE
        else:
            result[moment] = Presence.NO_DESCARGADO
    return result


def eligibility(moment: datetime, evidence: Iterable[EligibilitySpan]) -> Eligibility:
    """``no_elegible`` if some evidence says so, else ``elegible`` if some says so, else
    ``desconocida``: without evidence nothing is assumed."""
    covering = [span for span in evidence if span.covers(moment)]
    if any(not span.eligible for span in covering):
        return Eligibility.NO_ELEGIBLE
    if covering:
        return Eligibility.ELEGIBLE
    return Eligibility.DESCONOCIDA


@dataclass
class Coverage:
    """Counts of a set of minutes by presence and eligibility, and their coverage."""

    counts: dict[tuple[Presence, Eligibility], int] = field(default_factory=dict)
    contradictions: list[datetime] = field(default_factory=list)  # no_elegible with a candle
    in_conflict: list[datetime] = field(default_factory=list)  # two different candles
    failed: list[tuple[datetime, datetime]] = field(default_factory=list)  # [start, end)

    @property
    def numerator(self) -> int:
        return sum(
            n
            for (p, e), n in self.counts.items()
            if p is Presence.PRESENTE and e is not Eligibility.NO_ELEGIBLE
        )

    @property
    def denominator(self) -> int:
        return sum(n for (_p, e), n in self.counts.items() if e is not Eligibility.NO_ELEGIBLE)

    @property
    def ratio(self) -> float | None:
        """The coverage, between 0 and 1; None when not applicable (no minute in E)."""
        return self.numerator / self.denominator if self.denominator else None

    def total(self, presence: Presence) -> int:
        return sum(n for (p, _e), n in self.counts.items() if p is presence)


def coverage(states: dict[datetime, Presence], evidence: Iterable[EligibilitySpan]) -> Coverage:
    """The coverage of the minutes in ``states`` (a window, or a day of it)."""
    spans = list(evidence)
    result = Coverage()
    failed_from: datetime | None = None
    previous: datetime | None = None
    for moment in sorted(states):
        state = states[moment]
        elig = eligibility(moment, spans)
        result.counts[(state, elig)] = result.counts.get((state, elig), 0) + 1
        if state in WITH_CANDLES and elig is Eligibility.NO_ELEGIBLE:
            result.contradictions.append(moment)
        if state is Presence.EN_CONFLICTO:
            result.in_conflict.append(moment)
        contiguous = previous is not None and moment - previous == MINUTE
        ends = state is not Presence.NO_DESCARGADO or not contiguous
        if failed_from is not None and previous is not None and ends:
            result.failed.append((failed_from, previous + MINUTE))
            failed_from = None
        if state is Presence.NO_DESCARGADO and failed_from is None:
            failed_from = moment
        previous = moment
    if failed_from is not None and previous is not None:
        result.failed.append((failed_from, previous + MINUTE))
    return result
