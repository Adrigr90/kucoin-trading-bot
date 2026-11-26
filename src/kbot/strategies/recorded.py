"""What the recorder writes, as the fichas read it (``02`` §12.6.4; F3-E5b).

* **Visible in a cut:** a cycle of ``svc-strategy`` fixes its ``corte`` and from then on uses
  only the rows of ``market.candles``, ``market.instruments`` and
  ``market.universe_snapshots`` with ``recorded_at`` at or before it.
* **A row of ``market.candles`` (``last``) is a minute of the contract** (§12.1):

  ============== =========================== ============ ======== ============
  state          evidence                    P            V        T
  ============== =========================== ============ ======== ============
  cerrada_cruzada vela_cruzada_consistente   contrastada  solo REST contrastada
  discrepante    vela_cruzada_discrepante    discrepante  solo REST contrastada
  cerrada_rest   vela_rest_suficiente        solo REST    solo REST solo REST
  ============== =========================== ============ ======== ============

  The volume of the WebSocket does not judge until D26 (§3.8), so V is only REST. A minute
  is available at the latest of its ``recorded_at`` and its end plus the margin. A minute
  without a visible row is an absence.
* **The multiplier** (CD-12; F3E5B-04 and its residual), from the observations of
  ``market.instruments`` visible in the cut, in order: unknown before the first; between
  two in a row with the same multiplier, theirs; between two in a row that change it,
  unknown over the whole interval, from the earlier one to the one that detects the change;
  from the last one on, its own. **A minute has a multiplier only if it is known and the
  same in the whole minute** (F3E5B-05): if any part of it falls in an unknown interval,
  its multiplier is unknown and its V not valid. **Spot always has 1** (F3E5B-06), whatever
  the observations.
* **The universe, at each instant** (F3E5B-01): an instrument counts at ``D_s`` if the last
  visible evaluation with ``evaluated_at`` at or before ``D_s`` has it in the universe.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from kbot.history.timeframes import MinuteCandle, Quality
from kbot.market.last_candles import STABILIZATION, CandleEvidence, Ohlcv
from kbot.market.timeutil import MINUTE, as_utc

DAY = timedelta(days=1)

# state -> evidence, P, V and T (02 §12.6.4).
STATES: dict[str, tuple[CandleEvidence, Quality, Quality, Quality]] = {
    "cerrada_cruzada": (
        CandleEvidence.CRUZADA_CONSISTENTE,
        Quality.CONTRASTADA,
        Quality.SOLO_REST,
        Quality.CONTRASTADA,
    ),
    "discrepante": (
        CandleEvidence.CRUZADA_DISCREPANTE,
        Quality.DISCREPANTE,
        Quality.SOLO_REST,
        Quality.CONTRASTADA,
    ),
    "cerrada_rest": (
        CandleEvidence.REST_SUFICIENTE,
        Quality.SOLO_REST,
        Quality.SOLO_REST,
        Quality.SOLO_REST,
    ),
}


@dataclass(frozen=True)
class CandleRow:
    """A row of ``market.candles`` of the series ``last``."""

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal
    state: str
    recorded_at: datetime


@dataclass(frozen=True)
class Observation:
    """A row of ``market.instruments``: from ``observed_at`` the instrument had this
    multiplier, and it was so up to the next row."""

    observed_at: datetime
    multiplier: Decimal


@dataclass(frozen=True)
class Evaluation:
    """A row of ``market.universe_snapshots``."""

    evaluated_at: datetime
    market: str
    symbol: str
    in_universe: bool


def multiplier_at(observations: Sequence[Observation], minute: datetime) -> Decimal | None:
    """The multiplier of the minute [minute, minute + 1 min); None when it is unknown in
    any part of it (F3E5B-05). ``observations`` are those visible, in order of
    ``observed_at``."""
    start = as_utc(minute, "minute")
    end = start + MINUTE
    times = [o.observed_at for o in observations]
    index = bisect_right(times, start) - 1
    if index < 0:
        return None  # before the first observation of the instrument
    value = observations[index].multiplier
    # Every observation inside the minute must keep it: from each, the multiplier is the
    # one it says, and a change inside would make part of the minute unknown.
    while index + 1 < len(observations) and observations[index + 1].observed_at < end:
        index += 1
        if observations[index].multiplier != value:
            return None
    # The interval from the last observation before the end goes on to the next one: if
    # that one changes it, the change happened somewhere in between, this minute included.
    following = observations[index + 1] if index + 1 < len(observations) else None
    if following is not None and following.multiplier != value:
        return None
    return value


def minute_from_row(
    market: str, symbol: str, row: CandleRow, multiplier: Decimal | None
) -> MinuteCandle:
    """The minute of the contract of a row of the recorder."""
    try:
        evidence, price, volume, time = STATES[row.state]
    except KeyError:
        raise ValueError(f"{market} {symbol} {row.open_time}: state {row.state!r}") from None
    if multiplier is None:
        volume = Quality.DISCREPANTE  # the unit of its volume is unknown: V is not valid
    opened = as_utc(row.open_time, "open_time")
    return MinuteCandle(
        market,
        symbol,
        Ohlcv(opened, row.open, row.high, row.low, row.close, row.volume, row.turnover),
        evidence,
        price,
        volume,
        time,
        max(as_utc(row.recorded_at, "recorded_at"), opened + MINUTE + STABILIZATION),
        float(multiplier) if multiplier is not None else None,
    )


def minutes_from_rows(
    market: str, symbol: str, rows: Iterable[CandleRow], observations: Sequence[Observation]
) -> list[MinuteCandle]:
    if market == "SPOT":  # always 1 (§3.8), whatever the observations (F3E5B-06)
        return [minute_from_row(market, symbol, row, Decimal(1)) for row in rows]
    ordered = sorted(observations, key=lambda o: o.observed_at)
    return [
        minute_from_row(market, symbol, row, multiplier_at(ordered, row.open_time)) for row in rows
    ]


class Universe:
    """The visible evaluations of the universe, to ask who counts at each instant."""

    def __init__(self, evaluations: Iterable[Evaluation]) -> None:
        by_time: dict[datetime, dict[tuple[str, str], bool]] = {}
        for e in evaluations:
            by_time.setdefault(as_utc(e.evaluated_at, "evaluated_at"), {})[(e.market, e.symbol)] = (
                e.in_universe
            )
        self._times = sorted(by_time)
        self._members = [
            frozenset(key for key, inside in by_time[t].items() if inside) for t in self._times
        ]

    def at(self, instant: datetime) -> frozenset[tuple[str, str]]:
        """The instruments in the universe at ``instant``."""
        index = bisect_right(self._times, as_utc(instant, "instant")) - 1
        return self._members[index] if index >= 0 else frozenset()

    def during(self, day: date) -> frozenset[tuple[str, str]]:
        """The instruments in the universe at some instant of ``day``: those of the
        evaluation in force at its start and of every evaluation that starts in it."""
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        found = set(self.at(start))
        for time, members in zip(self._times, self._members, strict=True):
            if start < time < start + DAY:
                found |= members
        return frozenset(found)
