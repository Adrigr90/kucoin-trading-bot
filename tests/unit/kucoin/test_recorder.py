"""The fixture recorder keeps KuCoin's numbers exactly as sent."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kbot.kucoin.jsonparse import loads
from tools.record_kucoin import dump_raw, parse_raw, truncate


def test_numbers_keep_their_exact_text() -> None:
    text = '{"a":1.0E-4,"b":72087.8,"c":28800000,"d":"0.1","e":[1,2.50],"f":null,"g":true}'
    written = dump_raw(parse_raw(text))
    for token in ("1.0E-4", "72087.8", "28800000", '"0.1"', "2.50", "null", "true"):
        assert token in written
    assert loads(written) == loads(text)
    assert loads(written)["a"] == Decimal("0.0001")


def test_text_is_escaped() -> None:
    value = {"s": 'quote " and \\ and \n and ñ'}
    assert loads(dump_raw(value)) == value


def test_decimals_are_written_without_float() -> None:
    assert dump_raw({"x": Decimal("0.1000000000000000055511")}).endswith(
        '"x": 0.1000000000000000055511\n}'
    )
    with pytest.raises(TypeError):
        dump_raw({"x": 0.1})


def test_long_lists_are_cut_keeping_the_wanted_symbol() -> None:
    body = {"code": "200000", "data": [{"symbol": f"S{i}"} for i in range(10)]}
    cut = truncate(body, 3, "S7")
    assert cut == {"path": "data", "kept": 4, "total": 10}
    assert [item["symbol"] for item in body["data"]] == ["S0", "S1", "S2", "S7"]

    nested = {"data": {"tradeType": "SPOT", "list": list(range(5))}}
    assert truncate(nested, 3, None) == {"path": "data.list", "kept": 3, "total": 5}
    assert nested["data"]["list"] == [0, 1, 2]
    assert truncate({"data": [1, 2]}, 3, None) is None
