"""``strategy_version`` (02 §12.3.3): the hashes of the parameters of the three approved
fichas, the text of a version, and no version with uncommitted changes."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from kbot.core.jcs import canonicalize
from kbot.strategies.breakout import Breakout, BreakoutParams
from kbot.strategies.mean_reversion import MeanReversion
from kbot.strategies.trend import Trend, TrendParams
from kbot.strategies.version import (
    NoVersion,
    params_hash,
    repository_commit,
    strategy_version,
)

APPROVED = {  # 02 §12.3.4 to §12.3.6, approved by the owner on 20 November 2026
    "tendencia": "8866b51449f119568d5012acb47487d37d411b66fda8d615661564b144fa36d3",
    "breakout": "eac620fee86fed9ae4e3f5eeee8ded14986311445ccd71843bf5597d6ae94917",
    "reversion_media": "adee5c8a2a900e797bf62abbc22aef3f441de36ffd79e4425f72a7087bad63d1",
}
REGIME = (
    '"regimen":{"adx":14,"adx_rango":"20","adx_tendencia":"25","atr":14,"bollinger":20,'
    '"bollinger_k":"2","compresion_menor_que":"80","expansion_mayor_que":"80","ventana":100}'
)


def test_the_hashes_of_the_three_fichas_are_the_approved_ones() -> None:
    for ficha in (Trend(), Breakout(), MeanReversion()):
        assert ficha.params_hash() == APPROVED[ficha.family]


def test_the_canonical_parameters_of_the_trend() -> None:
    text = canonicalize(TrendParams().as_json()).decode("utf-8")
    assert text == (
        '{"arrastre":{"multiplo_atr":"3"},"contexto":{"ema_lenta":50,"ema_rapida":20},'
        '"ejecucion":{"velas":4},"familia":"tendencia",'
        + REGIME
        + ',"senal":{"atr":14,"ema_lenta":50,"ema_rapida":20,"macd":[12,26,9],"retroceso":5},'
        '"stop":{"multiplo_atr":"2"},'
        '"temporalidades":{"contexto":"4h","ejecucion":"15m","senal":"1h"}}'
    )


def test_any_parameter_changes_the_hash() -> None:
    approved = BreakoutParams()
    changed = replace(approved, relative_volume_min=Decimal("1.6"))
    assert params_hash(changed.as_json()) != params_hash(approved.as_json())


def test_the_text_of_a_version() -> None:
    commit = "0123456789abcdef0123456789abcdef01234567"
    params = TrendParams().as_json()
    assert strategy_version(commit, params) == f"{commit}+{APPROVED['tendencia']}"
    for wrong in (commit[:39], commit.upper(), commit[:39] + "g"):
        with pytest.raises(ValueError, match="40 lowercase"):
            strategy_version(wrong, params)


def git(repository: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    assert executable is not None, "the tests of the version need git"
    done = subprocess.run(
        [executable, "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_the_commit_of_a_repository_and_no_version_with_uncommitted_changes(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init", "--quiet")
    (tmp_path / "ficha.py").write_text("A = 1\n", encoding="utf-8")
    git(tmp_path, "add", "ficha.py")
    git(
        tmp_path,
        "-c",
        "user.name=kbot",
        "-c",
        "user.email=kbot@example.invalid",
        "commit",
        "-q",
        "-m",
        "x",
    )
    head = git(tmp_path, "rev-parse", "HEAD")
    assert repository_commit(tmp_path) == head
    (tmp_path / "notas.txt").write_text("not followed by git\n", encoding="utf-8")
    assert repository_commit(tmp_path) == head  # an untracked file is no change
    (tmp_path / "ficha.py").write_text("A = 2\n", encoding="utf-8")
    with pytest.raises(NoVersion, match="uncommitted"):
        repository_commit(tmp_path)


def test_no_version_without_a_commit(tmp_path: Path) -> None:
    git(tmp_path, "init", "--quiet")
    with pytest.raises(NoVersion):
        repository_commit(tmp_path)
