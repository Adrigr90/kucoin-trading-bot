"""How far back KuCoin's public history goes (07 §2, stage E3; 04, B3 and B13).

Usage, from the repository root:

    uv run python -m tools.kucoin_history index .KXBTUSDT .KETHUSDT
    uv run python -m tools.kucoin_history klines SPOT ABC-USDT XYZ-USDT

Everything goes through the guarded client of ``kbot.kucoin``: public market data only,
GET only, no keys, inside the machine-wide request budget. Both searches bisect over time,
so each answer costs a few dozen light requests at most.

* ``index``: the oldest value of Classic's index history (``/api/v1/index/query``).
* ``klines``: whether UTA V2 gives one-minute candles of a symbol (a delisted pair, for
  example) and, if it does, the oldest and the latest it gives.

The bisection assumes that the history has no hole longer than one search window (an hour
for the index, a day for the candles) between its oldest and its latest point.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from kbot.kucoin.rest import KucoinApiError, KucoinRestClient
from kbot.market.reference import index_points

HOUR_MS = 3_600_000
DAY_S = 86_400
# Before any KuCoin market existed: the searches start here.
EPOCH = datetime(2017, 1, 1, tzinfo=UTC)

Probe = Callable[[int], Awaitable[list[int]]]


async def first_window(probe: Probe, old: int, recent: int, window: int) -> int:
    """The start of the earliest window with data, on the grid ``old + k * window``, given
    that the window at ``old`` has none and the one at ``recent`` has some."""
    low, high = 0, (recent - old) // window
    while high - low > 1:
        middle = (low + high) // 2
        if await probe(old + middle * window):
            high = middle
        else:
            low = middle
    return old + high * window


async def last_window(probe: Probe, old: int, recent: int, window: int) -> int:
    """The start of the latest window with data, on the grid ``old + k * window``, given
    that the window at ``old`` has some and the one at ``recent`` has none."""
    low, high = 0, (recent - old) // window
    while high - low > 1:
        middle = (low + high) // 2
        if await probe(old + middle * window):
            low = middle
        else:
            high = middle
    return old + low * window


def _utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


async def oldest_index_value(client: KucoinRestClient, symbol: str, now_ms: int) -> str:
    async def probe(start: int) -> list[int]:
        response = await client.get(
            "futures.index",
            params={
                "symbol": symbol,
                "startAt": start,
                "endAt": start + HOUR_MS - 1,
                "maxCount": 1,
                "reverse": "false",
            },
        )
        points = index_points(response.data, symbol)
        if points is None:
            raise ValueError(f"{symbol}: unexpected answer: {response.text[:200]}")
        return [moment for moment, _ in points if start <= moment < start + HOUR_MS]

    old = int(EPOCH.timestamp() * 1000)
    recent = now_ms - 2 * HOUR_MS
    recent -= (recent - old) % HOUR_MS
    if await probe(old):
        return f"{symbol}: there is a value in the first hour searched, {_utc(old / 1000)}"
    if not await probe(recent):
        return f"{symbol}: no value two hours ago either"
    start = await first_window(probe, old, recent, HOUR_MS)
    first = min(await probe(start))
    return f"{symbol}: oldest value at {_utc(first / 1000)}"


async def candle_range(client: KucoinRestClient, market: str, symbol: str, now_s: int) -> str:
    async def probe(start: int) -> list[int]:
        response = await client.get(
            "uta.v2.kline",
            params={
                "tradeType": market,
                "symbol": symbol,
                "interval": "1min",
                "startAt": start,
                "endAt": start + DAY_S - 1,
            },
        )
        data = response.data
        rows = data.get("list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError(f"{symbol}: unexpected answer: {response.text[:200]}")
        return [int(row[0]) for row in rows if start <= int(row[0]) < start + DAY_S]

    old = int(EPOCH.timestamp())
    recent = now_s - DAY_S
    recent -= (recent - old) % DAY_S
    try:
        if await probe(old):
            return f"{market} {symbol}: there are candles in the first day searched"
        # The latest candle: bisect back from now if today has none.
        latest_day = recent
        if not await probe(recent):
            with_data = await _any_day(probe, old, recent)
            if with_data is None:
                return f"{market} {symbol}: no candles found"
            latest_day = await last_window(probe, with_data, recent, DAY_S)
        oldest_day = await first_window(probe, old, latest_day, DAY_S)
    except KucoinApiError as error:
        return f"{market} {symbol}: no candles: {error}"
    oldest = min(await probe(oldest_day))
    latest = max(await probe(latest_day))
    return f"{market} {symbol}: candles from {_utc(oldest)} to {_utc(latest)}"


async def _any_day(probe: Probe, old: int, recent: int) -> int | None:
    """Some day with candles before ``recent``: one probe every 30 days, newest first, so
    a pair listed for less than 30 days long ago can be missed."""
    step = 30 * DAY_S
    for start in range(recent - step, old, -step):
        if await probe(start):
            return start
    return None


async def run(arguments: argparse.Namespace) -> list[str]:
    now = datetime.now(UTC).timestamp()
    async with KucoinRestClient() as client:
        if arguments.command == "index":
            symbols = arguments.symbols or [".KXBTUSDT", ".KETHUSDT"]
            return [await oldest_index_value(client, s, int(now * 1000)) for s in symbols]
        return [
            await candle_range(client, arguments.market, s, int(now)) for s in arguments.symbols
        ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="oldest value of Classic's index history")
    index.add_argument("symbols", nargs="*", help="index symbols, such as .KXBTUSDT")
    klines = commands.add_parser("klines", help="range of UTA V2 one-minute candles")
    klines.add_argument("market", choices=["SPOT", "FUTURES"])
    klines.add_argument("symbols", nargs="+")
    arguments = parser.parse_args(argv)
    print(f"Asked at {_utc(datetime.now(UTC).timestamp())}")
    for line in asyncio.run(run(arguments)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
