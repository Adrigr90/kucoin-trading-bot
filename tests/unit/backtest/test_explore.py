"""The exploratory evaluation over a small synthetic history: two instruments and a few
days (``02`` §12.5.6; F3-A11)."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from kbot.backtest import report
from kbot.backtest.explore import evaluate, main, write_record
from kbot.backtest.fees import Fee
from kbot.backtest.partitions import (
    DEVELOPMENT,
    HISTORY_END,
    TEST,
    VALIDATION,
    SealedPartition,
    segment,
)
from kbot.backtest.position import ExitReason
from kbot.backtest.reader import Read, read_minutes
from kbot.backtest.report import FIRST_LINE, Evaluation
from kbot.backtest.reservation import COMPLETED, FAILED, STARTED, AlreadyReserved
from kbot.history.coverage import EligibilitySpan
from kbot.market.last_candles import Ohlcv
from kbot.market.timeutil import EPOCH
from kbot.strategies.ficha import Ficha
from kbot.strategies.rules import LONG
from kbot.strategies.trend import Trend, TrendParams
from tests.unit.backtest.history import DAY, fee, write_days, write_history
from tests.unit.backtest.support import Fixed, at, decision, flat
from tests.unit.strategies.support import DAYS, MINUTES, START, short_fichas, walk_minutes

COMMIT = "c" * 40
INSTRUMENTS = [("FUTURES", "ETHUSDTM"), ("SPOT", "BTC-USDT")]
FEES = {
    ("FUTURES", "ETHUSDTM"): fee("FUTURES", "ETHUSDTM", "0.0006"),
    ("SPOT", "BTC-USDT"): fee("SPOT", "BTC-USDT", "0.001"),
}
UNIVERSE = "docs/f3/universo_prueba.json"
# Two windows inside the development, each warmed up with the day before it.
WINDOWS = (
    segment("tramo 1", START + DAY, START + 2 * DAY, warmup=DAY),
    segment("tramo 2", START + 2 * DAY, START + 4 * DAY, warmup=DAY),
)
SECTIONS = (
    "## Familia, versión y ficha",
    "## Periodo, particiones y decisiones",
    "## Universo",
    "## Cobertura del histórico",
    "## Porcentaje solo REST",
    "## Modelo de ejecución y costes",
    "## Resultados",
    "### En total",
    "### Por ventana",
    "### Por instrumento",
    "## Datos para D15",
    "## Registro completo",
)


@pytest.fixture(scope="module")
def history(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("historico")
    spot = walk_minutes(DAYS * 1440, seed=5, market="SPOT", symbol="BTC-USDT", price=200.0)
    write_history(root, [*MINUTES, *spot])
    return root


def explore(history: Path, fichas: list[Ficha] | None = None, **options: Any) -> list[Evaluation]:
    options.setdefault("windows", WINDOWS)
    return evaluate(
        fichas or short_fichas(),
        COMMIT,
        options.pop("partition", DEVELOPMENT),
        history,
        options.pop("instruments", INSTRUMENTS),
        options.pop("fees", FEES),
        universe=UNIVERSE,
        **options,
    )


@pytest.fixture(scope="module")
def evaluations(history: Path) -> list[Evaluation]:
    return explore(history)


def test_the_report_has_every_section_and_its_first_line(
    evaluations: list[Evaluation], tmp_path: Path
) -> None:
    assert [e.family for e in evaluations] == ["tendencia", "breakout", "reversion_media"]
    trades = 0
    for evaluation in evaluations:
        markdown, figures = report.write(evaluation, tmp_path, tmp_path / "registro")
        text = markdown.read_text(encoding="utf-8")
        assert text.splitlines()[0] == FIRST_LINE
        for section in SECTIONS:
            assert section in text, section
        for words in ("Sesgo de supervivencia", "Sin funding", evaluation.version, UNIVERSE):
            assert words in text
        data = json.loads(figures.read_text(encoding="utf-8"))
        assert data["aviso"] == FIRST_LINE
        assert data["particiones_consultadas"] == [DEVELOPMENT]
        assert list(data["por_tramo"]) == ["tramo 1", "tramo 2"]
        assert list(data["por_instrumento"]) == ["FUTURES ETHUSDTM", "SPOT BTC-USDT"]
        total = data["total"]
        counts = total["recuentos"]
        assert counts["decisiones"] == counts["ejecutadas"] + sum(counts["no_ejecutadas"].values())
        assert counts["operaciones"] == counts["ejecutadas"] == sum(total["salidas"].values())
        assert all(r["operaciones"] == counts["operaciones"] for r in total["resultados"].values())
        rest = total["solo_rest"]["resultados"]
        assert rest["minutos"] == rest["solo_rest"]  # the history is all only REST
        # Spot only goes long.
        assert data["por_instrumento"]["SPOT BTC-USDT"]["recuentos"]["senales"]["cortos"] == 0
        coverage = {(c["mercado"], c["simbolo"]): c for c in data["cobertura"]}
        btc = coverage[("SPOT", "BTC-USDT")]
        # Every minute asked and present; without evidence, of unknown eligibility: in E.
        assert btc["numerador"] == btc["denominador"] == btc["minutos"] == 3 * 1440
        assert btc["presentes_sobre_el_calendario"] == 3 * 1440
        assert (btc["cobertura"], btc["fuera_de_lo_solicitado"]) == (1.0, 0)
        trades += counts["operaciones"]
    assert trades > 0, "the synthetic history must give some trades"


def test_the_same_input_gives_the_same_report(
    history: Path, evaluations: list[Evaluation], tmp_path: Path
) -> None:
    again = explore(history)
    for first, second in zip(evaluations, again, strict=True):
        one = report.write(first, tmp_path / "uno", tmp_path / "registro")
        two = report.write(second, tmp_path / "dos", tmp_path / "registro")
        for a, b in zip(one, two, strict=True):
            assert a.read_bytes() == b.read_bytes()
        records = [
            write_record(e, tmp_path / name) / "registro.jsonl"
            for e, name in ((first, "r1"), (second, "r2"))
        ]
        assert records[0].read_bytes() == records[1].read_bytes()


def test_each_window_is_warmed_up_and_ends_without_positions(history: Path) -> None:
    calls: list[tuple[str, str, datetime, datetime]] = []

    def reader(root: Path, market: str, symbol: str, start: datetime, end: datetime) -> Read:
        calls.append((market, symbol, start, end))
        return read_minutes(root, market, symbol, start, end)

    (evaluation,) = explore(history, short_fichas()[:1], reader=reader)
    assert calls == [
        (market, symbol, w.warmup_from, w.window.end)
        for market, symbol in sorted(INSTRUMENTS)
        for w in WINDOWS
    ]
    assert [w.warmup_from for w in WINDOWS] == [START, START + DAY]
    windows = {w.name: w.window for w in WINDOWS}
    for piece in evaluation.pieces:
        assert piece.run is not None
        window = windows[piece.segment]
        assert piece.run.window == window
        assert piece.run.costs == piece.fee.costs()
        for trade in piece.run.trades:
            assert window.start <= trade.entry_minute <= trade.exit_minute < window.end


def test_a_position_open_at_the_end_of_a_window_is_liquidated(tmp_path: Path) -> None:
    write_history(tmp_path, flat(at(0), 1440))
    chosen = decision("tendencia", LONG, at(11, 50, 15), stop=90.0)
    windows = (segment("tramo", at(0), at(12), warmup=timedelta(0)),)
    instruments = [("FUTURES", "XUSDTM")]
    fees = {instruments[0]: fee("FUTURES", "XUSDTM", "0.0006")}
    (evaluation,) = explore(
        tmp_path, [Fixed(chosen)], windows=windows, instruments=instruments, fees=fees
    )
    ((piece),) = evaluation.pieces
    assert piece.run is not None
    (trade,) = piece.run.trades
    assert trade.entry_minute == at(11, 51)
    assert (trade.exit_reason, trade.exit_minute) == (ExitReason.LIQUIDATION, at(11, 59))


def test_the_test_partition_is_never_read(history: Path) -> None:
    calls: list[object] = []

    def reader(*arguments: object) -> Read:
        calls.append(arguments)
        return Read([], ())

    with pytest.raises(SealedPartition):
        explore(history, partition=TEST, windows=None, reader=reader)
    assert calls == []


def validation(tmp_path: Path, ficha: Ficha, reader: Any) -> list[Evaluation]:
    return explore(
        tmp_path,
        [ficha],
        partition=VALIDATION,
        windows=None,
        instruments=INSTRUMENTS,
        reader=reader,
        now=lambda: datetime(2026, 11, 23, 10, 0, tzinfo=START.tzinfo),
        copy=tmp_path / "consultas_validacion.json",
    )


def reservations(tmp_path: Path) -> dict[str, dict[str, Any]]:
    return {
        p.name: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((tmp_path / "validacion").glob("*.json"))
    }


def test_the_validation_is_reserved_before_reading_and_consulted_once(tmp_path: Path) -> None:
    calls: list[str] = []

    def reader(root: Path, market: str, symbol: str, start: datetime, end: datetime) -> Read:
        (held,) = reservations(root).values()
        assert held["estado"] == STARTED  # reserved before the first minute is read
        calls.append(symbol)
        return Read([], ())

    (evaluation,) = validation(tmp_path, Trend(), reader)
    assert calls == ["ETHUSDTM", "BTC-USDT"]
    (held,) = reservations(tmp_path).values()
    assert held["estado"] == COMPLETED
    assert held["resultado"]["informe"] == evaluation.name
    copy = json.loads((tmp_path / "consultas_validacion.json").read_text(encoding="utf-8"))
    assert [(e["familia"], e["estado"]) for e in copy["consultas"]] == [("tendencia", COMPLETED)]
    with pytest.raises(AlreadyReserved):
        validation(tmp_path, Trend(), reader)
    assert calls == ["ETHUSDTM", "BTC-USDT"]
    # A new version, with another parameter, is another query.
    calls.clear()
    (other,) = validation(
        tmp_path, Trend(TrendParams(stop_atr=Decimal("3"))), lambda *a: Read([], ())
    )
    assert other.version != evaluation.version
    assert len(reservations(tmp_path)) == 2


def test_f3e5_01_a_failure_after_reading_the_validation_blocks_another_query(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def failing(root: Path, market: str, symbol: str, start: datetime, end: datetime) -> Read:
        calls.append(symbol)
        if len(calls) == 2:
            raise RuntimeError("disco")
        return Read([], ())

    with pytest.raises(RuntimeError, match="disco"):
        validation(tmp_path, Trend(), failing)
    (held,) = reservations(tmp_path).values()
    assert (held["estado"], held["error"]) == (FAILED, "RuntimeError: disco")
    copy = json.loads((tmp_path / "consultas_validacion.json").read_text(encoding="utf-8"))
    assert [e["estado"] for e in copy["consultas"]] == [FAILED]
    with pytest.raises(AlreadyReserved):
        validation(tmp_path, Trend(), failing)
    assert calls == ["ETHUSDTM", "BTC-USDT"]


def test_the_validation_is_consulted_one_family_at_a_time(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="one family at a time"):
        explore(tmp_path, short_fichas(), partition=VALIDATION, windows=None)
    assert not (tmp_path / "validacion").exists()
    with pytest.raises(SystemExit):
        main(["--familia", "todas", "--particion", "validacion", "--carpeta", str(tmp_path)])


def git(repository: Path, *arguments: str) -> None:
    executable = shutil.which("git")
    assert executable is not None, "the tests of the version need git"
    subprocess.run([executable, "-C", str(repository), *arguments], check=True, capture_output=True)


def test_without_a_version_there_is_no_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = tmp_path / "repositorio"
    repository.mkdir()
    git(repository, "init", "--quiet")
    (repository / "ficha.py").write_text("PARAMETRO = 1\n", encoding="utf-8")
    git(repository, "add", "ficha.py")
    git(repository, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "uno")
    (repository / "ficha.py").write_text("PARAMETRO = 2\n", encoding="utf-8")
    out = tmp_path / "informes"
    arguments = ["--familia", "tendencia", "--particion", "desarrollo", "--carpeta", str(tmp_path)]
    assert main([*arguments, "--repositorio", str(repository), "--salida", str(out)]) == 2
    assert "sin versión" in capsys.readouterr().err
    assert not out.exists()


def test_a_missing_fee_stops_before_reading(history: Path) -> None:
    fees: dict[tuple[str, str], Fee] = {("SPOT", "BTC-USDT"): FEES[("SPOT", "BTC-USDT")]}
    with pytest.raises(ValueError, match="no fee"):
        explore(history, fees=fees, reader=lambda *a: pytest.fail("read without a fee"))


def coverage_row(evaluation: Evaluation) -> dict[str, Any]:
    (row,) = report.content(evaluation)["cobertura"]
    return row


def test_f3e5_05_an_instrument_opened_after_the_window_has_no_applicable_coverage(
    tmp_path: Path,
) -> None:
    """Like SOXLUSDTM in the development: asked and answered without candles, and not
    eligible until it opens, after the window. The day before what was asked is outside
    the history: it counts nowhere."""
    first, late = datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 1, 20, tzinfo=UTC)
    instrument = ("FUTURES", "TARDEUSDTM")
    write_days(tmp_path, {(*instrument, (first + i * DAY).date()): [] for i in range(2)})
    spans = [
        EligibilitySpan(EPOCH, late, False, "firstOpenDate"),
        EligibilitySpan(late, HISTORY_END, True, "firstOpenDate"),
    ]
    (evaluation,) = explore(
        tmp_path,
        [Trend()],
        windows=(segment("tramo", first - DAY, first + 2 * DAY, warmup=timedelta(0)),),
        instruments=[instrument],
        fees={instrument: fee(*instrument, "0.0006")},
        evidence={instrument: spans},
    )
    row = coverage_row(evaluation)
    assert row["cobertura"] is None
    assert (row["minutos"], row["fuera_de_lo_solicitado"], row["no_elegibles"]) == (
        3 * 1440,
        1440,
        2 * 1440,
    )
    assert (row["denominador"], row["numerador"], row["presentes_sobre_el_calendario"]) == (0, 0, 0)
    text = report.render(report.content(evaluation))
    assert (
        "| FUTURES TARDEUSDTM | 4320 | 1440 | 2880 | 0 | 0 | 0 | 0 | 0 | 0 | no aplicable |" in text
    )


def test_f3e5_05_four_eligible_minutes_present_and_one_not_eligible(tmp_path: Path) -> None:
    t0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    minute = timedelta(minutes=1)
    one = Decimal("100")
    candles = [Ohlcv(t0 + i * minute, one, one, one, one, Decimal(1), one) for i in range(1, 5)]
    instrument = ("FUTURES", "NUEVOUSDTM")
    write_days(tmp_path, {(*instrument, t0.date()): candles})
    opened = t0 + minute  # the first minute is not eligible, and KuCoin gave no candle
    spans = [
        EligibilitySpan(EPOCH, opened, False, "firstOpenDate"),
        EligibilitySpan(opened, HISTORY_END, True, "firstOpenDate"),
    ]
    (evaluation,) = explore(
        tmp_path,
        [Trend()],
        windows=(segment("tramo", t0, t0 + 5 * minute, warmup=timedelta(0)),),
        instruments=[instrument],
        fees={instrument: fee(*instrument, "0.0006")},
        evidence={instrument: spans},
    )
    row = coverage_row(evaluation)
    assert (row["numerador"], row["denominador"], row["cobertura"]) == (4, 4, 1.0)
    assert (row["no_elegibles"], row["ausentes"], row["contradicciones"]) == (1, 0, 0)
    assert (row["presentes_sobre_el_calendario"], row["minutos"]) == (4, 5)
