"""The costs of the exploratory reports (``02`` §12.5.3).

* **The fee per side,** as a taker, is that of each instrument. It is read from KuCoin with
  the read-only client of E1 on the day of the report, one request per instrument within
  the single REST budget, and kept with the report:
  - **Futures:** the ``takerFeeRate`` of the contract (0.06 % in the answer recorded in E1);
  - **Spot:** the taker fee of level 0 for the class of the pair (``feeCategory``), times
    its ``takerFeeCoefficient``. The fee of each class is that of KuCoin's VIP fee table,
    read on 23 November 2026 (``FEE_PAGE``): VIP 0 takes 0.1 % in class A, 0.2 % in B and
    0.3 % in C. A class not in that table stops the report: no fee is guessed.
* **Slippage:** 0.05 % per fill, fixed and declared: the F3 has no book.
* **Stress test:** every report gives its net results again with the fee and the slippage
  doubled.
* **No funding** (§12.4.4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from kbot.backtest.execution import Costs
from kbot.core.decimal import to_canonical
from kbot.kucoin.models import InstrumentList, Model, TextDecimal
from kbot.kucoin.rest import KucoinRestClient

FEE_PAGE = "https://www.kucoin.com/support/48142946141635"
# The taker fee of VIP 0 by class of Spot pair [H, FEE_PAGE, read on 23 November 2026].
SPOT_CLASS_TAKER = {
    "classA": Decimal("0.001"),
    "classB": Decimal("0.002"),
    "classC": Decimal("0.003"),
}
SLIPPAGE = Decimal("0.0005")
STRESS = 2  # the costs of the stress test: fee and slippage doubled


class _SpotFee(Model):
    symbol: str
    feeCategory: str  # KuCoin's field names
    takerFeeCoefficient: TextDecimal


class _FuturesFee(Model):
    symbol: str
    takerFeeRate: TextDecimal


@dataclass(frozen=True)
class Fee:
    market: str
    symbol: str
    taker: Decimal  # per side, as a fraction of the notional
    origin: str  # what KuCoin gave for it
    read_at: datetime

    def costs(self, factor: int = 1) -> Costs:
        """The costs of the report; with ``STRESS``, those of the stress test."""
        return Costs(self.taker * factor, SLIPPAGE * factor)

    def as_json(self) -> dict[str, str]:
        return {
            "mercado": self.market,
            "simbolo": self.symbol,
            "comision_tomador": to_canonical(self.taker),
            "origen": self.origin,
            "leida": self.read_at.isoformat(),
        }


def fee_from(market: str, symbol: str, data: Any, read_at: datetime) -> Fee:
    """The fee of an instrument from the answer of ``uta.v2.instrument`` for it."""
    listing = InstrumentList.model_validate(data)
    if listing.tradeType != market:
        raise ValueError(f"{symbol}: an answer of {listing.tradeType}, not of {market}")
    items = [item for item in listing.list if item.get("symbol") == symbol]
    if len(items) != 1:
        raise ValueError(f"{market} {symbol}: {len(items)} instruments in the answer, not 1")
    if market == "SPOT":
        spot = _SpotFee.model_validate(items[0])
        base = SPOT_CLASS_TAKER.get(spot.feeCategory)
        if base is None:
            raise ValueError(f"{symbol}: no taker fee known for the class {spot.feeCategory!r}")
        taker = (base * spot.takerFeeCoefficient).normalize()
        origin = f"{spot.feeCategory} {base} x takerFeeCoefficient {spot.takerFeeCoefficient}"
    elif market == "FUTURES":
        futures = _FuturesFee.model_validate(items[0])
        taker = futures.takerFeeRate.normalize()
        origin = f"takerFeeRate {futures.takerFeeRate}"
    else:
        raise ValueError(f"unknown market {market!r}")
    return Fee(market, symbol, taker, origin, read_at)


async def read_fees(
    client: KucoinRestClient, instruments: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], Fee]:
    """The fee of each instrument, read from KuCoin once."""
    fees = {}
    for market, symbol in instruments:
        answer = await client.get(
            "uta.v2.instrument", params={"tradeType": market, "symbol": symbol}
        )
        fees[(market, symbol)] = fee_from(market, symbol, answer.data, answer.received_at)
    return fees
