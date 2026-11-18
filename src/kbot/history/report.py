"""The coverage report of the history, built from the files alone (``02`` §12.1, point 2;
F3-A1: the report matches what was downloaded).

Per instrument and day: the minutes of the window by presence and eligibility, the failed
stretches, the contradictions and the coverage, with the numerator and the denominator
apart. Every candle of the history is only REST, so the share of results based only on REST
is 100 %; and the survivorship bias that remains is declared.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from kbot.history.coverage import (
    Coverage,
    Eligibility,
    EligibilitySpan,
    Presence,
    coverage,
    presence,
)
from kbot.history.store import read_candles, read_record

HEADER = (
    "- **Todas las velas son solo REST** (`vela_rest_suficiente`): el porcentaje de "
    "resultados basados solo en REST es del 100 %.",
    "- **Cobertura** = minutos presentes de E / minutos de E, con E los minutos de la "
    "ventana cuya elegibilidad no es `no_elegible`; «no aplicable» si E está vacío.",
    "- **Sesgo de supervivencia:** el universo es el del grabador en esa fecha; los "
    "instrumentos que ya no cotizan y cuyas velas KuCoin no sirve no están.",
)
COLUMNS = (
    "Mercado",
    "Símbolo",
    "Días",
    "Minutos",
    "Presentes",
    "Ausentes",
    "No descargados",
    "Elegibilidad desconocida",
    "No elegibles",
    "Contradicciones",
    "Cobertura",
)


@dataclass
class InstrumentReport:
    market: str
    symbol: str
    days: list[tuple[date, Coverage]] = field(default_factory=list)
    missing_records: list[date] = field(default_factory=list)  # files without a valid record

    @property
    def total(self) -> Coverage:
        total = Coverage()
        for _day, part in self.days:
            for key, count in part.counts.items():
                total.counts[key] = total.counts.get(key, 0) + count
            total.contradictions += part.contradictions
            total.failed += part.failed
        return total


def instrument_report(
    root: Path, market: str, symbol: str, evidence: Sequence[EligibilitySpan]
) -> InstrumentReport:
    report = InstrumentReport(market, symbol)
    folder = root / market / symbol
    for parquet_file in sorted(folder.glob("*.parquet")) if folder.exists() else []:
        day = date.fromisoformat(parquet_file.stem)
        record = read_record(root, market, symbol, day)
        if record is None:
            report.missing_records.append(day)
            continue
        present = [c.open_time for c in read_candles(root, market, symbol, day)]
        report.days.append(
            (day, coverage(presence(record.window, record.requests, present), evidence))
        )
    return report


def write(
    reports: Iterable[InstrumentReport],
    markdown: Path,
    data: Path,
    *,
    universe_day: date,
    generated_at: datetime,
) -> None:
    rows = list(reports)
    lines = [
        f"# Cobertura del histórico (universo del {universe_day.isoformat()})",
        "",
        f"Generado el {generated_at.isoformat()} a partir de los archivos (F3-A1).",
        "",
        *HEADER,
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "|" + "---|" * len(COLUMNS),
    ]
    out: list[dict[str, Any]] = []
    for report in rows:
        total = report.total
        minutes = sum(total.counts.values())
        unknown = sum(n for (_p, e), n in total.counts.items() if e is Eligibility.DESCONOCIDA)
        not_eligible = sum(n for (_p, e), n in total.counts.items() if e is Eligibility.NO_ELEGIBLE)
        ratio = total.ratio
        shown = "no aplicable" if ratio is None else f"{ratio:.4%}"
        lines.append(
            f"| {report.market} | {report.symbol} | {len(report.days)} | {minutes} "
            f"| {total.total(Presence.PRESENTE)} | {total.total(Presence.AUSENTE)} "
            f"| {total.total(Presence.NO_DESCARGADO)} | {unknown} | {not_eligible} "
            f"| {len(total.contradictions)} | {shown} |"
        )
        out.append(
            {
                "mercado": report.market,
                "simbolo": report.symbol,
                "numerador": total.numerator,
                "denominador": total.denominator,
                "cobertura": ratio,
                "contradicciones": [m.isoformat() for m in total.contradictions],
                "tramos_fallidos": [[s.isoformat(), e.isoformat()] for s, e in total.failed],
                "sin_registro_valido": [d.isoformat() for d in report.missing_records],
                "por_dia": [
                    {
                        "dia": day.isoformat(),
                        "numerador": part.numerator,
                        "denominador": part.denominator,
                        "presencia": {
                            f"{p.value}/{e.value}": n for (p, e), n in sorted(part.counts.items())
                        },
                    }
                    for day, part in report.days
                ],
            }
        )
    markdown.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    data.write_text(
        json.dumps(
            {
                "universo": universe_day.isoformat(),
                "generado": generated_at.isoformat(),
                "instrumentos": out,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
