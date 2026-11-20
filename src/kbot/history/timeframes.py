"""Candles of 5 m, 15 m, 1 h, 4 h and 1 d from the candles of 1 minute (``02`` §12.1,
points 3 to 5; F3-E1).

* **Time is never compressed.** A candle is indexed by the UTC opening of its interval, and
  two candles are consecutive only if the second opens one interval after the first; the
  position in a list never decides it.
* **Each interval starts at a multiple of its length counted from 00:00 UTC**: the 4 h
  candles open at 00, 04, 08, 12, 16 and 20 UTC, the daily one at 00:00 UTC.
* **A candle is made from the minutes present in its interval:** open of the first, close
  of the last, highest high, lowest low, volume and turnover added in exact decimals, and
  how many of its N minutes there are. It is **complete** with all N, **incomplete** with
  fewer (kept for the reports, never used by an indicator), and with none there is no
  candle: that is an absence.
* **The sums are exact:** they run in the accounting context (precision 80, ``Inexact``
  trapped), never in the ambient one, whose 28 digits rounded the daily turnover of
  ZEC-USDT (F3E1-01). A sum that does not fit raises; it is never rounded.
* **It only exists closed:** an interval that has not ended within what was downloaded
  gives no candle. A complete candle is **available** when the last of its minutes is
  (CD-R02); before that it does not exist for anybody.
* **Quality:** in each of the dimensions P, V and T a candle takes the worst of its
  minutes, and its evidence is ``vela_rest_suficiente`` if any of them is.
* **Deterministic:** the result depends only on the set of minutes by their key (market,
  symbol, opening). The order of loading and an identical repeat change nothing; two
  different candles with the same key stop the process, and none is chosen.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import IntEnum

from kbot.core.decimal import accounting
from kbot.market.last_candles import STABILIZATION, CandleEvidence, Ohlcv
from kbot.market.timeutil import MINUTE, as_utc, floor_to

# Length in minutes of each timeframe built from the minutes.
TIMEFRAMES: Mapping[str, int] = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


class Quality(IntEnum):
    """Quality of one dimension (P, V or T) of a candle, from best to worst (02 §3.4)."""

    CONTRASTADA = 0  # REST and the WebSocket compared, and consistent
    SOLO_REST = 1  # REST alone, without contrast
    DISCREPANTE = 2  # they disagree: the dimension is not valid


class ConflictingCandles(ValueError):
    """Two different candles with the same key: none can be chosen."""


@dataclass(frozen=True)
class MinuteCandle:
    """A closed candle of 1 minute with what the contract needs to know about it."""

    market: str
    symbol: str
    candle: Ohlcv
    evidence: CandleEvidence
    price: Quality
    volume: Quality
    time: Quality
    available_at: datetime  # when it became a closed candle (02 §12.1, point 4)

    @property
    def key(self) -> tuple[str, str, datetime]:
        return (self.market, self.symbol, self.candle.open_time)


def historical_minute(market: str, symbol: str, candle: Ohlcv) -> MinuteCandle:
    """A candle downloaded from REST for the history (F3-E1): every dimension is only REST,
    its evidence is ``vela_rest_suficiente``, and it is available at the end of its minute
    plus the stabilization margin, the rule declared for the history."""
    end = as_utc(candle.open_time, "open_time") + MINUTE
    return MinuteCandle(
        market,
        symbol,
        candle,
        CandleEvidence.REST_SUFICIENTE,
        Quality.SOLO_REST,
        Quality.SOLO_REST,
        Quality.SOLO_REST,
        end + STABILIZATION,
    )


@dataclass(frozen=True)
class Bar:
    """A candle of a timeframe above 1 minute."""

    market: str
    symbol: str
    timeframe: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal
    minutes_present: int
    minutes: int  # N, the minutes of the interval
    price: Quality
    volume_quality: Quality
    time: Quality
    evidence: CandleEvidence
    # When the last of its minutes became available; None if incomplete: it never is.
    available_at: datetime | None
    # Of its minutes, those only REST (``vela_rest_suficiente``): the share of results based
    # only on REST counts original minutes (02 §12.1, point 5; §12.2, point 1).
    rest_only_minutes: int = 0

    @property
    def complete(self) -> bool:
        return self.minutes_present == self.minutes

    def usable_at(self, moment: datetime) -> bool:
        """Whether an indicator may use it at ``moment``: complete and already available."""
        return self.available_at is not None and self.available_at <= as_utc(moment, "moment")


def collect(minutes: Iterable[MinuteCandle]) -> dict[tuple[str, str, datetime], MinuteCandle]:
    """The minutes by their key. An identical repeat changes nothing; two different
    candles with the same key raise ``ConflictingCandles``."""
    by_key: dict[tuple[str, str, datetime], MinuteCandle] = {}
    for minute in minutes:
        if as_utc(minute.candle.open_time, "open_time") != floor_to(
            minute.candle.open_time, MINUTE
        ):
            raise ValueError(f"a minute must open on the minute: {minute.key}")
        held = by_key.setdefault(minute.key, minute)
        if held != minute:
            raise ConflictingCandles(f"two different candles for {minute.key}")
    return by_key


def interval_start(moment: datetime, timeframe: str) -> datetime:
    """Opening of the interval of ``timeframe`` that holds ``moment``, in UTC."""
    return floor_to(moment, timedelta(minutes=TIMEFRAMES[timeframe]))


def aggregate(
    minutes: Iterable[MinuteCandle], timeframe: str, *, downloaded_until: datetime
) -> list[Bar]:
    """The candles of ``timeframe`` of every instrument in ``minutes``, sorted by market,
    symbol and opening. Only intervals that end by ``downloaded_until`` (the end of what
    was downloaded, exclusive) give a candle."""
    length = TIMEFRAMES[timeframe]
    span = timedelta(minutes=length)
    until = as_utc(downloaded_until, "downloaded_until")
    groups: dict[tuple[str, str, datetime], list[MinuteCandle]] = {}
    for minute in collect(minutes).values():
        start = interval_start(minute.candle.open_time, timeframe)
        if start + span <= until:
            groups.setdefault((minute.market, minute.symbol, start), []).append(minute)
    bars = []
    for (market, symbol, start), members in sorted(groups.items()):
        members.sort(key=lambda m: m.candle.open_time)
        candles = [m.candle for m in members]
        complete = len(members) == length
        bars.append(
            Bar(
                market=market,
                symbol=symbol,
                timeframe=timeframe,
                open_time=start,
                open=candles[0].open,
                high=max(c.high for c in candles),
                low=min(c.low for c in candles),
                close=candles[-1].close,
                volume=exact_sum(c.volume for c in candles),
                turnover=exact_sum(c.turnover for c in candles),
                minutes_present=len(members),
                minutes=length,
                price=max(m.price for m in members),
                volume_quality=max(m.volume for m in members),
                time=max(m.time for m in members),
                evidence=_evidence(m.evidence for m in members),
                available_at=max(m.available_at for m in members) if complete else None,
                rest_only_minutes=sum(
                    1 for m in members if m.evidence is CandleEvidence.REST_SUFICIENTE
                ),
            )
        )
    return bars


def exact_sum(values: Iterable[Decimal]) -> Decimal:
    """The exact sum, whatever the ambient decimal context. Format 2 holds at most 14
    integer digits and 24 decimals, so the 1440 minutes of a day add up to at most 42
    digits, far below the 80 of the accounting context; beyond them ``Inexact`` is raised."""
    with accounting():
        return sum(values, Decimal(0))


def consecutive(first: Bar, second: Bar) -> bool:
    """Whether ``second`` is the candle right after ``first``: same instrument and
    timeframe, opening one interval later. A gap between them is never skipped."""
    return (first.market, first.symbol, first.timeframe) == (
        second.market,
        second.symbol,
        second.timeframe,
    ) and second.open_time - first.open_time == timedelta(minutes=TIMEFRAMES[first.timeframe])


def _evidence(evidences: Iterable[CandleEvidence]) -> CandleEvidence:
    seen = set(evidences)
    if CandleEvidence.REST_SUFICIENTE in seen:
        return CandleEvidence.REST_SUFICIENTE
    if CandleEvidence.CRUZADA_DISCREPANTE in seen:
        return CandleEvidence.CRUZADA_DISCREPANTE
    return CandleEvidence.CRUZADA_CONSISTENTE
