"""The automatic check of the hardened server (02 §9 and §13.1; acceptance F2-A10).

Each check reads the state of the server through a command or a file and gives a verdict.
``python -m kbot.ops.check`` runs them all on the server, prints the report and exits
with 1 if any fails. The checks are pure functions of what the commands print, so the
unit tests feed them recorded outputs; the real commands run only on the server.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

Runner = Callable[[list[str]], str]  # a command and its standard output
Reader = Callable[[str], str]  # a path and its text

SERVICE_USERS = ("kbot_ingest", "kbot_archive", "kbot_backup", "kbot_restore", "kbot_sync")
# Only SSH listens on the network (02 §9). PostgreSQL has listen_addresses = '' and
# systemd-resolved listens on 127.0.0.53, which is the loopback and not the network.
LISTENING_ALLOWED = {("0.0.0.0", "22"), ("[::]", "22"), ("*", "22")}  # noqa: S104
LOOPBACK = ("127.", "[::1]")


@dataclass(frozen=True)
class Verdict:
    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        return f"{'OK  ' if self.ok else 'FAIL'} {self.name}: {self.detail}"


def listening_ports(ss_output: str) -> Verdict:
    """``ss -Hltn``: every TCP listener must be SSH, or on the loopback."""
    unexpected = []
    for line in ss_output.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        local = fields[3]
        address, _, port = local.rpartition(":")
        if (address, port) in LISTENING_ALLOWED or address.startswith(LOOPBACK):
            continue
        unexpected.append(local)
    if unexpected:
        return Verdict("listening ports", False, "unexpected: " + ", ".join(unexpected))
    return Verdict("listening ports", True, "only SSH on the network")


def sshd_settings(sshd_T_output: str) -> Verdict:
    """``sshd -T``: the effective settings, whatever file set them."""
    wanted = {
        "passwordauthentication": "no",
        "kbdinteractiveauthentication": "no",
        "permitrootlogin": "no",
        "pubkeyauthentication": "yes",
        "authenticationmethods": "publickey",
        "disableforwarding": "yes",
    }
    found = {}
    for line in sshd_T_output.splitlines():
        key, _, value = line.partition(" ")
        if key in wanted:
            found[key] = value.strip()
    wrong = {k: found.get(k, "unset") for k, v in wanted.items() if found.get(k) != v}
    if wrong:
        return Verdict("sshd", False, ", ".join(f"{k}={v}" for k, v in wrong.items()))
    return Verdict("sshd", True, "keys only, no root, no forwarding")


def ufw_status(ufw_output: str) -> Verdict:
    """``ufw status verbose``: active, deny incoming, and only port 22 allowed in."""
    if not re.search(r"^Status: active", ufw_output, re.MULTILINE):
        return Verdict("ufw", False, "not active")
    if not re.search(r"^Default: deny \(incoming\)", ufw_output, re.MULTILINE):
        return Verdict("ufw", False, "incoming is not denied by default")
    allowed = re.findall(r"^(\S+)\s+ALLOW IN\s+(\S+)", ufw_output, re.MULTILINE)
    ports = {port.split("/")[0] for port, _ in allowed}
    if ports - {"22"}:
        return Verdict("ufw", False, f"ports allowed in: {sorted(ports)}")
    if any(source in ("Anywhere", "Anywhere (v6)") for _, source in allowed):
        return Verdict("ufw", False, "SSH allowed from anywhere, not only from the owner")
    return Verdict("ufw", True, "active, deny incoming, SSH only from the owner's address")


def service_active(name: str, systemctl_output: str) -> Verdict:
    return Verdict(name, systemctl_output.strip() == "active", systemctl_output.strip())


def postgres_no_tcp(pg_conf_value: str, pg_hba: str) -> Verdict:
    """``listen_addresses`` must be empty and pg_hba.conf must only have ``local ... peer``."""
    if pg_conf_value.strip().strip("'\"") != "":
        return Verdict("postgresql", False, f"listen_addresses = {pg_conf_value.strip()}")
    rules = [
        line.split()
        for line in pg_hba.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    # TYPE DATABASE USER METHOD [OPTIONS]: a map option after peer is still peer.
    bad = [" ".join(r) for r in rules if len(r) < 4 or r[0] != "local" or r[3] != "peer"]
    if bad:
        return Verdict("postgresql", False, "pg_hba rules: " + "; ".join(bad))
    return Verdict("postgresql", True, "no TCP, peer only")


def unattended_upgrades(apt_conf: str) -> Verdict:
    """The security updates run daily and never reboot on their own."""
    update = re.search(r'Unattended-Upgrade\s+"1"', apt_conf)
    reboot = re.search(r'Automatic-Reboot\s+"false"', apt_conf)
    if update and reboot:
        return Verdict("unattended-upgrades", True, "daily, no automatic reboot")
    return Verdict("unattended-upgrades", False, "not configured as deploy/etc/apt expects")


def service_users(passwd: str) -> Verdict:
    """Every service user exists and has no shell."""
    shells = {}
    for line in passwd.splitlines():
        fields = line.split(":")
        if len(fields) >= 7:
            shells[fields[0]] = fields[6]
    problems = []
    for user in SERVICE_USERS:
        shell = shells.get(user)
        if shell is None:
            problems.append(f"{user} missing")
        elif shell not in ("/usr/sbin/nologin", "/bin/false"):
            problems.append(f"{user} has shell {shell}")
    if problems:
        return Verdict("service users", False, ", ".join(problems))
    return Verdict("service users", True, "all present, none with a shell")


def metadata_guard(nft_output: str) -> Verdict:
    """``nft list table inet kbot_guard``: the kernel rule of E4-D04 is loaded."""
    if "169.254.169.254" in nft_output and "reject" in nft_output and "skuid" in nft_output:
        return Verdict("metadata guard", True, "nftables rule loaded")
    return Verdict("metadata guard", False, "table inet kbot_guard not loaded as expected")


def run_all(
    run: Runner, read: Reader, pg_conf_dir: str = "/etc/postgresql/18/main"
) -> list[Verdict]:
    """Every check, with the real commands of the server."""
    verdicts = [
        listening_ports(run(["ss", "-Hltn"])),
        sshd_settings(run(["sshd", "-T"])),
        ufw_status(run(["ufw", "status", "verbose"])),
        service_active("fail2ban", run(["systemctl", "is-active", "fail2ban"])),
        service_active("nftables", run(["systemctl", "is-active", "nftables"])),
        metadata_guard(run(["nft", "list", "table", "inet", "kbot_guard"])),
        unattended_upgrades(read("/etc/apt/apt.conf.d/52kbot-unattended")),
        service_users(read("/etc/passwd")),
    ]
    hba = Path(pg_conf_dir) / "pg_hba.conf"
    if Path(pg_conf_dir).exists():
        value = run(["sudo", "-u", "postgres", "psql", "-XAtc", "SHOW listen_addresses"])
        verdicts.append(postgres_no_tcp(value, read(str(hba))))
    else:
        verdicts.append(Verdict("postgresql", False, "not installed yet (stage E4b)"))
    return verdicts


def report(verdicts: list[Verdict], when: str) -> str:
    lines = [f"# Comprobación del servidor (F2-A10), {when}", ""]
    lines += [f"- {v.line()}" for v in verdicts]
    failed = sum(not v.ok for v in verdicts)
    lines += [
        "",
        "Resultado: " + ("todo en verde" if not failed else f"{failed} comprobaciones fallan"),
    ]
    return "\n".join(lines) + "\n"
