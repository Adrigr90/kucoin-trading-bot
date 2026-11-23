"""The exploratory evaluation of the families: ``python -m kbot.backtest.explore``
(``02`` §12.5.5; F3-A11).

    uv run python -m kbot.backtest.explore --familia todas --particion desarrollo \
        --carpeta C:/kbot_historico

* **Version:** that of each family (§12.3.3), from the commit checked out in the repository.
  With a followed file changed there is no version, and no report.
* **Data:** the minutes of each instrument from F3-E1, only those of each window and of its
  warm-up, and never one of the test (``reader``).
* **Coverage** (F3E5-05): that of the contract (§12.1, point 2), with the presence of each
  minute asked and the evidence of eligibility of F3-E1. The minutes present over the whole
  calendar of the windows are another measure, named apart.
* **Bounded memory:** the engine runs by instrument and window, with about 140 days of
  minutes at once. Each family runs alone (the mode by family, §12.4.3) over the same
  minutes, read once.
* **Fees:** read from KuCoin once, before anything else, and kept with the report.
* **Validation:** one family at a time. After the fees, and before the first minute is read,
  the reservation of the family and version is created; at the end it is marked
  ``completada`` or ``fallida``, and its copy goes to ``docs/f3/consultas_validacion.json``
  (§12.5.1; F3E5-01).
* **Outputs:** the report and its JSON in ``docs/informes/exploratorio/``, and the full
  record (signals, decisions and trades of every run, and the fees) in
  ``<history>/informes/``, outside Git.
* **Deterministic:** the same data, version and costs give the same report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import structlog

from kbot.backtest import report
from kbot.backtest.engine import run
from kbot.backtest.fees import Fee, read_fees
from kbot.backtest.partitions import DEVELOPMENT, HISTORY_END, VALIDATION, Segment, segments
from kbot.backtest.reader import Read, read_minutes
from kbot.backtest.report import Evaluation, Piece
from kbot.backtest.reservation import validation_query
from kbot.core.clock import SystemClock
from kbot.core.decimal import to_canonical
from kbot.history.__main__ import load_contracts
from kbot.history.coverage import EligibilitySpan, coverage
from kbot.history.universe import evidence as eligibility_evidence
from kbot.history.universe import load
from kbot.kucoin.rest import KucoinRestClient
from kbot.market.timeutil import MINUTE
from kbot.strategies.breakout import Breakout
from kbot.strategies.ficha import Ficha
from kbot.strategies.mean_reversion import MeanReversion
from kbot.strategies.trend import Trend
from kbot.strategies.version import NoVersion, repository_commit, strategy_version

log = structlog.get_logger("kbot.backtest")
CLOCK = SystemClock()

# The three fichas with the parameters approved by the owner on 20 November 2026.
FAMILIES: Mapping[str, Callable[[], Ficha]] = {
    "tendencia": Trend,
    "breakout": Breakout,
    "reversion_media": MeanReversion,
}
ALL = "todas"
UNIVERSE = Path("docs/f3/universo_2026-11-18.json")
REPORTS = Path("docs/informes/exploratorio")
COPY = Path("docs/f3/consultas_validacion.json")
NO_DECISIONS = (
    "ninguna todavía: el propietario decide qué familias siguen con los informes del "
    "desarrollo (§12.5.7)."
)

Reader = Callable[[Path, str, str, datetime, datetime], Read]


def evaluate(
    fichas: Sequence[Ficha],
    commit: str,
    partition: str,
    root: Path,
    instruments: Sequence[tuple[str, str]],
    fees: Mapping[tuple[str, str], Fee],
    *,
    universe: str,
    decisions: str = NO_DECISIONS,
    evidence: Mapping[tuple[str, str], Sequence[EligibilitySpan]] | None = None,
    windows: Sequence[Segment] | None = None,
    reader: Reader = read_minutes,
    now: Callable[[], datetime] = CLOCK.now,
    copy: Path | None = None,
) -> list[Evaluation]:
    """Run each family alone over every instrument and window of ``partition``.
    ``evidence`` is the evidence of eligibility of each instrument, for the coverage
    (F3E5-05); without it every minute is of unknown eligibility. ``windows`` replaces
    the fixed ones of the partition, for the tests."""
    if partition not in (DEVELOPMENT, VALIDATION):
        segments(partition)  # the test is sealed: it raises
        raise ValueError(f"unknown partition {partition!r}")
    chosen = tuple(windows) if windows is not None else segments(partition)
    if not fichas or len({f.family for f in fichas}) != len(fichas):
        raise ValueError("one ficha per family, at least one")
    missing = [i for i in instruments if i not in fees]
    if missing:
        raise ValueError(f"no fee for {missing}")
    versions = {f.family: strategy_version(commit, f.params.as_json()) for f in fichas}

    def go() -> list[Evaluation]:
        return _evaluate(
            fichas,
            versions,
            partition,
            root,
            instruments,
            fees,
            evidence or {},
            chosen,
            reader,
            universe,
            decisions,
        )

    if partition == DEVELOPMENT:
        evaluations = go()
    else:
        if len(fichas) != 1:
            raise ValueError("the validation is consulted one family at a time")
        family = fichas[0].family
        folder = root / "validacion"
        with validation_query(folder, family, versions[family], now, copy) as query:
            evaluations = go()
            (evaluation,) = evaluations
            query.result = {
                "informe": evaluation.name,
                **report.figures(evaluation.pieces)["resultados"][report.NET],
            }
    return evaluations


def _evaluate(
    fichas: Sequence[Ficha],
    versions: Mapping[str, str],
    partition: str,
    root: Path,
    instruments: Sequence[tuple[str, str]],
    fees: Mapping[tuple[str, str], Fee],
    evidence: Mapping[tuple[str, str], Sequence[EligibilitySpan]],
    chosen: Sequence[Segment],
    reader: Reader,
    universe: str,
    decisions: str,
) -> list[Evaluation]:
    pieces: dict[str, list[Piece]] = {f.family: [] for f in fichas}
    for market, symbol in sorted(instruments):
        fee = fees[(market, symbol)]
        spans = evidence.get((market, symbol), ())
        for segment in chosen:
            window = segment.window
            read = reader(root, market, symbol, segment.warmup_from, window.end)
            present = sum(1 for m in read.minutes if window.holds(m.candle.open_time))
            expected = (window.end - window.start) // MINUTE
            # The coverage of the contract (§12.1, point 2), over the minutes asked.
            asked = {m: s for m, s in read.states.items() if window.holds(m)}
            counted = coverage(asked, spans)
            for ficha in fichas:
                family = ficha.family
                done = None
                if read.minutes:
                    done = run(
                        [ficha], read.minutes, window, fee.costs(), {family: versions[family]}
                    )
                pieces[family].append(
                    Piece(
                        segment.name,
                        market,
                        symbol,
                        done,
                        present,
                        expected,
                        read.invalid_days,
                        fee,
                        counted,
                        expected - len(asked),
                    )
                )
            log.info(
                "explore_window",
                market=market,
                symbol=symbol,
                window=segment.name,
                minutes=len(read.minutes),
            )
    return [
        Evaluation(
            f.family,
            versions[f.family],
            f.params.as_json(),
            partition,
            tuple(chosen),
            tuple(pieces[f.family]),
            universe,
            decisions,
        )
        for f in fichas
    ]


def write_record(evaluation: Evaluation, folder: Path) -> Path:
    """The full record of the evaluation, outside Git: one line per run with its signals,
    decisions and trades, and the fees read."""
    target = folder / evaluation.name
    target.mkdir(parents=True, exist_ok=True)
    fees = {(p.market, p.symbol): p.fee for p in evaluation.pieces}
    fees_text = json.dumps([fees[i].as_json() for i in sorted(fees)], ensure_ascii=False, indent=1)
    (target / "comisiones.json").write_text(fees_text + "\n", encoding="utf-8", newline="\n")
    with (target / "registro.jsonl").open("w", encoding="utf-8", newline="\n") as file:
        for piece in evaluation.pieces:
            line = {
                "ventana": piece.segment,
                "mercado": piece.market,
                "simbolo": piece.symbol,
                "presentes": piece.present,
                "minutos": piece.expected,
                "cobertura": {
                    "numerador": piece.coverage.numerator,
                    "denominador": piece.coverage.denominator,
                    "fuera_de_lo_solicitado": piece.outside,
                },
                "dias_no_validos": piece.invalid_days,
                "ejecucion": asdict(piece.run) if piece.run is not None else None,
            }
            file.write(json.dumps(line, ensure_ascii=False, default=_plain) + "\n")
    return target


def _plain(value: object) -> object:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return to_canonical(value)
    raise TypeError(f"not in the record: {type(value).__name__}")


async def _fees(instruments: Sequence[tuple[str, str]]) -> dict[tuple[str, str], Fee]:
    async with KucoinRestClient() as client:
        return await read_fees(client, instruments)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The exploratory evaluation of F3-E5a")
    parser.add_argument("--familia", choices=[*FAMILIES, ALL], required=True)
    parser.add_argument("--particion", choices=[DEVELOPMENT, VALIDATION], required=True)
    parser.add_argument("--carpeta", type=Path, required=True, help="the history of F3-E1")
    parser.add_argument("--universo", type=Path, default=UNIVERSE)
    parser.add_argument("--repositorio", type=Path, default=Path())
    parser.add_argument("--salida", type=Path, default=REPORTS)
    parser.add_argument("--decisiones", default=NO_DECISIONS)
    arguments = parser.parse_args(argv)
    if arguments.particion == VALIDATION and arguments.familia == ALL:
        parser.error("la validación se consulta familia a familia")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
    try:
        commit = repository_commit(arguments.repositorio)
    except NoVersion as error:
        print(f"sin versión no hay informe: {error}", file=sys.stderr)
        return 2
    names = list(FAMILIES) if arguments.familia == ALL else [arguments.familia]
    fichas = [FAMILIES[name]() for name in names]
    universe = load(arguments.universo)
    instruments = [(m.market, m.symbol) for m in universe.instruments]
    # The evidence of eligibility of F3-E1: the opening of each Futures contract, what the
    # recorder saw and the announcements (§12.1, point 2).
    opened = load_contracts(arguments.carpeta)
    evidence = {
        (market, symbol): eligibility_evidence(
            universe, market, symbol, first_open=opened, until=HISTORY_END
        )
        for market, symbol in instruments
    }
    fees = asyncio.run(_fees(instruments))
    evaluations = evaluate(
        fichas,
        commit,
        arguments.particion,
        arguments.carpeta,
        instruments,
        fees,
        universe=arguments.universo.as_posix(),
        decisions=arguments.decisiones,
        evidence=evidence,
        copy=arguments.repositorio / COPY,
    )
    for evaluation in evaluations:
        record = write_record(evaluation, arguments.carpeta / "informes")
        markdown, _figures = report.write(evaluation, arguments.salida, record)
        log.info("explore_report", family=evaluation.family, report=str(markdown))
    return 0


if __name__ == "__main__":
    sys.exit(main())
