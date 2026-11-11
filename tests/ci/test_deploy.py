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
PYTHON_UNITS = [
    u for u in UNITS if u.suffix == ".service" and u.name != "kbot-metadata-guard.service"
]
SCRIPTS = sorted((DEPLOY / "install").glob("*.sh"))
INTERPRETER = "/opt/kbot/.venv/bin/python"  # exists only on the server


def tool(name: str) -> str:
    path = shutil.which(name) or shutil.which(name, path="/usr/sbin:/sbin")
    if path is None:
        if os.environ.get("KBOT_CI"):
            pytest.fail(f"{name} is required in the CI")
        pytest.skip(f"{name} not installed")
    return path


def run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def as_root(*command: str) -> tuple[str, ...]:
    """The command as root when the runner is not: nft needs the kernel even to check."""
    if os.geteuid() == 0 or shutil.which("sudo") is None:
        return command
    return ("sudo", "-n", *command)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_install_scripts_pass_shellcheck(script: Path) -> None:
    bash = tool("bash")
    syntax = run(bash, "-n", str(script))
    assert syntax.returncode == 0, syntax.stderr
    shellcheck = tool("shellcheck")
    done = run(shellcheck, "--severity=style", "--shell=bash", str(script))
    assert done.returncode == 0, done.stdout


def test_the_system_script_never_verifies_units_before_their_interpreter_exists() -> None:
    """Review of E4a, E4a-02: 01 installs the units, 03 verifies them with the venv."""
    system = (DEPLOY / "install" / "01_sistema.sh").read_text(encoding="utf-8")
    code = (DEPLOY / "install" / "03_codigo.sh").read_text(encoding="utf-8")
    assert "systemd-analyze verify" not in system
    assert "systemd-analyze verify" in code
    # E4a-01: the guard never goes through /etc/nftables.conf (Ubuntu's flushes everything).
    assert "/etc/nftables.conf" not in system
    assert "kbot-metadata-guard" in system
    # E4a-03: no data volume means no installation, unless a test instance says so.
    assert "KBOT_ALLOW_BOOT_VOLUME" in system
    assert "findmnt -no LABEL" in system


def test_units_pass_systemd_analyze(tmp_path: Path) -> None:
    """The units, with the interpreter of the server replaced by one of the runner: the
    verification checks that every ExecStart exists, and only the server has the venv."""
    analyze = tool("systemd-analyze")
    python = shutil.which("python3") or "/usr/bin/python3"
    nft = shutil.which("nft") or shutil.which("nft", path="/usr/sbin:/sbin") or "/usr/sbin/nft"
    copies = []
    for unit in UNITS:
        copy = tmp_path / unit.name
        text = unit.read_text(encoding="utf-8").replace(INTERPRETER, python)
        copy.write_text(text.replace("/usr/sbin/nft", nft), encoding="utf-8")
        copies.append(str(copy))
    done = run(analyze, "verify", "--man=no", "--generators=no", *copies)
    # Missing dependencies of the host (postgresql.service) are warnings on stderr; only
    # a non-zero exit is a real error.
    assert done.returncode == 0, done.stderr


def test_units_keep_the_isolation_of_the_design() -> None:
    for unit in PYTHON_UNITS:
        text = unit.read_text(encoding="utf-8")
        assert "NoNewPrivileges=yes" in text, unit.name
        assert "ProtectSystem=strict" in text, unit.name
        assert "CapabilityBoundingSet=" in text, unit.name
        assert INTERPRETER in text, unit.name
        if unit.name != "kbot-guardcheck@.service":
            assert "IPAddressDeny=" in text, unit.name
    ingest = (DEPLOY / "systemd" / "svc-ingest.service").read_text(encoding="utf-8")
    assert "169.254.0.0/16" in ingest  # the instance metadata (E4-D04)
    assert "ReadWritePaths=/run/kbot/kucoin-rest.lock" in ingest  # E4-D06
    assert "Environment=KBOT_REST_LOCK=/run/kbot/kucoin-rest.lock" in ingest
    # E4a-04: the guard check must not block anything itself, or it proves nothing.
    guardcheck = (DEPLOY / "systemd" / "kbot-guardcheck@.service").read_text(encoding="utf-8")
    directives = [line for line in guardcheck.splitlines() if not line.startswith("#")]
    assert not any(line.startswith("IPAddressDeny") for line in directives)
    assert "netcheck guard" in guardcheck
    netcheck = (DEPLOY / "systemd" / "kbot-netcheck@.service").read_text(encoding="utf-8")
    assert "IPAddressDeny=any" in netcheck and "netcheck b19" in netcheck
    guard = (DEPLOY / "systemd" / "kbot-metadata-guard.service").read_text(encoding="utf-8")
    assert "After=ufw.service" in guard and "RemainAfterExit=yes" in guard


def test_sshd_drop_in_is_valid(tmp_path: Path) -> None:
    """``sshd -t`` with the drop-in as the whole configuration and a throwaway host key:
    the daemon refuses to start without one, even to test."""
    sshd = tool("sshd")
    keygen = tool("ssh-keygen")
    host_key = tmp_path / "host_ed25519"
    made = run(keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(host_key))
    assert made.returncode == 0, made.stderr
    drop_in = DEPLOY / "etc" / "ssh" / "sshd_config.d" / "10-kbot.conf"
    done = run(sshd, "-t", "-f", str(drop_in), "-h", str(host_key))
    assert done.returncode == 0, done.stderr
    effective = run(sshd, "-T", "-f", str(drop_in), "-h", str(host_key)).stdout
    assert "passwordauthentication no" in effective
    assert "permitrootlogin no" in effective
    assert "disableforwarding yes" in effective
    assert "authenticationmethods publickey" in effective


def test_metadata_guard_is_valid_nftables_and_manages_only_its_table() -> None:
    guard = DEPLOY / "etc" / "kbot" / "metadata-guard.nft"
    text = guard.read_text(encoding="utf-8")
    assert "flush ruleset" not in text  # E4a-01: never the whole ruleset
    assert text.count("table inet kbot_guard") >= 2 and "delete table inet kbot_guard" in text
    nft = tool("nft")
    done = run(*as_root(nft, "-c", "-f", str(guard)))
    assert done.returncode == 0, done.stderr


def test_uv_checksum_is_pinned_for_the_server() -> None:
    """Review of E4a, E4a-07: the checksum of the pinned uv tarball lives in the repository."""
    text = (DEPLOY / "install" / "uv.sha256").read_text(encoding="utf-8")
    digest, _, name = text.split()[0], None, text.split()[-1]
    assert len(digest) == 64 and int(digest, 16)
    assert name == "uv-aarch64-unknown-linux-gnu.tar.gz"
    code = (DEPLOY / "install" / "03_codigo.sh").read_text(encoding="utf-8")
    assert "uv.sha256" in code and "KBOT_UV_SHA256" not in code


def test_tmpfiles_creates_the_shared_lock_owned_by_root() -> None:
    text = (DEPLOY / "etc" / "tmpfiles.d" / "kbot.conf").read_text(encoding="utf-8")
    assert "d /run/kbot 0750 root kbot" in text
    assert "f /run/kbot/kucoin-rest.lock 0660 root kbot" in text
