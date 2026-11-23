"""The costs of the reports: the fee of each instrument read from KuCoin, the slippage and
the stress test (``02`` §12.5.3 and §12.5.6)."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kbot.backtest.execution import Costs
from kbot.backtest.fees import SLIPPAGE, STRESS, fee_from, read_fees
from tests.unit.backtest.history import READ_AT

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "kucoin"


def recorded(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["body"]["data"]


def spot(category: str, coefficient: str, symbol: str = "BTC-USDT") -> dict[str, Any]:
    item = {"symbol": symbol, "feeCategory": category, "takerFeeCoefficient": coefficient}
    return {"tradeType": "SPOT", "list": [item]}


def test_the_fees_of_the_answers_recorded_in_e1() -> None:
    btc = fee_from("SPOT", "BTC-USDT", recorded("v2_instrument_spot"), READ_AT)
    assert btc.taker == Decimal("0.001")
    assert btc.origin == "classA 0.001 x takerFeeCoefficient 1.00"
    xbt = fee_from("FUTURES", "XBTUSDTM", recorded("v2_instrument_futures"), READ_AT)
    assert xbt.taker == Decimal("0.0006")
    assert xbt.origin == "takerFeeRate 0.00060"
    assert xbt.as_json()["comision_tomador"] == "0.0006"


@pytest.mark.parametrize(
    ("category", "coefficient", "taker"),
    [
        ("classA", "1.00", "0.001"),
        ("classB", "1.00", "0.002"),
        ("classC", "1.00", "0.003"),
        ("classB", "0.5", "0.001"),
        ("classA", "0", "0"),
    ],
)
def test_a_spot_fee_is_its_class_times_its_coefficient(
    category: str, coefficient: str, taker: str
) -> None:
    fee = fee_from("SPOT", "BTC-USDT", spot(category, coefficient), READ_AT)
    assert fee.taker == Decimal(taker)


def test_no_fee_is_guessed() -> None:
    with pytest.raises(ValueError, match="no taker fee known"):
        fee_from("SPOT", "BTC-USDT", spot("classD", "1"), READ_AT)
    with pytest.raises(ValueError, match="not of FUTURES"):
        fee_from("FUTURES", "BTC-USDT", spot("classA", "1"), READ_AT)
    with pytest.raises(ValueError, match="0 instruments"):
        fee_from("SPOT", "ETH-USDT", spot("classA", "1"), READ_AT)


def test_the_costs_and_the_stress_test_double_them() -> None:
    fee = fee_from("SPOT", "BTC-USDT", spot("classA", "1.00"), READ_AT)
    assert (
        fee.costs()
        == Costs(Decimal("0.001"), SLIPPAGE)
        == Costs(Decimal("0.001"), Decimal("0.0005"))
    )
    assert fee.costs(STRESS) == Costs(Decimal("0.002"), Decimal("0.0010"))


class Client:
    """Answers ``uta.v2.instrument`` from a table, and keeps what it was asked."""

    def __init__(self, answers: dict[tuple[str, str], Any]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, dict[str, str]]] = []

    async def get(self, route: str, *, params: dict[str, str]) -> SimpleNamespace:
        self.asked.append((route, dict(params)))
        return SimpleNamespace(
            data=self.answers[(params["tradeType"], params["symbol"])], received_at=READ_AT
        )


def test_the_fees_are_read_once_one_request_per_instrument() -> None:
    client = Client(
        {
            ("SPOT", "BTC-USDT"): recorded("v2_instrument_spot"),
            ("FUTURES", "XBTUSDTM"): recorded("v2_instrument_futures"),
        }
    )
    fees = asyncio.run(read_fees(client, [("SPOT", "BTC-USDT"), ("FUTURES", "XBTUSDTM")]))  # type: ignore[arg-type]
    assert {k: v.taker for k, v in fees.items()} == {
        ("SPOT", "BTC-USDT"): Decimal("0.001"),
        ("FUTURES", "XBTUSDTM"): Decimal("0.0006"),
    }
    assert client.asked == [
        ("uta.v2.instrument", {"tradeType": "SPOT", "symbol": "BTC-USDT"}),
        ("uta.v2.instrument", {"tradeType": "FUTURES", "symbol": "XBTUSDTM"}),
    ]
    assert all(f.read_at == READ_AT for f in fees.values())
