"""Record real public KuCoin responses as fixtures for the contract tests (07 §2 E1, A4).

Usage, from the repository root:  uv run python -m tools.record_kucoin

Everything goes through the guarded clients of ``kbot.kucoin``: public market data only,
GET only, no keys. Each REST case is requested twice in a row so that the weight can be
read from the ``gw-ratelimit-remaining`` header; the first answer is the fixture. The
WebSocket part listens to one complete minute of trades and candles on each market, so
that the candle columns of REST V2 can be matched against the labelled WebSocket fields.

Long lists are cut to a few items. Numbers keep their exact text: a fixture never
contains a value that KuCoin did not send.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from kbot.kucoin.ratelimit import RateLimitHeaders
from kbot.kucoin.rest import KucoinApiError, KucoinRestClient, RestResponse
from kbot.kucoin.ws import ConnectionPacer, PublicStream, Subscription

SPOT_SYMBOL = "BTC-USDT"
FUTURES_SYMBOL = "XBTUSDTM"
KEEP_ITEMS = 3
# D04 (approved on 7 November 2026): UTA V2 plus Classic. V1 is no longer in the whitelist;
# its answers recorded on 6 November 2026 are kept in tests/fixtures/kucoin/evidencia_d04_v1.
VERSIONS = ("v2",)
FIXTURES = Path("tests/fixtures/kucoin")


class RawNumber(str):
    """A JSON number kept as the exact text that the server sent."""


def parse_raw(text: str) -> Any:
    return json.loads(text, parse_float=RawNumber, parse_int=RawNumber)


def dump_raw(value: Any, indent: int = 0) -> str:
    """Pretty JSON that writes RawNumber and Decimal without converting them to float."""
    pad = "  " * (indent + 1)
    if isinstance(value, RawNumber):
        return str(value)
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        if not any(isinstance(item, (list, dict)) for item in value):
            return "[" + ", ".join(dump_raw(item) for item in value) + "]"
        items = ",\n".join(pad + dump_raw(item, indent + 1) for item in value)
        return "[\n" + items + "\n" + "  " * indent + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = ",\n".join(
            f"{pad}{json.dumps(key, ensure_ascii=False)}: {dump_raw(item, indent + 1)}"
            for key, item in value.items()
        )
        return "{\n" + items + "\n" + "  " * indent + "}"
    raise TypeError(f"cannot write {type(value).__name__} as JSON")


def write_json(path: Path, value: Any) -> None:
    path.write_text(dump_raw(value) + "\n", encoding="utf-8", newline="\n")


def truncate(body: Any, keep: int, wanted: str | None) -> dict[str, int | str] | None:
    """Cut the longest list under ``data`` to ``keep`` items, keeping the wanted symbol."""
    if not isinstance(body, dict):
        return None
    candidates: list[tuple[str, dict[str, Any] | list[Any], str | int]] = []
    data = body.get("data")
    if isinstance(data, list):
        candidates.append(("data", body, "data"))
    elif isinstance(data, dict):
        candidates += [(f"data.{k}", data, k) for k, v in data.items() if isinstance(v, list)]
    if not candidates:
        return None
    path, parent, key = max(candidates, key=lambda c: len(c[1][c[2]]))  # type: ignore[index]
    items = parent[key]  # type: ignore[index]
    if len(items) <= keep:
        return None
    kept = items[:keep]
    if wanted is not None and not any(_symbol_of(item) == wanted for item in kept):
        kept += [item for item in items if _symbol_of(item) == wanted][:1]
    parent[key] = kept  # type: ignore[index]
    return {"path": path, "kept": len(kept), "total": len(items)}


def _symbol_of(item: Any) -> Any:
    return item.get("symbol") if isinstance(item, dict) else None


@dataclass(frozen=True)
class Case:
    name: str
    route: str
    params: Mapping[str, str] = field(default_factory=dict)
    path: Mapping[str, str] = field(default_factory=dict)
    wanted: str | None = None
    probe: bool = False  # a parameter value whose validity is being checked (B4)


def rest_cases(now_ms: int) -> list[Case]:
    spot = {"tradeType": "SPOT", "symbol": SPOT_SYMBOL}
    fut = {"tradeType": "FUTURES", "symbol": FUTURES_SYMBOL}
    cases: list[Case] = []
    for version in VERSIONS:
        for label, base in (("spot", spot), ("futures", fut)):
            cases += [
                Case(f"{version}_instrument_{label}", f"uta.{version}.instrument", base),
                Case(f"{version}_ticker_{label}", f"uta.{version}.ticker", base),
                Case(f"{version}_trades_{label}", f"uta.{version}.trades", base),
            ]
        cases.append(
            Case(f"{version}_funding", f"uta.{version}.funding", {"symbol": FUTURES_SYMBOL})
        )
    cases += [
        Case("v2_kline_spot", "uta.v2.kline", {**spot, "interval": "1min"}),
        Case("v2_kline_futures", "uta.v2.kline", {**fut, "interval": "1min"}),
    ]
    for kline_type in ("TRADE", "MARK", "INDEX"):
        cases.append(
            Case(
                f"v2_kline_futures_{kline_type.lower()}",
                "uta.v2.kline",
                {**fut, "interval": "1min", "klineType": kline_type},
                probe=True,
            )
        )
    for product_type in ("USDT-FUTURES", "COIN-FUTURES"):
        cases.append(
            Case(
                f"v2_funding_{product_type.lower()}",
                "uta.v2.funding",
                {"symbol": FUTURES_SYMBOL, "productType": product_type},
                probe=True,
            )
        )
    cases += [
        Case("classic_contracts_active", "futures.contracts", wanted=FUTURES_SYMBOL),
        Case("classic_risk_limit", "futures.risk_limit", path={"symbol": FUTURES_SYMBOL}),
        Case("classic_mark_price", "futures.mark_price", path={"symbol": FUTURES_SYMBOL}),
        Case("classic_index", "futures.index", {"symbol": ".KXBTUSDT", "maxCount": "3"}),
    ]
    history = {
        "symbol": FUTURES_SYMBOL,
        "startAt": str(now_ms - 2 * 86_400_000),
        "endAt": str(now_ms),
    }
    for version in VERSIONS:
        cases.append(Case(f"{version}_funding_history", f"uta.{version}.funding_history", history))
    return cases


def _weight(first: RateLimitHeaders | None, second: RateLimitHeaders | None) -> int | None:
    """Weight of the second call, when both answers belong to the same window."""
    if first is None or second is None or second.reset_ms > first.reset_ms:
        return None
    used = first.remaining - second.remaining
    return used if used > 0 else None


async def record_rest(client: KucoinRestClient, out: Path) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    for case in rest_cases(now_ms):
        responses: list[RestResponse] = []
        error: KucoinApiError | None = None
        for _ in range(2):
            try:
                responses.append(
                    await client.get(case.route, path=case.path or None, params=case.params)
                )
            except KucoinApiError as exc:
                error = exc
                break
        if error is not None and not responses:
            summary.append(
                {
                    "case": case.name,
                    "route": case.route,
                    "params": dict(case.params),
                    "probe": case.probe,
                    "status": error.status,
                    "code": error.code,
                    "error": str(error),
                }
            )
            print(f"  {case.name}: ERROR {error}")
            continue
        first = responses[0]
        weight = _weight(first.ratelimit, responses[1].ratelimit) if len(responses) > 1 else None
        body = parse_raw(first.text)
        cut = truncate(body, KEEP_ITEMS, case.wanted)
        fixture = {
            "recorded_at": first.received_at.isoformat().replace("+00:00", "Z"),
            "route": case.route,
            "request": first.url,
            "status": first.status,
            "weight": weight,
            "ratelimit": None
            if first.ratelimit is None
            else {
                "limit": first.ratelimit.limit,
                "remaining": first.ratelimit.remaining,
                "reset_ms": first.ratelimit.reset_ms,
            },
            "truncated": cut,
            "body": body,
        }
        write_json(out / f"{case.name}.json", fixture)
        summary.append(
            {
                "case": case.name,
                "route": case.route,
                "params": dict(case.params),
                "probe": case.probe,
                "status": first.status,
                "code": body.get("code"),
                "weight": weight,
            }
        )
        print(f"  {case.name}: ok, weight {weight}")
    return summary


@dataclass
class Capture:
    """What one WebSocket connection received."""

    messages: list[tuple[datetime, dict[str, Any]]] = field(default_factory=list)
    control: list[tuple[datetime, dict[str, Any]]] = field(default_factory=list)
    states: list[tuple[datetime, str, str]] = field(default_factory=list)

    def on_message(self, message: dict[str, Any], at: datetime) -> None:
        self.messages.append((at, message))

    def on_control(self, message: dict[str, Any], at: datetime) -> None:
        self.control.append((at, message))

    def on_state(self, state: str, at: datetime, detail: str) -> None:
        self.states.append((at, state, detail))

    def topic(self, prefix: str, depth: str | None = None) -> Iterator[tuple[datetime, dict]]:
        for at, message in self.messages:
            if str(message.get("T", "")).startswith(prefix) and (
                depth is None or message.get("dp") == depth
            ):
                yield at, message


def ws_subscriptions(trade_type: str, symbol: str) -> list[Subscription]:
    subscriptions = [
        Subscription("obu", trade_type, symbol, (("depth", "5"),)),
        Subscription("obu", trade_type, symbol, (("depth", "50"),)),
        Subscription("trade", trade_type, symbol),
        Subscription("ticker", trade_type, symbol),
        Subscription("kline", trade_type, symbol, (("interval", "1min"),)),
    ]
    if trade_type == "FUTURES":
        subscriptions.append(Subscription("mark-price", trade_type, symbol))
    return subscriptions


async def listen(seconds_after_minute: float) -> tuple[dict[str, Capture], int]:
    """Listen until one whole minute has passed; returns the captures and that minute."""
    captures = {"SPOT": Capture(), "FUTURES": Capture()}
    pacer = ConnectionPacer()
    stop = asyncio.Event()
    streams = [
        PublicStream(
            trade_type,
            ws_subscriptions(trade_type, symbol),
            on_message=captures[trade_type].on_message,
            on_control=captures[trade_type].on_control,
            on_state=captures[trade_type].on_state,
            pacer=pacer,
        )
        for trade_type, symbol in (("SPOT", SPOT_SYMBOL), ("FUTURES", FUTURES_SYMBOL))
    ]
    tasks = [asyncio.create_task(stream.run(stop)) for stream in streams]
    await asyncio.sleep(10)  # connected and subscribed before the minute starts
    now = datetime.now(UTC).timestamp()
    minute = int(now // 60 + 1) * 60
    end = minute + 60 + seconds_after_minute
    print(f"  listening until {datetime.fromtimestamp(end, UTC):%H:%M:%S} UTC")
    await asyncio.sleep(end - datetime.now(UTC).timestamp())
    stop.set()
    await asyncio.gather(*tasks)
    return captures, minute


def _sample(items: list[tuple[datetime, dict]], count: int) -> list[dict[str, Any]]:
    return [
        {"received_at": at.isoformat().replace("+00:00", "Z"), "message": message}
        for at, message in items[:count]
    ]


def _seconds(timestamp: int) -> float:
    """Seconds from a KuCoin timestamp in s, ms, us or ns (the unit depends on the field)."""
    for scale in (10**9, 10**6, 10**3):
        if timestamp >= 10**9 * scale:
            return timestamp / scale
    return float(timestamp)


def minute_evidence(capture: Capture, minute: int) -> dict[str, Any]:
    """Trades and candle updates of one whole minute, compact."""
    trades = [m["d"] for _, m in capture.topic("trade.")]
    in_minute = [t for t in trades if minute <= _seconds(int(t["M"])) < minute + 60]
    klines = [(at, m) for at, m in capture.topic("kline.") if _seconds(int(m["d"]["O"])) == minute]
    return {
        "minute_open_s": minute,
        "trades_in_minute": [[t["M"], t["p"], t["q"], t["S"]] for t in in_minute],
        "trade_count": len(in_minute),
        "trade_quantity_sum": str(sum((Decimal(t["q"]) for t in in_minute), Decimal(0))),
        "trade_turnover_sum": str(
            sum((Decimal(t["p"]) * Decimal(t["q"]) for t in in_minute), Decimal(0))
        ),
        "kline_updates": len(klines),
        "kline_last": _sample(klines[-1:], 1),
        "kline_after_minute": _sample(
            [(at, m) for at, m in klines if at.timestamp() >= minute + 60], 2
        ),
    }


async def record_ws(out: Path, seconds_after_minute: float) -> tuple[dict[str, Any], int]:
    captures, minute = await listen(seconds_after_minute)
    summary: dict[str, Any] = {}
    for trade_type, capture in captures.items():
        label = trade_type.lower()
        samples = {
            "obu_5": _sample(list(capture.topic("obu.", "5")), 3),
            "obu_50": _sample(list(capture.topic("obu.", "50")), 1),
            "trade": _sample(list(capture.topic("trade.")), 5),
            "ticker": _sample(list(capture.topic("ticker")), 3),
            "kline": _sample(list(capture.topic("kline.")), 3),
            "mark_price": _sample(list(capture.topic("mark-price")), 3),
            "control": _sample(capture.control, 12),
            "states": [[at.isoformat(), state, detail] for at, state, detail in capture.states],
        }
        fixture = {
            "trade_type": trade_type,
            "samples": samples,
            "minute": minute_evidence(capture, minute),
        }
        write_json(out / f"ws_{label}.json", fixture)
        counts: dict[str, int] = {}
        for _, message in capture.messages:
            key = str(message.get("T")) + (f"/{message['dp']}" if "dp" in message else "")
            counts[key] = counts.get(key, 0) + 1
        summary[trade_type] = {
            "messages": counts,
            "pongs": sum(1 for _, m in capture.control if m.get("type") == "pong"),
        }
        print(f"  {trade_type}: {counts}")
    return summary, minute


async def record_minute_candles(client: KucoinRestClient, out: Path, minute: int) -> None:
    """REST candles of the minute heard on the WebSocket."""
    for label, trade_type, symbol in (
        ("spot", "SPOT", SPOT_SYMBOL),
        ("futures", "FUTURES", FUTURES_SYMBOL),
    ):
        for version in VERSIONS:
            params = {
                "tradeType": trade_type,
                "symbol": symbol,
                "interval": "1min",
                "startAt": str(minute - 120),
                "endAt": str(minute + 120),
            }
            name = f"{version}_kline_{label}_minute"
            try:
                response = await client.get(f"uta.{version}.kline", params=params)
            except KucoinApiError as exc:
                print(f"  {name}: ERROR {exc}")
                continue
            fixture = {
                "recorded_at": response.received_at.isoformat().replace("+00:00", "Z"),
                "route": response.route,
                "request": response.url,
                "status": response.status,
                "minute_open_s": minute,
                "body": parse_raw(response.text),
            }
            write_json(out / f"{name}.json", fixture)
            print(f"  {name}: ok")


async def main(out: Path, seconds_after_minute: float) -> int:
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    async with KucoinRestClient() as client:
        print("REST")
        rest = await record_rest(client, out)
        print("WebSocket")
        ws, minute = await record_ws(out, seconds_after_minute)
        print("REST candles of the recorded minute")
        await asyncio.sleep(5)
        await record_minute_candles(client, out, minute)
    manifest = {
        "recorded_from": "owner's PC (B14 a)",
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "spot_symbol": SPOT_SYMBOL,
        "futures_symbol": FUTURES_SYMBOL,
        "rest": rest,
        "ws": ws,
    }
    write_json(out / "manifest.json", manifest)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=FIXTURES)
    parser.add_argument(
        "--after",
        type=float,
        default=8.0,
        help="seconds to keep listening after the recorded minute ends",
    )
    arguments = parser.parse_args()
    sys.exit(asyncio.run(main(arguments.out, arguments.after)))
