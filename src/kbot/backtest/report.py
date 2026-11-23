"""The exploratory report of a family (``02`` §12.5.4; F3-A11).

* **One per family and version,** in Markdown, with a JSON next to it that holds all its
  figures. Its first line says it is not the validation of the strategy.
* **Figures,** by instrument, by fold and in total: the counts of signals, decisions,
  expiries and refusals by reason, and trades; by regime, the trades and their mean R; the
  results in R, gross, net and net with the costs doubled; and the exits by reason.
* **The maximum drawdown** (F3E5-02): the curve of cumulative R starts at 0 and moves by the
  minute of exit. The trades that exit in the same minute, of one instrument or of several,
  are added in a single step. The drawdown is the largest fall from an earlier maximum of
  the curve, the initial 0 included. Sums are exactly rounded (``math.fsum``), so neither
  the drawdown nor any sum depends on the order of the trades.
* **Deterministic:** the same evaluation gives the same files, byte for byte.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from kbot.backtest.engine import CHOSEN, EXECUTED, Run
from kbot.backtest.execution import net_r
from kbot.backtest.fees import SLIPPAGE, STRESS, Fee
from kbot.backtest.partitions import DEVELOPMENT, Segment
from kbot.backtest.position import EntryFailure, ExitReason, Trade
from kbot.core.decimal import to_canonical
from kbot.history.coverage import Coverage, Eligibility, Presence
from kbot.strategies.combiner import Refusal
from kbot.strategies.regime import Direction, Volatility
from kbot.strategies.rules import LONG, Outcome, Signal

FORMAT = "kbot.backtest informe exploratorio 1"
FIRST_LINE = (
    "Este informe no es la validación de la estrategia (D16, F8): "
    "sirve para ver si la familia merece seguir."
)
GROSS, NET, STRESSED = "bruto", "neto", "neto_costes_dobles"
EXPIRIES = tuple(o.value for o in Outcome if o is not Outcome.DECISION)
NOT_EXECUTED = (*(r.value for r in Refusal), *(f.value for f in EntryFailure))
EXITS = tuple(reason.value for reason in ExitReason)
NO_LABEL = "sin_etiqueta"
# The eligibility of the minutes of E, the denominator of the coverage (§12.1, point 2).
ELIGIBLE = (Eligibility.ELEGIBLE, Eligibility.DESCONOCIDA)
FICHAS = {"tendencia": "§12.3.4", "breakout": "§12.3.5", "reversion_media": "§12.3.6"}


@dataclass(frozen=True)
class Piece:
    """The run of one family over one instrument and one window of the engine."""

    segment: str
    market: str
    symbol: str
    run: Run | None  # None: there was no minute to run on
    present: int  # minutes present in the window
    expected: int  # minutes of the window
    invalid_days: tuple[tuple[date, str], ...]
    fee: Fee
    # The coverage of the contract over the minutes of the window asked for the instrument,
    # and how many minutes of the window were never asked (F3E5-05).
    coverage: Coverage = field(default_factory=Coverage)
    outside: int = 0

    @property
    def instrument(self) -> str:
        return f"{self.market} {self.symbol}"


@dataclass(frozen=True)
class Evaluation:
    family: str
    version: str  # <commit>+<hash>
    params: Mapping[str, object]
    partition: str
    segments: tuple[Segment, ...]
    pieces: tuple[Piece, ...]
    universe: str  # the file of the universe
    decisions: str  # the decisions taken with the partitions consulted

    @property
    def name(self) -> str:
        commit, params_hash = self.version.split("+")
        return f"{self.family}_{self.partition}_{commit[:7]}_{params_hash[:12]}"


# --- the figures ------------------------------------------------------------------------


def stressed_r(trade: Trade, fee: Fee) -> float:
    """The net R of a trade with the fee and the slippage doubled. The costs never change
    a decision, so only the fills and the fees change."""
    costs, d = fee.costs(STRESS), trade.direction
    entry, exit_ = costs.entry(d, trade.entry_price), costs.exit(d, trade.exit_price)
    return net_r(d, entry, exit_, trade.r, costs.fee)


def max_drawdown(exits: Iterable[tuple[datetime, float]]) -> float:
    """The largest fall of the cumulative R from an earlier maximum, the 0 included, with
    the exits of one minute added in a single step (F3E5-02)."""
    by_minute: dict[datetime, list[float]] = defaultdict(list)
    for minute, r in exits:
        by_minute[minute].append(r)
    level = peak = drawdown = 0.0
    for minute in sorted(by_minute):
        level += math.fsum(by_minute[minute])
        peak = max(peak, level)
        drawdown = max(drawdown, peak - level)
    return drawdown


def summary(exits: Sequence[tuple[datetime, float]]) -> dict[str, Any]:
    values = [r for _, r in exits]
    if not values:
        return {
            "operaciones": 0,
            "positivas": 0,
            "media": None,
            "mediana": None,
            "suma": 0.0,
            "peor": None,
            "mejor": None,
            "caida_maxima": 0.0,
        }
    total = math.fsum(values)
    return {
        "operaciones": len(values),
        "positivas": sum(1 for r in values if r > 0),
        "media": total / len(values),
        "mediana": statistics.median(values),
        "suma": total,
        "peor": min(values),
        "mejor": max(values),
        "caida_maxima": max_drawdown(exits),
    }


def figures(pieces: Sequence[Piece]) -> dict[str, Any]:
    """Every figure of a group of runs."""
    signals = Counter[str]()
    expired = Counter[str]({reason: 0 for reason in EXPIRIES})
    refused = Counter[str]({reason: 0 for reason in NOT_EXECUTED})
    decided = executed = 0
    results: dict[str, list[tuple[datetime, float]]] = {GROSS: [], NET: [], STRESSED: []}
    exits = Counter[str]({reason: 0 for reason in EXITS})
    regimes: dict[str, dict[str, list[Trade]]] = {"contexto": {}, "senal": {}}
    rest = {"decisiones": [0, 0], "resultados": [0, 0]}
    trades = 0
    for piece in pieces:
        if piece.run is None:
            continue
        for signal in piece.run.signals:
            signals["largos" if signal.direction == LONG else "cortos"] += 1
        chosen: dict[tuple[datetime, int], Signal] = {}
        for record in piece.run.decisions:
            if not record.decision.decided:
                expired[record.status] += 1
                continue
            decided += 1
            if record.status != CHOSEN:
                refused[record.status] += 1
            elif record.entry != EXECUTED:
                refused[str(record.entry)] += 1
            else:
                executed += 1
                signal = record.decision.signal
                chosen[(signal.open_time, signal.direction)] = signal
        for trade in piece.run.trades:
            trades += 1
            results[GROSS].append((trade.exit_minute, trade.gross_r))
            results[NET].append((trade.exit_minute, trade.net_r))
            results[STRESSED].append((trade.exit_minute, stressed_r(trade, piece.fee)))
            exits[trade.exit_reason.value] += 1
            signal = chosen[(trade.signal_open, trade.direction)]
            for where, labels in (
                ("contexto", signal.context_regime),
                ("senal", signal.signal_regime),
            ):
                regimes[where].setdefault(_label(labels), []).append(trade)
            for key, (total, only_rest) in (
                ("decisiones", trade.decision_rest_only),
                ("resultados", trade.result_rest_only),
            ):
                rest[key][0] += total
                rest[key][1] += only_rest
    return {
        "recuentos": {
            "senales": {"largos": signals["largos"], "cortos": signals["cortos"]},
            "decisiones": decided,
            "ejecutadas": executed,
            "operaciones": trades,
            "caducadas": dict(expired),
            "no_ejecutadas": dict(refused),
        },
        "resultados": {measure: summary(values) for measure, values in results.items()},
        "regimen": {
            where: {
                label: {
                    "operaciones": len(group),
                    "r_bruto_medio": math.fsum(t.gross_r for t in group) / len(group),
                    "r_neto_medio": math.fsum(t.net_r for t in group) / len(group),
                }
                for label, group in sorted(found.items(), key=lambda item: _label_order(item[0]))
            }
            for where, found in regimes.items()
        },
        "salidas": dict(exits),
        "solo_rest": {
            key: {"minutos": total, "solo_rest": only_rest}
            for key, (total, only_rest) in rest.items()
        },
    }


def _label(labels: tuple[Direction | None, Volatility | None]) -> str:
    trend, volatility = labels
    return f"{trend.value if trend else NO_LABEL}/{volatility.value if volatility else NO_LABEL}"


def _label_order(label: str) -> tuple[int, int]:
    trend, volatility = label.split("/")
    directions = [d.value for d in Direction] + [NO_LABEL]
    volatilities = [v.value for v in Volatility] + [NO_LABEL]
    return directions.index(trend), volatilities.index(volatility)


def content(evaluation: Evaluation) -> dict[str, Any]:
    """The JSON of the report: everything it says, with all its figures."""
    pieces = evaluation.pieces
    instruments = sorted({(p.market, p.symbol) for p in pieces})
    folds = [s.name for s in evaluation.segments]
    by_fold = {name: figures([p for p in pieces if p.segment == name]) for name in folds}
    winning = [name for name in folds if by_fold[name]["resultados"][NET]["suma"] > 0]
    commit, params_hash = evaluation.version.split("+")
    fees = {(p.market, p.symbol): p.fee for p in pieces}
    return {
        "formato": FORMAT,
        "aviso": FIRST_LINE,
        "familia": evaluation.family,
        "version": evaluation.version,
        "commit": commit,
        "hash_parametros": params_hash,
        "ficha": FICHAS.get(evaluation.family),
        "parametros": _plain(evaluation.params),
        "particion": evaluation.partition,
        "particiones_consultadas": _consulted(evaluation.partition),
        "decisiones_tomadas": evaluation.decisions,
        "tramos": [
            {
                "nombre": s.name,
                "desde": s.window.start.isoformat(),
                "hasta": s.window.end.isoformat(),
                "calentamiento_desde": s.warmup_from.isoformat(),
            }
            for s in evaluation.segments
        ],
        "universo": evaluation.universe,
        "costes": {
            "comisiones": [fees[i].as_json() for i in instruments],
            "deslizamiento": format(SLIPPAGE, "f"),
            "prueba_de_esfuerzo": f"comisión y deslizamiento por {STRESS}",
            "funding": "sin funding (§12.4.4)",
        },
        "cobertura": [_coverage(pieces, market, symbol) for market, symbol in instruments],
        "total": figures(pieces),
        "por_tramo": by_fold,
        "tramos_con_r_neto_positivo": winning,
        "por_instrumento": {
            f"{market} {symbol}": figures(
                [p for p in pieces if (p.market, p.symbol) == (market, symbol)]
            )
            for market, symbol in instruments
        },
    }


def _consulted(partition: str) -> list[str]:
    if partition == DEVELOPMENT:
        return [DEVELOPMENT]
    return [partition, f"{DEVELOPMENT}: solo los 30 días que calientan los indicadores"]


def _coverage(pieces: Sequence[Piece], market: str, symbol: str) -> dict[str, Any]:
    """The coverage of the contract (§12.1, point 2; F3E5-05) over the windows: E is the set
    of their minutes asked whose eligibility is not ``no_elegible``, and the coverage is
    its present minutes over its size, «no aplicable» when it is empty. The minutes present
    over the whole calendar of the windows are another measure, named apart."""
    mine = [p for p in pieces if (p.market, p.symbol) == (market, symbol)]
    invalid = sorted({day for p in mine for day in p.invalid_days})
    counts: Counter[tuple[Presence, Eligibility]] = Counter()
    contradictions = 0
    for piece in mine:
        counts.update(piece.coverage.counts)
        contradictions += len(piece.coverage.contradictions)

    def in_e(presence: Presence) -> int:
        return sum(n for (p, e), n in counts.items() if p is presence and e in ELIGIBLE)

    numerator, denominator = (
        in_e(Presence.PRESENTE),
        sum(n for (_p, e), n in counts.items() if e in ELIGIBLE),
    )
    return {
        "mercado": market,
        "simbolo": symbol,
        "minutos": sum(p.expected for p in mine),
        "fuera_de_lo_solicitado": sum(p.outside for p in mine),
        "no_elegibles": sum(n for (_p, e), n in counts.items() if e is Eligibility.NO_ELEGIBLE),
        "elegibilidad_desconocida": sum(
            n for (_p, e), n in counts.items() if e is Eligibility.DESCONOCIDA
        ),
        "denominador": denominator,
        "numerador": numerator,
        "ausentes": in_e(Presence.AUSENTE),
        "en_conflicto": in_e(Presence.EN_CONFLICTO),
        "no_descargados": in_e(Presence.NO_DESCARGADO),
        "contradicciones": contradictions,
        "cobertura": numerator / denominator if denominator else None,
        "presentes_sobre_el_calendario": sum(p.present for p in mine),
        "dias_no_validos": [[day.isoformat(), reason] for day, reason in invalid],
    }


# --- the files --------------------------------------------------------------------------


def write(evaluation: Evaluation, folder: Path, record: Path | None = None) -> tuple[Path, Path]:
    """Write the report and its JSON in ``folder``; ``record`` is where the full record is."""
    data = content(evaluation)
    folder.mkdir(parents=True, exist_ok=True)
    markdown = folder / f"{evaluation.name}.md"
    figures_file = folder / f"{evaluation.name}.json"
    markdown.write_text(render(data, record), encoding="utf-8", newline="\n")
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    figures_file.write_text(text, encoding="utf-8", newline="\n")
    return markdown, figures_file


def render(data: Mapping[str, Any], record: Path | None = None) -> str:
    """The Markdown of the report."""
    family, partition = data["familia"], data["particion"]
    lines = [
        data["aviso"],
        "",
        f"# Informe exploratorio de «{family}» en la partición «{partition}»",
        "",
        "## Familia, versión y ficha",
        "",
        f"- **Familia:** {family}, con la ficha de `02` {data['ficha']}, aprobada.",
        f"- **Versión:** `{data['version']}`.",
        f"  - commit `{data['commit']}`;",
        f"  - hash de los parámetros `{data['hash_parametros']}`.",
        "- **Parámetros** (§12.3.3):",
        "",
        "```json",
        json.dumps(data["parametros"], ensure_ascii=False, indent=1),
        "```",
        "",
        "## Periodo, particiones y decisiones",
        "",
        _table(
            ["Ventana", "Desde", "Hasta", "Calentamiento desde"],
            [
                [s["nombre"], s["desde"], s["hasta"], s["calentamiento_desde"]]
                for s in data["tramos"]
            ],
        ),
        "",
        f"- **Particiones consultadas:** {'; '.join(data['particiones_consultadas'])}.",
        "- **La prueba no se ha leído:** está sellada hasta la F8.",
        f"- **Decisiones tomadas con ellas:** {data['decisiones_tomadas']}",
        "- Cada ventana empieza sin posiciones, y una posición abierta al final se liquida.",
        "",
        "## Universo",
        "",
        f"`{data['universo']}`: {_markets(data['cobertura'])}, los del universo del grabador "
        "el día de esa lista. **Sesgo de supervivencia:** son instrumentos que seguían "
        "cotizando ese día. Los que dejaron de cotizar antes no están, así que los "
        "resultados pueden ser mejores que los de una selección hecha en su día.",
        "",
        "## Cobertura del histórico",
        "",
        "En las ventanas, sin el calentamiento. **Cobertura** es la del contrato (§12.1, "
        "punto 2): los minutos presentes de E entre los de E. E son los minutos pedidos a "
        "KuCoin cuya elegibilidad no es `no_elegible`; los de elegibilidad desconocida "
        "cuentan. Si E está vacío, es «no aplicable». **Fuera de lo solicitado** son "
        "minutos que nunca se pidieron, como los de Futures antes del 1 ene 2025, que no "
        "tienen velas de un minuto: no cuentan en E. **Presentes sobre el calendario** es "
        "otra medida: los presentes entre todos los minutos de las ventanas.",
        "",
        _table(
            [
                "Instrumento",
                "Minutos",
                "Fuera de lo solicitado",
                "No elegibles",
                "E",
                "Presentes en E",
                "Ausentes",
                "En conflicto",
                "No descargados",
                "Contradicciones",
                "Cobertura",
                "Presentes sobre el calendario",
                "Días no válidos",
            ],
            [
                [
                    f"{c['mercado']} {c['simbolo']}",
                    str(c["minutos"]),
                    str(c["fuera_de_lo_solicitado"]),
                    str(c["no_elegibles"]),
                    str(c["denominador"]),
                    str(c["numerador"]),
                    str(c["ausentes"]),
                    str(c["en_conflicto"]),
                    str(c["no_descargados"]),
                    str(c["contradicciones"]),
                    _percent(c["numerador"], c["denominador"], "no aplicable"),
                    _percent(c["presentes_sobre_el_calendario"], c["minutos"]),
                    ", ".join(f"{d} ({why})" for d, why in c["dias_no_validos"]) or "—",
                ]
                for c in data["cobertura"]
            ],
        ),
        "",
        "## Porcentaje solo REST",
        "",
        "Minutos originales de las operaciones, cada uno una vez por operación. En el "
        "histórico todos son solo REST.",
        "",
        _table(
            ["Procedencia", "Minutos", "Solo REST", "Porcentaje"],
            [
                [
                    name,
                    str(v["minutos"]),
                    str(v["solo_rest"]),
                    _percent(v["solo_rest"], v["minutos"]),
                ]
                for name, v in data["total"]["solo_rest"].items()
            ],
        ),
        "",
        "## Modelo de ejecución y costes",
        "",
        "- **Ejecución** (§12.4.4): entrada a mercado en la apertura del primer minuto que "
        "empieza después de la decisión; stop a su nivel, o en la apertura si el minuto ya "
        "abre más allá; objetivo a su nivel, nunca mejor; salidas por cierre, por tiempo y "
        "por el final de la ventana, a mercado. Sin precio observado no hay ejecución.",
        "- **Comisión** por lado, de tomador, la de cada instrumento leída de KuCoin (§12.5.3).",
        f"- **Deslizamiento:** {_fraction(data['costes']['deslizamiento'])} por ejecución.",
        f"- **Prueba de esfuerzo:** {data['costes']['prueba_de_esfuerzo']}.",
        "- **Sin funding** (§12.4.4).",
        "",
        _table(
            ["Instrumento", "Comisión de tomador", "Origen", "Leída"],
            [
                [
                    f"{f['mercado']} {f['simbolo']}",
                    _fraction(f["comision_tomador"]),
                    f["origen"],
                    f["leida"],
                ]
                for f in data["costes"]["comisiones"]
            ],
        ),
        "",
        "## Resultados",
        "",
        "En R, por operación. **Aciertos:** operaciones con R neto > 0. **Caída máxima:** "
        "sobre la curva de R acumulado por minuto de salida, desde 0, con las salidas de un "
        "mismo minuto sumadas en un paso.",
        "",
        "### En total",
        "",
        *_tables([("Total", data["total"])]),
        *_regime(data["total"]["regimen"]),
        "### Por ventana",
        "",
        *_folds(data),
        *_tables(list(data["por_tramo"].items())),
        "### Por instrumento",
        "",
        *_tables(list(data["por_instrumento"].items())),
        "El régimen por ventana y por instrumento está en el JSON.",
        "",
        "## Datos para D15",
        "",
        "Los resultados por volatilidad servirán, si procede, para proponer el filtro de "
        "volatilidad extrema (D15), que decide el propietario.",
        "",
    ]
    if record is not None:
        lines += [
            "## Registro completo",
            "",
            f"Las señales, las decisiones y las operaciones de cada ejecución están en el PC, "
            f"fuera de Git: `{record.as_posix()}`.",
            "",
        ]
    return "\n".join(lines)


def _markets(coverage: Sequence[Mapping[str, Any]]) -> str:
    count = Counter(c["mercado"] for c in coverage)
    return f"{count['SPOT']} instrumentos de Spot y {count['FUTURES']} de Futures"


def _folds(data: Mapping[str, Any]) -> list[str]:
    if data["particion"] != DEVELOPMENT:
        return []
    winning = data["tramos_con_r_neto_positivo"]
    if len(winning) == 1:
        text = f"**Aviso: la familia solo gana en un tramo** ({winning[0]}): R neto sumado > 0."
    elif not winning:
        text = "**La familia no gana en ningún tramo** (R neto sumado ≤ 0 en los cuatro)."
    else:
        text = f"Tramos con R neto sumado > 0: {', '.join(winning)}."
    return [text, ""]


def _tables(groups: Sequence[tuple[str, Mapping[str, Any]]]) -> list[str]:
    counts = [g["recuentos"] for _, g in groups]
    lines = [
        "**Recuentos**",
        "",
        _table(
            [
                "",
                "Señales largas",
                "Señales cortas",
                "Decisiones",
                "Ejecutadas",
                "Operaciones",
                "Caducadas",
                "No ejecutadas",
            ],
            [
                [
                    name,
                    str(c["senales"]["largos"]),
                    str(c["senales"]["cortos"]),
                    str(c["decisiones"]),
                    str(c["ejecutadas"]),
                    str(c["operaciones"]),
                    str(sum(c["caducadas"].values())),
                    str(sum(c["no_ejecutadas"].values())),
                ]
                for (name, _), c in zip(groups, counts, strict=True)
            ],
        ),
        "",
        "**Caducadas y no ejecutadas, por motivo**",
        "",
        _table(
            ["", *EXPIRIES, *NOT_EXECUTED],
            [
                [
                    name,
                    *(str(c["caducadas"][r]) for r in EXPIRIES),
                    *(str(c["no_ejecutadas"][r]) for r in NOT_EXECUTED),
                ]
                for (name, _), c in zip(groups, counts, strict=True)
            ],
        ),
        "",
    ]
    for measure, title in (
        (NET, "R neto"),
        (GROSS, "R bruto"),
        (STRESSED, "R neto con los costes dobles"),
    ):
        lines += [
            f"**{title}**",
            "",
            _table(
                [
                    "",
                    "Operaciones",
                    "Aciertos" if measure == NET else "R > 0",
                    "R medio",
                    "Mediana",
                    "Suma",
                    "Peor",
                    "Mejor",
                    "Caída máxima",
                ],
                [[name, *_result(g["resultados"][measure])] for name, g in groups],
            ),
            "",
        ]
    lines += [
        "**Salidas, por motivo**",
        "",
        _table(
            ["", *EXITS], [[name, *(str(g["salidas"][r]) for r in EXITS)] for name, g in groups]
        ),
        "",
    ]
    return lines


def _regime(regimes: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = []
    for where, title in (("contexto", "vela de contexto"), ("senal", "vela de señal")):
        lines += [
            f"**Por régimen de la {title}** (dirección/volatilidad, §12.3.2)",
            "",
            _table(
                ["Régimen", "Operaciones", "R bruto medio", "R neto medio"],
                [
                    [label, str(v["operaciones"]), _r(v["r_bruto_medio"]), _r(v["r_neto_medio"])]
                    for label, v in regimes[where].items()
                ]
                or [["—", "0", "—", "—"]],
            ),
            "",
        ]
    return lines


def _result(s: Mapping[str, Any]) -> list[str]:
    return [
        str(s["operaciones"]),
        str(s["positivas"]),
        *(_r(s[key]) for key in ("media", "mediana", "suma", "peor", "mejor", "caida_maxima")),
    ]


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _r(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}".replace(".", ",")


def _percent(part: int, whole: int, empty: str = "—") -> str:
    return empty if whole == 0 else f"{100 * part / whole:.2f} %".replace(".", ",")


def _fraction(text: str) -> str:
    """A fraction in canonical text as a percentage, exactly."""
    value = Decimal(text) * 100
    return f"{format(value.normalize(), 'f')} %".replace(".", ",")


def _plain(value: object) -> object:
    """A value of the parameters in JSON: ``Decimal`` as the canonical text of the project
    (§5.7), the one the hash of the parameters uses, so that ``2`` and ``2.00`` give the
    same text and the hash can be rebuilt from the report (F3E5-07)."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, Decimal):
        return to_canonical(value)
    if isinstance(value, int | str | bool) or value is None:
        return value
    raise TypeError(f"not a parameter: {type(value).__name__}")
