"""A fake KuCoin REST API that answers like the real one (shapes recorded in E1).

It sits under the guarded client of E1 as its network transport, so every request the
recorder makes still passes the whitelist.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx


def spot_instrument(symbol: str) -> dict[str, Any]:
    base = symbol.split("-")[0]
    return {
        "symbol": symbol,
        "name": symbol,
        "baseCurrency": base,
        "quoteCurrency": "USDT",
        "market": "USDS",
        "minBaseOrderSize": "0.00001",
        "minQuoteOrderSize": "0.1",
        "baseOrderStep": "0.00000001",
        "quoteOrderStep": "0.000001",
        "tickSize": "0.1",
        "feeCurrency": "USDT",
        "tradingStatus": "TradingEnabled",
        "minFunds": "0.1",
    }


def futures_instrument(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": symbol,
        "baseCurrency": symbol.removesuffix("USDTM"),
        "quoteCurrency": "USDT",
        "settlementCurrency": "USDT",
        "isInverse": False,
        "tickSize": "0.1",
        "lotSize": "1",
        "unitSize": "0.001",
        "maxLeverage": "125",
        "makerFeeRate": "0.00020",
        "takerFeeRate": "0.00060",
        "tradingStatus": "Open",
    }


def ticker(symbol: str, volume: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "quoteVolume": volume,
        "bestBidPrice": "100.0",
        "bestAskPrice": "100.2",
        "lastPrice": "100.1",
    }


Rows = Callable[[str, str, int, int], list[list[object]]]


@dataclass
class FakeKucoin(httpx.AsyncBaseTransport):
    spot: list[str]
    futures: list[str]
    candles: Rows = field(default=lambda market, symbol, start, end: [])
    index_value: Callable[[int], object] = field(default=lambda ms: 75000)
    requests: list[str] = field(default_factory=list)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        q = dict(request.url.params)
        self.requests.append(path)
        data: Any
        if path.endswith("/market/instrument"):
            names = self.spot if q["tradeType"] == "SPOT" else self.futures
            make = spot_instrument if q["tradeType"] == "SPOT" else futures_instrument
            data = {"tradeType": q["tradeType"], "list": [make(s) for s in names]}
        elif path.endswith("/market/ticker"):
            names = self.spot if q["tradeType"] == "SPOT" else self.futures
            data = {
                "tradeType": q["tradeType"],
                "ts": 1,
                "list": [ticker(s, str(1000 - i)) for i, s in enumerate(names)],
            }
        elif path == "/api/v1/contracts/active":
            data = [{"symbol": s, "indexSymbol": ".K" + s.removesuffix("M")} for s in self.futures]
        elif path.startswith("/api/v1/contracts/risk-limit/"):
            symbol = path.rsplit("/", 1)[1]
            data = [
                {
                    "symbol": symbol,
                    "level": 1,
                    "maxRiskLimit": 250000,
                    "minRiskLimit": 0,
                    "maxLeverage": 125,
                    "initialMargin": 0.008,
                    "maintainMargin": 0.004,
                },
                {
                    "symbol": symbol,
                    "level": 2,
                    "maxRiskLimit": 600000,
                    "minRiskLimit": 250000,
                    "maxLeverage": 100,
                    "initialMargin": 0.01,
                    "maintainMargin": 0.005,
                },
            ]
        elif path.endswith("/market/kline"):
            start, end = int(q["startAt"]), int(q["endAt"])
            data = {
                "tradeType": q["tradeType"],
                "symbol": q["symbol"],
                "list": self.candles(q["tradeType"], q["symbol"], start, end),
            }
        elif path.endswith("/market/funding-rate"):
            data = [
                {
                    "symbol": q["symbol"],
                    "nextFundingRate": "0.0001",
                    "fundingTime": 1794009600000,
                    "fundingRateCap": "0.003",
                    "fundingRateFloor": "-0.003",
                    "currentGranularity": 28800000,
                    "newGranularity": 28800000,
                    "newGranularityStartTime": 1750147200000,
                }
            ]
        elif path.endswith("/market/funding-rate-history"):
            data = {
                "symbol": q["symbol"],
                "list": [{"fundingRate": "0.0001", "ts": 1793980800000}],
            }
        elif path == "/api/v1/index/query":
            start, end = int(q["startAt"]), int(q["endAt"])
            data = {
                "dataList": [
                    {
                        "symbol": q["symbol"],
                        "granularity": 1000,
                        "timePoint": ms,
                        "value": self.index_value(ms),
                    }
                    for ms in range(start - start % 1000, end + 1, 1000)
                ],
                "hasMore": False,
            }
        else:
            return httpx.Response(404, json={"code": "404000", "msg": "not found"})
        return httpx.Response(200, json={"code": "200000", "data": data})
