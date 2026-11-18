"""The universe of the history and the evidence of eligibility (``02`` §12.1, points 1 and
2; F3-E1).

The universe is a versioned list in the repository, with its date
(``docs/f3/universo_<fecha>.json``): the 20 + 20 instruments of the recorder's universe on
that day, the delisted ones KuCoin still serves, and the official announcements with their
links. The evidence of eligibility is independent of the candles:

* in Futures, the opening of the contract (``firstOpenDate`` of ``/api/v1/contracts/active``,
  in ms [H, response recorded in E1]): not eligible before it, eligible from it;
* the first time the recorder saw the instrument trading (``market.instruments`` keeps only
  changes, so it kept trading from then on): eligible from then;
* an announcement: whatever it says, between its dates, with its link.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from kbot.history.coverage import EligibilitySpan
from kbot.market.timeutil import EPOCH, as_utc, from_nanoseconds

FORMAT = "kbot.history universo 1"


@dataclass(frozen=True)
class Member:
    market: str
    symbol: str
    rank: int | None
    l50: bool
    trading_since: datetime | None  # first time the recorder saw it trading


@dataclass(frozen=True)
class Announcement:
    market: str
    symbol: str
    start: datetime
    end: datetime
    eligible: bool
    link: str


@dataclass(frozen=True)
class Universe:
    day: date
    members: tuple[Member, ...]
    delisted: tuple[Member, ...]
    announcements: tuple[Announcement, ...]

    @property
    def instruments(self) -> tuple[Member, ...]:
        return self.members + self.delisted


def load(path: Path) -> Universe:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("formato") != FORMAT:
        raise ValueError(f"{path.name}: unknown universe format {data.get('formato')!r}")
    return Universe(
        day=date.fromisoformat(data["fecha"]),
        members=tuple(_member(m) for m in data["instrumentos"]),
        delisted=tuple(_member(m) for m in data.get("deslistados", [])),
        announcements=tuple(
            Announcement(
                a["mercado"],
                a["simbolo"],
                as_utc(datetime.fromisoformat(a["desde"]), "desde"),
                as_utc(datetime.fromisoformat(a["hasta"]), "hasta"),
                bool(a["elegible"]),
                str(a["enlace"]),
            )
            for a in data.get("anuncios", [])
        ),
    )


def first_open_dates(contracts: Any) -> dict[str, datetime]:
    """``firstOpenDate`` of each contract in an answer of ``futures.contracts``."""
    dates = {}
    for contract in contracts if isinstance(contracts, list) else []:
        if not isinstance(contract, dict):
            continue
        symbol, opened = contract.get("symbol"), contract.get("firstOpenDate")
        if isinstance(symbol, str) and isinstance(opened, int) and not isinstance(opened, bool):
            dates[symbol] = from_nanoseconds(opened * 1_000_000)
    return dates


def evidence(
    universe: Universe,
    market: str,
    symbol: str,
    *,
    first_open: Mapping[str, datetime],
    until: datetime,
) -> list[EligibilitySpan]:
    """The evidence of eligibility of an instrument, up to ``until``."""
    spans = []
    opened = first_open.get(symbol) if market == "FUTURES" else None
    if opened is not None:
        spans.append(EligibilitySpan(EPOCH, opened, False, "firstOpenDate"))
        spans.append(EligibilitySpan(opened, until, True, "firstOpenDate"))
    for member in universe.instruments:
        if (member.market, member.symbol) == (market, symbol) and member.trading_since:
            spans.append(
                EligibilitySpan(member.trading_since, until, True, "instruments (grabador)")
            )
    for a in universe.announcements:
        if (a.market, a.symbol) == (market, symbol):
            spans.append(EligibilitySpan(a.start, a.end, a.eligible, f"anuncio {a.link}"))
    return spans


def _member(data: Mapping[str, Any]) -> Member:
    seen = data.get("cotizando_desde")
    return Member(
        market=str(data["mercado"]),
        symbol=str(data["simbolo"]),
        rank=data.get("rango"),
        l50=bool(data.get("nivel_50", False)),
        trading_since=as_utc(datetime.fromisoformat(seen), "cotizando_desde") if seen else None,
    )
