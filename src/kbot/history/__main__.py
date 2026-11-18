"""The history of F3-E1 on the PC: ``python -m kbot.history``.

    uv run python -m kbot.history descargar \
        --universo docs/f3/universo_2026-11-18.json --carpeta C:/kbot_historico
    uv run python -m kbot.history informe \
        --universo docs/f3/universo_2026-11-18.json --carpeta C:/kbot_historico

``descargar`` asks KuCoin, with the read-only client of E1, for the 1-minute candles of the
last ``--dias`` days (730 by default) of each instrument, day by day, and keeps the opening
dates of the Futures contracts (``contratos_futures.json``) as evidence of eligibility.
Repeating it resumes: a closed day is never asked again. ``informe`` writes the coverage
report from the files alone (``informe_cobertura.md`` and ``cobertura.json``).
``--solo MERCADO:SÍMBOLO`` (repeatable) limits either to some instruments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import structlog

from kbot.core.clock import SystemClock
from kbot.history.download import Downloader, Instrument, window_for
from kbot.history.report import instrument_report, write
from kbot.history.universe import Member, Universe, evidence, first_open_dates, load
from kbot.kucoin.rest import KucoinRestClient

log = structlog.get_logger("kbot.history")
CONTRACTS = "contratos_futures.json"


def selected(universe: Universe, only: Sequence[str]) -> list[Member]:
    if not only:
        return list(universe.instruments)
    wanted = {tuple(item.split(":", 1)) for item in only}
    chosen = [m for m in universe.instruments if (m.market, m.symbol) in wanted]
    missing = wanted - {(m.market, m.symbol) for m in chosen}
    if missing:
        raise SystemExit(f"not in the universe: {sorted(missing)}")
    return chosen


async def download(arguments: argparse.Namespace) -> None:
    universe = load(arguments.universo)
    now = SystemClock().now()
    client = KucoinRestClient()
    try:
        opened = first_open_dates((await client.get("futures.contracts")).data)
        save_contracts(arguments.carpeta, opened)
        downloader = Downloader(client, arguments.carpeta)
        for member in selected(universe, arguments.solo):
            window = window_for(member.market, arguments.dias, now)
            records = await downloader.instrument(
                Instrument(member.market, member.symbol), window, now
            )
            log.info(
                "history_instrument",
                market=member.market,
                symbol=member.symbol,
                days=len(records),
                closed=sum(1 for r in records if r.closed),
                candles=sum(r.rows for r in records),
            )
    finally:
        await client.aclose()


def report(arguments: argparse.Namespace) -> None:
    universe = load(arguments.universo)
    now = SystemClock().now()
    opened = load_contracts(arguments.carpeta)
    reports = [
        instrument_report(
            arguments.carpeta,
            m.market,
            m.symbol,
            evidence(universe, m.market, m.symbol, first_open=opened, until=now),
        )
        for m in selected(universe, arguments.solo)
    ]
    write(
        reports,
        arguments.carpeta / "informe_cobertura.md",
        arguments.carpeta / "cobertura.json",
        universe_day=universe.day,
        generated_at=now,
    )


def save_contracts(folder: Path, opened: dict[str, datetime]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    content = {symbol: moment.isoformat() for symbol, moment in sorted(opened.items())}
    (folder / CONTRACTS).write_text(json.dumps(content, indent=1) + "\n", encoding="utf-8")


def load_contracts(folder: Path) -> dict[str, datetime]:
    path = folder / CONTRACTS
    if not path.exists():
        return {}
    return {s: datetime.fromisoformat(v) for s, v in json.loads(path.read_text("utf-8")).items()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The history of F3-E1 on the PC")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("descargar", "informe"):
        command = commands.add_parser(name)
        command.add_argument("--universo", type=Path, required=True)
        command.add_argument("--carpeta", type=Path, required=True)
        command.add_argument("--solo", action="append", default=[], metavar="MERCADO:SÍMBOLO")
        if name == "descargar":
            command.add_argument("--dias", type=int, default=730)
    arguments = parser.parse_args(argv)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
    if arguments.command == "descargar":
        asyncio.run(download(arguments))
    else:
        report(arguments)
    return 0


if __name__ == "__main__":
    sys.exit(main())
