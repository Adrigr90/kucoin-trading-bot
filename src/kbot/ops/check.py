"""The automatic check of the hardened server (02 §9 and §13.1; acceptance F2-A10).

Each check reads the state of the server through a command or a file and gives a verdict.
``python -m kbot.ops`` runs them all on the server, prints the report and exits with 1
if any fails. The checks are pure functions of what the commands print, so the unit
tests feed them recorded outputs; the real commands run only on the server. A command
that fails, or an output the parser does not understand, is a failed check, never a
passed one (review of E4a, E4a-06).
"""

from __future__ import annotations

import ipaddress
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


# The firewall is the one of the Oracle image, iptables loaded at boot by
# netfilter-persistent, not ufw (owner decision of 16 Nov 2026). Its IPv4 rules stay as the
# image ships them: the owner's address is limited only in the security list of Oracle
# (owner decision of 17 Nov 2026, review FW-02). The check compares what `iptables -S` and
# `ip6tables -S` print with the expected rules, chain by chain and in order (review FW-01):
# any other rule, chain or policy fails, whatever it does. The only part that may vary is
# what fail2ban adds on its first ban: a jump at the top of INPUT to its own chain, which
# may hold only bans of single addresses and ends returning.
_ORACLE_NOTE = (
    '-m comment --comment "See the Oracle-Provided Images section in the Oracle Cloud'
    ' Infrastructure documentation for security impact of modifying or removing this rule"'
)
# `iptables -S InstanceServices` on the instance (16 Nov 2026): what the instance needs to
# reach its boot volume (iSCSI, only as root) and Oracle's services, and nothing else of
# 169.254.0.0/16.
_INSTANCE_SERVICES = tuple(
    f"{match} {_ORACLE_NOTE} -j {target}"
    for match, target in (
        ("-d 169.254.0.2/32 -p tcp -m owner --uid-owner 0 -m tcp --dport 3260", "ACCEPT"),
        ("-d 169.254.2.0/24 -p tcp -m owner --uid-owner 0 -m tcp --dport 3260", "ACCEPT"),
        ("-d 169.254.4.0/24 -p tcp -m owner --uid-owner 0 -m tcp --dport 3260", "ACCEPT"),
        ("-d 169.254.5.0/24 -p tcp -m owner --uid-owner 0 -m tcp --dport 3260", "ACCEPT"),
        ("-d 169.254.0.2/32 -p tcp -m tcp --dport 80", "ACCEPT"),
        ("-d 169.254.169.254/32 -p udp -m udp --dport 53", "ACCEPT"),
        ("-d 169.254.169.254/32 -p tcp -m tcp --dport 53", "ACCEPT"),
        ("-d 169.254.0.3/32 -p tcp -m owner --uid-owner 0 -m tcp --dport 80", "ACCEPT"),
        ("-d 169.254.0.4/32 -p tcp -m tcp --dport 80", "ACCEPT"),
        ("-d 169.254.169.254/32 -p tcp -m tcp --dport 80", "ACCEPT"),
        ("-d 169.254.169.254/32 -p udp -m udp --dport 67", "ACCEPT"),
        ("-d 169.254.169.254/32 -p udp -m udp --dport 69", "ACCEPT"),
        ("-d 169.254.169.254/32 -p udp -m udp --dport 123", "ACCEPT"),
        ("-d 169.254.0.0/16 -p tcp -m tcp", "REJECT --reject-with tcp-reset"),
        ("-d 169.254.0.0/16 -p udp -m udp", "REJECT --reject-with icmp-port-unreachable"),
    )
)
F2B_CHAIN = "f2b-sshd"
_F2B_JUMP = f"-p tcp -m multiport --dports 22 -j {F2B_CHAIN}"


@dataclass(frozen=True)
class FirewallRules:
    """What ``-S`` must print for one family: the policies, the rules of each chain in
    order, and the form of a ban of fail2ban (deploy/etc/fail2ban/jail.d/kbot.conf)."""

    family: str
    policies: dict[str, str]
    chains: dict[str, tuple[str, ...]]
    ban: re.Pattern[str]
    address: Callable[[str], object]  # raises ValueError unless one single address


IPV4_RULES = FirewallRules(
    "IPv4",
    policies={"INPUT": "ACCEPT", "FORWARD": "ACCEPT", "OUTPUT": "ACCEPT"},
    chains={
        "INPUT": (
            "-m state --state RELATED,ESTABLISHED -j ACCEPT",
            "-p icmp -j ACCEPT",
            "-i lo -j ACCEPT",
            "-p tcp -m state --state NEW -m tcp --dport 22 -j ACCEPT",
            "-j REJECT --reject-with icmp-host-prohibited",
        ),
        "FORWARD": ("-j REJECT --reject-with icmp-host-prohibited",),
        "OUTPUT": ("-d 169.254.0.0/16 -j InstanceServices",),
        "InstanceServices": _INSTANCE_SERVICES,
    },
    ban=re.compile(r"-s (\S+)/32 -j REJECT --reject-with icmp-port-unreachable"),
    address=ipaddress.IPv4Address,
)
# deploy/etc/iptables/rules.v6: nothing new comes in (the instance has no public IPv6).
IPV6_RULES = FirewallRules(
    "IPv6",
    policies={"INPUT": "DROP", "FORWARD": "DROP", "OUTPUT": "ACCEPT"},
    chains={
        "INPUT": (
            "-i lo -j ACCEPT",
            "-m state --state RELATED,ESTABLISHED -j ACCEPT",
            "-p ipv6-icmp -j ACCEPT",
        ),
        "FORWARD": (),
        "OUTPUT": (),
    },
    ban=re.compile(r"-s (\S+)/128 -j REJECT --reject-with icmp6-port-unreachable"),
    address=ipaddress.IPv6Address,
)


def _first_difference(found: list[str], expected: tuple[str, ...]) -> str:
    for index, (have, want) in enumerate(zip(found, expected, strict=False), 1):
        if have != want:
            return f"rule {index} is {have!r}, expected {want!r}"
    if len(found) > len(expected):
        return f"unexpected rule {len(expected) + 1}: {found[len(expected)]!r}"
    return f"missing rule {len(found) + 1}: {expected[len(found)]!r}"


def _is_address(kind: Callable[[str], object], text: str) -> bool:
    try:
        kind(text)
    except ValueError:
        return False
    return True


def _fail2ban_problems(
    expected: FirewallRules, rules: list[str] | None, jump: bool
) -> tuple[list[str], int]:
    """fail2ban's chain and its jump come together, on the first ban. The chain may hold
    only bans of single addresses and must end returning: an accept there would let in
    what the rules of the image reject (review FW-01)."""
    name = f"{expected.family} {F2B_CHAIN}"
    if rules is None:
        return ([f"{name}: jump without its chain"] if jump else []), 0
    problems = [] if jump else [f"{name}: chain without the jump at the top of INPUT"]
    body = rules[:-1] if rules[-1:] == ["-j RETURN"] else rules
    if body is rules:
        problems.append(f"{name}: does not end returning")
    bans = 0
    for rule in body:
        match = expected.ban.fullmatch(rule)
        if match is None or not _is_address(expected.address, match[1]):
            problems.append(f"{name}: not a ban of one address: {rule!r}")
        else:
            bans += 1
    return problems, bans


def _rules_problems(expected: FirewallRules, output: str) -> tuple[list[str], int]:
    """What separates ``-S`` from the expected rules, and the number of bans."""
    name = expected.family
    policies: dict[str, str] = {}
    chains: dict[str, list[str]] = {}
    for line in (raw.strip() for raw in output.splitlines()):
        if not line:
            continue
        option, _, rest = line.partition(" ")
        chain, _, rule = rest.partition(" ")
        if option == "-P" and chain and rule:
            policies[chain] = rule
        elif option == "-N" and chain and not rule:
            chains.setdefault(chain, [])
        elif option == "-A" and chain and rule:
            chains.setdefault(chain, []).append(rule)
        else:
            return [f"{name}: line not understood: {line!r}"], 0
    problems = []
    if policies != expected.policies:
        problems.append(f"{name}: policies {policies}, expected {expected.policies}")
    problems += [
        f"{name}: unexpected chain {chain}"
        for chain in chains
        if chain not in expected.chains and chain != F2B_CHAIN
    ]
    jump = chains.get("INPUT", [])[:1] == [_F2B_JUMP]
    for chain, rules in expected.chains.items():
        found = chains.get(chain, [])[1:] if chain == "INPUT" and jump else chains.get(chain, [])
        if found != list(rules):
            problems.append(f"{name} {chain}: {_first_difference(found, rules)}")
    fail2ban, bans = _fail2ban_problems(expected, chains.get(F2B_CHAIN), jump)
    return problems + fail2ban, bans


def firewall_status(v4: str, v6: str) -> Verdict:
    """``iptables -S`` and ``ip6tables -S`` against the expected rules (review FW-01): in
    IPv4, exactly the rules of the Oracle image (new connections only for SSH, Oracle's
    own rules); in IPv6, nothing new comes in; in both, only what fail2ban adds on a ban.
    An output the parser does not understand fails the check."""
    problems4, bans4 = _rules_problems(IPV4_RULES, v4)
    problems6, bans6 = _rules_problems(IPV6_RULES, v6)
    if problems4 or problems6:
        return Verdict("firewall", False, "; ".join(problems4 + problems6))
    return Verdict(
        "firewall",
        True,
        "IPv4 exactly as the Oracle image ships it (SSH limited to the owner in the security"
        f" list of Oracle), IPv6 closed; addresses banned by fail2ban: {bans4 + bans6}",
    )


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
        checked(
            "firewall",
            lambda: firewall_status(output(["iptables", "-S"]), output(["ip6tables", "-S"])),
        ),
        checked(
            "netfilter-persistent",
            lambda: service_active(
                "netfilter-persistent", output(["systemctl", "is-active", "netfilter-persistent"])
            ),
        ),
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
