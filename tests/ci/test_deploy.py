"""The files of deploy/ are valid before they reach the server (02 §13.1, point 8).

Each check needs a tool of the Ubuntu runner: shellcheck and bash for the scripts,
systemd-analyze for the units, sshd for the SSH drop-in and nft for the metadata guard.
On the PC (no such tools) the checks are skipped; in the CI (KBOT_CI) they are required.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
UNITS = sorted((DEPLOY / "systemd").glob("*"))
SCRIPTS = sorted((DEPLOY / "install").glob("*.sh"))


def tool(name: str) -> str:
    path = shutil.which(name) or shutil.which(name, path="/usr/sbin:/sbin")
    if path is None:
        if os.environ.get("KBOT_CI"):
            pytest.fail(f"{name} is required in the CI")
        pytest.skip(f"{name} not installed")
    return path


def run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_install_scripts_pass_shellcheck(script: Path) -> None:
    bash = tool("bash")
    syntax = run(bash, "-n", str(script))
    assert syntax.returncode == 0, syntax.stderr
    shellcheck = tool("shellcheck")
    done = run(shellcheck, "--severity=style", "--shell=bash", str(script))
    assert done.returncode == 0, done.stdout


def test_units_pass_systemd_analyze() -> None:
    analyze = tool("systemd-analyze")
    done = run(analyze, "verify", "--man=no", "--generators=no", *(str(u) for u in UNITS))
    # systemd-analyze reports missing dependencies of the host (postgresql.service) as
    # warnings on stderr; only a non-zero exit is a real error.
    assert done.returncode == 0, done.stderr


def test_units_keep_the_isolation_of_the_design() -> None:
    for unit in UNITS:
        if unit.suffix != ".service":
            continue
        text = unit.read_text(encoding="utf-8")
        assert "NoNewPrivileges=yes" in text, unit.name
        assert "ProtectSystem=strict" in text, unit.name
        assert "CapabilityBoundingSet=" in text, unit.name
        assert "IPAddressDeny=" in text, unit.name
    ingest = (DEPLOY / "systemd" / "svc-ingest.service").read_text(encoding="utf-8")
    assert "169.254.0.0/16" in ingest  # the instance metadata (E4-D04)
    assert "ReadWritePaths=/run/kbot/kucoin-rest.lock" in ingest  # E4-D06
    assert "Environment=KBOT_REST_LOCK=/run/kbot/kucoin-rest.lock" in ingest


def test_sshd_drop_in_is_valid() -> None:
    sshd = tool("sshd")
    drop_in = DEPLOY / "etc" / "ssh" / "sshd_config.d" / "10-kbot.conf"
    done = run(sshd, "-t", "-f", str(drop_in))
    assert done.returncode == 0, done.stderr
    effective = run(sshd, "-T", "-f", str(drop_in)).stdout
    assert "passwordauthentication no" in effective
    assert "permitrootlogin no" in effective
    assert "disableforwarding yes" in effective


def test_metadata_guard_is_valid_nftables() -> None:
    nft = tool("nft")
    guard = DEPLOY / "etc" / "kbot" / "metadata-guard.nft"
    done = run(nft, "-c", "-f", str(guard))
    assert done.returncode == 0, done.stderr


def test_tmpfiles_creates_the_shared_lock_owned_by_root() -> None:
    text = (DEPLOY / "etc" / "tmpfiles.d" / "kbot.conf").read_text(encoding="utf-8")
    assert "d /run/kbot 0750 root kbot" in text
    assert "f /run/kbot/kucoin-rest.lock 0660 root kbot" in text
