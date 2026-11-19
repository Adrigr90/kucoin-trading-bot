"""The universe, the evidence of eligibility and the coverage report built from the files
alone (02 §12.1, points 1 and 2; F3-A1: the report matches what was downloaded)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from kbot.history.__main__ import main, save_contracts
from kbot.history.coverage import Eligibility, Request, Window
from kbot.history.report import instrument_report
from kbot.history.store import Conflict, write_day
from kbot.history.universe import evidence, first_open_dates, load
from kbot.market.last_candles import Ohlcv
from kbot.market.timeutil import EPOCH

UNIVERSE = Path(__file__).resolve().parents[3] / "docs" / "f3" / "universo_2026-11-18.json"
START = datetime(2026, 11, 10, tzinfo=UTC)
NOW = START + timedelta(days=3)


def candle(minute: int) -> Ohlcv:
    p = Decimal(100)
    return Ohlcv(START + timedelta(minutes=minute), p, p, p, p, Decimal(1), p)


def test_the_universe_of_the_repository_is_the_recorder_s_twenty_and_twenty() -> None:
    universe = load(UNIVERSE)
    assert universe.day == date(2026, 11, 18)
    markets = [m.market for m in universe.members]
    assert markets.count("SPOT") == 20 and markets.count("FUTURES") == 20
    assert all(m.trading_since is not None for m in universe.members)


def test_the_evidence_comes_from_the_contract_the_recorder_and_announcements() -> None:
    """firstOpenDate (ms, as in the response recorded in E1): not eligible before it,
    eligible from it; the recorder's first observation: eligible from it."""
    universe = load(UNIVERSE)
    opened = first_open_dates([{"symbol": "XBTUSDTM", "firstOpenDate": 1585555200000}])
    assert opened == {"XBTUSDTM": datetime(2020, 3, 30, 8, tzinfo=UTC)}
    spans = evidence(universe, "FUTURES", "XBTUSDTM", first_open=opened, until=NOW)
    assert [(s.start, s.eligible, s.source) for s in spans][:2] == [
        (EPOCH, False, "firstOpenDate"),
        (datetime(2020, 3, 30, 8, tzinfo=UTC), True, "firstOpenDate"),
    ]
    spot = evidence(universe, "SPOT", "BTC-USDT", first_open=opened, until=NOW)
    assert [s.source for s in spot] == ["instruments (grabador)"], "no firstOpenDate in Spot"


def test_the_report_is_built_from_the_files_and_matches_them(tmp_path: Path) -> None:
    """Two days: one whole with a candle missing, one with a failed stretch. The report
    counts what the files hold, with the numerator, the denominator and the failed
    stretches apart."""
    whole = Window(START, START + timedelta(days=1))
    everything = [Request(START, START + timedelta(minutes=1439), True)]
    write_day(
        tmp_path,
        "SPOT",
        "BTC-USDT",
        START.date(),
        whole,
        [candle(m) for m in range(1440) if m != 7],
        everything,
        NOW,
    )
    second = START + timedelta(days=1)
    failed = [
        Request(second, second + timedelta(minutes=99), False),
        Request(second + timedelta(minutes=100), second + timedelta(minutes=1439), True),
    ]
    write_day(
        tmp_path,
        "SPOT",
        "BTC-USDT",
        second.date(),
        Window(second, second + timedelta(days=1)),
        [],
        failed,
        NOW,
    )
    report = instrument_report(tmp_path, "SPOT", "BTC-USDT", [])
    total = report.total
    assert (total.numerator, total.denominator) == (1439, 2880)
    assert total.failed == [(second, second + timedelta(minutes=100))]
    assert {e for (_p, e) in total.counts} == {Eligibility.DESCONOCIDA}


def test_the_report_counts_and_lists_the_minutes_in_conflict(tmp_path: Path) -> None:
    """CD-19 in the report: a column of its own and the list in the data, in the
    denominator and not in the numerator."""
    whole = Window(START, START + timedelta(days=1))
    everything = [Request(START, START + timedelta(minutes=1439), True)]
    seven = candle(7)
    partial = Ohlcv(
        seven.open_time, seven.open, seven.high, seven.low, seven.close, Decimal(2), seven.turnover
    )
    write_day(
        tmp_path,
        "SPOT",
        "DOT-USDT",
        START.date(),
        whole,
        [candle(m) for m in range(1440) if m != 7],
        everything,
        NOW,
        [Conflict(seven.open_time, (seven, partial))],
    )
    save_contracts(tmp_path, {})
    arguments = ["--universo", str(UNIVERSE), "--carpeta", str(tmp_path)]
    assert main(["informe", *arguments, "--solo", "SPOT:DOT-USDT"]) == 0
    data = json.loads((tmp_path / "cobertura.json").read_text(encoding="utf-8"))
    (dot,) = data["instrumentos"]
    assert dot["en_conflicto"] == [seven.open_time.isoformat()]
    assert (dot["numerador"], dot["denominador"]) == (1439, 1440)
    assert dot["por_dia"][0]["presencia"]["en_conflicto/desconocida"] == 1
    markdown = (tmp_path / "informe_cobertura.md").read_text(encoding="utf-8")
    assert "| Presentes | Ausentes | En conflicto | No descargados |" in markdown
    assert "| SPOT | DOT-USDT | 1 | 1440 | 1439 | 0 | 1 | 0 |" in markdown


def test_the_report_command_writes_its_files(tmp_path: Path) -> None:
    whole = Window(START, START + timedelta(days=1))
    everything = [Request(START, START + timedelta(minutes=1439), True)]
    write_day(tmp_path, "SPOT", "BTC-USDT", START.date(), whole, [candle(0)], everything, NOW)
    save_contracts(tmp_path, {})
    assert (
        main(
            [
                "informe",
                "--universo",
                str(UNIVERSE),
                "--carpeta",
                str(tmp_path),
                "--solo",
                "SPOT:BTC-USDT",
            ]
        )
        == 0
    )
    data = json.loads((tmp_path / "cobertura.json").read_text(encoding="utf-8"))
    (btc,) = data["instrumentos"]
    assert (btc["numerador"], btc["denominador"]) == (1, 1440)
    assert "| SPOT | BTC-USDT | 1 | 1440 | 1 |" in (tmp_path / "informe_cobertura.md").read_text(
        encoding="utf-8"
    )
