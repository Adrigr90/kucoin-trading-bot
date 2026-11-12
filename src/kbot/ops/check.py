"""The automatic check of the hardened server (02 §9 and §13.1; acceptance F2-A10).

Each check reads the state of the server through a command or a file and gives a verdict.
``python -m kbot.ops`` runs them all on the server, prints the report and exits with 1
if any fails. The checks are pure functions of what the commands print, so the unit
tests feed them recorded outputs; the real commands run only on the server. A command
that fails, or an output the parser does not understand, is a failed check, never a
passed one (review of E4a, E4a-06).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Command:
    """What a command printed, and whether it succeeded."""

    stdout: str
    returncode: int
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


Runner = Callable[[list[str]], Command]
Reader = Callable[[str], str]  # a path and its text; raises OSError when it cannot


class CommandFailed(Exception):
    pass


SERVICE_USERS = ("kbot_ingest", "kbot_archive", "kbot_backup", "kbot_restore", "kbot_sync")
# Only SSH listens on the network (02 §9). PostgreSQL has listen_addresses = '' and
# systemd-resolved listens on 127.0.0.53, which is the loopback and not the network.
LISTENING_ALLOWED = {("0.0.0.0", "22"), ("[::]", "22"), ("*", "22")}  # noqa: S104
LOOPBACK = ("127.", "[::1]")
GUARD_SERVICE = "kbot-metadata-guard"


@dataclass(frozen=True)
class Verdict:
    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        return f"{'OK  ' if self.ok else 'FAIL'} {self.name}: {self.detail}"


def listening_ports(ss_output: str) -> Verdict:
    """``ss -Hltn``: every TCP listener must be SSH, or on the loopback. A line that is not
    a listener row is a malformed output and fails the check."""
    unexpected = []
    for line in ss_output.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) < 5 or fields[0] != "LISTEN":
            return Verdict("listening ports", False, f"unexpected output: {line.strip()!r}")
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


# A rule row of `ufw status verbose`: "<to>  <action> <direction>  <from>  [# comment]",
# where <to> and <from> may carry "(v6)" and an interface ("22/tcp on eth0").
_UFW_RULE = re.compile(
    r"^(?P<to>\S.*?)\s{2,}(?P<action>ALLOW|DENY|REJECT|LIMIT)\s+(?P<direction>IN|OUT|FWD)"
    r"\s{2,}(?P<from>\S.*?)(?:\s+#.*)?$"
)


def ufw_status(ufw_output: str) -> Verdict:
    """``ufw status verbose``: active, deny incoming, and only SSH allowed in, only from
    the owner's address, in IPv4 and IPv6 alike (review of E4a, E4a-05). Every row after
    the header must parse; an unknown row fails the check."""
    if not re.search(r"^Status: active", ufw_output, re.MULTILINE):
        return Verdict("ufw", False, "not active")
    if not re.search(r"^Default: deny \(incoming\)", ufw_output, re.MULTILINE):
        return Verdict("ufw", False, "incoming is not denied by default")
    lines = ufw_output.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("--")) + 1
    except StopIteration:
        return Verdict("ufw", False, "no rules table in the output")
    problems = []
    for line in lines[start:]:
        if not line.strip():
            continue
        match = _UFW_RULE.match(line.rstrip())
        if match is None:
            return Verdict("ufw", False, f"rule not understood: {line.strip()!r}")
        if match["direction"] != "IN" or match["action"] not in ("ALLOW", "LIMIT"):
            continue
        to = match["to"].replace("(v6)", "").strip()
        source = match["from"].replace("(v6)", "").strip()
        port = to.split("/")[0].split(" ")[0]
        if port != "22":
            problems.append(f"port {to} allowed in from {source}")
        elif source.startswith("Anywhere"):
            problems.append("SSH allowed from anywhere, not only from the owner")
    if problems:
        return Verdict("ufw", False, "; ".join(sorted(set(problems))))
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


GUARD_ADDRESS = "169.254.169.254"
GUARD_PORTS = {80, 443}


def _match(expr: dict[str, Any], left: dict[str, Any], op: str = "==") -> dict[str, Any] | None:
    """The right side of a `match` expression whose left side is `left`, or None."""
    match = expr.get("match")
    if match is None or match.get("left") != left or match.get("op") != op:
        return None
    right = match.get("right")
    return right if isinstance(right, dict) else {"value": right}


class _NotUnderstood(Exception):
    """An element of an nft set that this parser does not interpret (a range, a prefix,
    a concatenation...). It must never be dropped: it could widen the exemption (review
    of E4a, third round)."""


def _set_values(right: dict[str, Any]) -> set[Any]:
    values = right["set"] if "set" in right else [right.get("value")]
    if any(not isinstance(v, int | str) or isinstance(v, bool) for v in values):
        raise _NotUnderstood(f"set element not understood: {values!r}")
    return set(values)


def _guard_rule_users(rule: dict[str, Any]) -> set[str] | None:
    """The users exempted by a rule that rejects the metadata address on the HTTP ports
    for every other user id, or None if the rule is not that rule."""
    exprs = rule.get("expr", [])
    daddr = any(
        _match(e, {"payload": {"protocol": "ip", "field": "daddr"}}) == {"value": GUARD_ADDRESS}
        for e in exprs
    )
    ports = any(
        (right := _match(e, {"payload": {"protocol": "tcp", "field": "dport"}})) is not None
        and _set_values(right) == GUARD_PORTS
        for e in exprs
    )
    users = next(
        (
            _set_values(right)
            for e in exprs
            if (right := _match(e, {"meta": {"key": "skuid"}}, "!=")) is not None
        ),
        None,
    )
    rejects = any("reject" in e for e in exprs)
    if not (daddr and ports and users is not None and rejects):
        return None
    return {"root" if u in (0, "0") else str(u) for u in users}


def _unconditional_accept(rule: dict[str, Any]) -> bool:
    exprs = [e for e in rule.get("expr", []) if "counter" not in e]
    return bool(exprs) and all("accept" in e for e in exprs)


def metadata_guard(nft_json: str, allowed_users: frozenset[str] = frozenset({"root"})) -> Verdict:
    """``nft -j list table inet kbot_guard``: the rule of E4-D04 must sit in a chain hooked
    on output, before any unconditional accept, and exempt exactly the allowed users
    (review of E4a, E4a-04: a rule in a chain nothing calls filters nothing)."""
    try:
        objects = json.loads(nft_json).get("nftables", [])
    except (json.JSONDecodeError, AttributeError):
        return Verdict("metadata guard", False, "nft did not return JSON")
    if not any(o.get("table", {}).get("name") == "kbot_guard" for o in objects):
        return Verdict("metadata guard", False, "table inet kbot_guard not loaded")
    hooked = {
        o["chain"]["name"]
        for o in objects
        if "chain" in o
        and o["chain"].get("hook") == "output"
        and o["chain"].get("type") == "filter"
    }
    if not hooked:
        return Verdict("metadata guard", False, "no chain hooked on output in the table")
    blocked: set[str] = set()
    for o in objects:
        rule = o.get("rule")
        if rule is None or rule.get("chain") not in hooked or rule["chain"] in blocked:
            continue
        try:
            users = _guard_rule_users(rule)
        except _NotUnderstood as error:
            return Verdict("metadata guard", False, str(error))
        if users is not None:
            if users != allowed_users:
                return Verdict("metadata guard", False, f"users exempted: {sorted(users)}")
            return Verdict(
                "metadata guard", True, f"output hook, reject by user, exempt {sorted(users)}"
            )
        if _unconditional_accept(rule):
            blocked.add(rule["chain"])  # what follows in this chain is never reached
    return Verdict(
        "metadata guard", False, "no reachable reject rule for 169.254.169.254:80,443 by user"
    )


def run_all(
    run: Runner, read: Reader, pg_conf_dir: str = "/etc/postgresql/18/main"
) -> list[Verdict]:
    """Every check, with the real commands of the server. A command that fails or a file
    that cannot be read fails its check with the error as the detail."""

    def output(command: list[str]) -> str:
        result = run(command)
        if not result.ok:
            raise CommandFailed(
                f"{' '.join(command)}: exit {result.returncode}: {result.stderr.strip()}"
            )
        return result.stdout

    def checked(name: str, check: Callable[[], Verdict]) -> Verdict:
        try:
            return check()
        except (CommandFailed, OSError) as error:
            return Verdict(name, False, str(error))

    verdicts = [
        checked("listening ports", lambda: listening_ports(output(["ss", "-Hltn"]))),
        checked("sshd", lambda: sshd_settings(output(["sshd", "-T"]))),
        checked("ufw", lambda: ufw_status(output(["ufw", "status", "verbose"]))),
        checked(
            "fail2ban",
            lambda: service_active("fail2ban", output(["systemctl", "is-active", "fail2ban"])),
        ),
        checked(
            GUARD_SERVICE,
            lambda: service_active(
                GUARD_SERVICE, output(["systemctl", "is-active", GUARD_SERVICE])
            ),
        ),
        checked(
            "metadata guard",
            lambda: metadata_guard(output(["nft", "-j", "list", "table", "inet", "kbot_guard"])),
        ),
        checked(
            "unattended-upgrades",
            lambda: unattended_upgrades(read("/etc/apt/apt.conf.d/52kbot-unattended")),
        ),
        checked("service users", lambda: service_users(read("/etc/passwd"))),
    ]
    hba = str(Path(pg_conf_dir) / "pg_hba.conf")
    if Path(pg_conf_dir).exists():
        verdicts.append(
            checked(
                "postgresql",
                lambda: postgres_no_tcp(
                    output(["sudo", "-u", "postgres", "psql", "-XAtc", "SHOW listen_addresses"]),
                    read(hba),
                ),
            )
        )
    else:
        verdicts.append(Verdict("postgresql", False, "not installed yet (stage E4b)"))
    return verdicts


def report(verdicts: list[Verdict], when: str) -> str:
    lines = [f"# Comprobación del servidor (F2-A10), {when}", ""]
    lines += [f"- {v.line()}" for v in verdicts]
    failed = sum(not v.ok for v in verdicts)
    result = "todo en verde" if not failed else f"{failed} comprobaciones fallan"
    lines += ["", "Resultado: " + result]
    return "\n".join(lines) + "\n"
