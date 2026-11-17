"""The hardening checks of the server (kbot.ops.check, F2-A10), on recorded outputs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kbot.ops import check, netcheck
from kbot.ops.check import Command, Verdict

SS_OK = """LISTEN 0 128 0.0.0.0:22 0.0.0.0:*
LISTEN 0 128 127.0.0.53%lo:53 0.0.0.0:*
LISTEN 0 4096 [::]:22 [::]:*
"""
SS_BAD = SS_OK + "LISTEN 0 244 0.0.0.0:5432 0.0.0.0:*\n"
SSHD_OK = """port 22
passwordauthentication no
kbdinteractiveauthentication no
permitrootlogin no
pubkeyauthentication yes
authenticationmethods publickey
disableforwarding yes
"""
# `iptables -S` on the instance of 16 Nov 2026, verbatim: the rules of the Oracle image.
# The CI checks that the iptables of the runner prints the same for the image's file.
FIREWALL = Path(__file__).resolve().parents[2] / "fixtures" / "firewall"
IPTABLES_OK = (FIREWALL / "oracle_iptables-S.txt").read_text(encoding="utf-8")
# What `ip6tables -S` prints for deploy/etc/iptables/rules.v6 (also checked in the CI).
IP6TABLES_OK = """-P INPUT DROP
-P FORWARD DROP
-P OUTPUT ACCEPT
-A INPUT -i lo -j ACCEPT
-A INPUT -m state --state RELATED,ESTABLISHED -j ACCEPT
-A INPUT -p ipv6-icmp -j ACCEPT
"""
SSH_IMAGE = "-A INPUT -p tcp -m state --state NEW -m tcp --dport 22 -j ACCEPT"
ISCSI = "-A InstanceServices -d 169.254.0.2/32 -p tcp -m owner --uid-owner 0 -m tcp --dport 3260"
F2B_JUMP = "-A INPUT -p tcp -m multiport --dports 22 -j f2b-sshd"
BAN4 = "-s 203.0.113.7/32 -j REJECT --reject-with icmp-port-unreachable"
BAN6 = "-s 2001:db8::7/128 -j REJECT --reject-with icmp6-port-unreachable"


def with_fail2ban(rules: str, *chain: str, jump: bool = True, declare: bool = True) -> str:
    """``-S`` after fail2ban's first ban: its chain, and its jump at the top of INPUT."""
    lines = rules.splitlines()
    first_rule = next(i for i, line in enumerate(lines) if line.startswith("-A "))
    if jump:
        lines.insert(first_rule, F2B_JUMP)
    if declare:
        lines.insert(first_rule, "-N f2b-sshd")
    lines += [f"-A f2b-sshd {rule}" for rule in chain]
    return "\n".join(lines) + "\n"


def firewall(v4: str = IPTABLES_OK, v6: str = IP6TABLES_OK) -> Verdict:
    return check.firewall_status(v4, v6)


HBA_OK = "# comment\nlocal   all   all   peer\n"
PASSWD = "\n".join(
    [
        "root:x:0:0:root:/root:/bin/bash",
        "ubuntu:x:1000:1000::/home/ubuntu:/bin/bash",
        *(
            f"{u}:x:99{i}:99{i}::/nonexistent:/usr/sbin/nologin"
            for i, u in enumerate(check.SERVICE_USERS)
        ),
    ]
)


def nft_json(
    rule_chain: str = "output",
    users: list[object] | None = None,
    hooked: bool = True,
    accept_first: bool = False,
) -> str:
    """What ``nft -j list table inet kbot_guard`` prints for the guard, with variations."""
    users = ["root", "snap_daemon"] if users is None else users
    chain = {"family": "inet", "table": "kbot_guard", "name": "output", "handle": 1}
    if hooked:
        chain |= {"type": "filter", "hook": "output", "prio": -10, "policy": "accept"}
    guard_rule = {
        "family": "inet",
        "table": "kbot_guard",
        "chain": rule_chain,
        "handle": 3,
        "expr": [
            {
                "match": {
                    "op": "==",
                    "left": {"payload": {"protocol": "ip", "field": "daddr"}},
                    "right": "169.254.169.254",
                }
            },
            {
                "match": {
                    "op": "==",
                    "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                    "right": {"set": [80, 443]},
                }
            },
            {"match": {"op": "!=", "left": {"meta": {"key": "skuid"}}, "right": {"set": users}}},
            {"counter": {"packets": 0, "bytes": 0}},
            {"reject": None},
        ],
    }
    accept_rule = {
        "family": "inet",
        "table": "kbot_guard",
        "chain": "output",
        "handle": 2,
        "expr": [{"counter": {"packets": 0, "bytes": 0}}, {"accept": None}],
    }
    objects: list[dict[str, object]] = [
        {"metainfo": {"version": "1.0.9", "release_name": "x", "json_schema_version": 1}},
        {"table": {"family": "inet", "name": "kbot_guard", "handle": 7}},
        {"chain": chain},
        {"chain": {"family": "inet", "table": "kbot_guard", "name": "unused", "handle": 9}},
    ]
    if accept_first:
        objects.append({"rule": accept_rule})
    objects.append({"rule": guard_rule})
    return json.dumps({"nftables": objects})


NFT_OK = nft_json()


def test_only_ssh_may_listen_on_the_network() -> None:
    assert check.listening_ports(SS_OK).ok
    assert check.listening_ports("").ok  # no listener at all is fine
    bad = check.listening_ports(SS_BAD)
    assert not bad.ok and "5432" in bad.detail


def test_an_output_that_is_not_a_listener_table_fails() -> None:
    # Review of E4a, E4a-06: an error text must never read as "no listeners".
    assert not check.listening_ports("error (1): ").ok
    assert not check.listening_ports("Netid State Recv-Q Send-Q\n").ok


def test_sshd_effective_settings() -> None:
    assert check.sshd_settings(SSHD_OK).ok
    weak = check.sshd_settings(
        SSHD_OK.replace("passwordauthentication no", "passwordauthentication yes")
    )
    assert not weak.ok and "passwordauthentication=yes" in weak.detail
    missing = check.sshd_settings("port 22\n")
    assert not missing.ok and "unset" in missing.detail


def test_the_firewall_is_exactly_the_one_of_the_image() -> None:
    """Owner decisions of 16 and 17 Nov 2026: the firewall of the Oracle image, not ufw,
    with its IPv4 rules as shipped; the owner's address is limited in the security list
    of Oracle, not on the server (review FW-02)."""
    verdict = firewall()
    assert verdict.ok, verdict.detail
    assert "security list" in verdict.detail and "fail2ban: 0" in verdict.detail
    # Limiting SSH on the server again changes the approved rules, and fails.
    owner = SSH_IMAGE.replace("-A INPUT", "-A INPUT -s 79.112.83.254/32")
    assert "IPv4 INPUT: rule 4" in firewall(IPTABLES_OK.replace(SSH_IMAGE, owner)).detail


def test_the_firewall_check_fails_on_the_reviewers_examples() -> None:
    """Review FW-01: three changes the earlier check let through."""
    # An accept in fail2ban's chain lets in whatever reaches it, before the image's rules.
    accept = firewall(with_fail2ban(IPTABLES_OK, "-j ACCEPT", "-j RETURN"))
    assert not accept.ok and "not a ban of one address: '-j ACCEPT'" in accept.detail
    # A drop before SSH, or before the iSCSI rules of the boot volume.
    drop_ssh = firewall(IPTABLES_OK.replace(SSH_IMAGE, "-A INPUT -j DROP\n" + SSH_IMAGE))
    assert "IPv4 INPUT: rule 4 is '-j DROP'" in drop_ssh.detail
    drop_iscsi = firewall(IPTABLES_OK.replace(ISCSI, "-A InstanceServices -j DROP\n" + ISCSI))
    assert "IPv4 InstanceServices: rule 1 is '-j DROP'" in drop_iscsi.detail


def test_the_firewall_check_admits_only_bans_from_fail2ban() -> None:
    second = BAN4.replace("203.0.113.7", "198.51.100.1")
    banned = firewall(
        with_fail2ban(IPTABLES_OK, BAN4, second, "-j RETURN"),
        with_fail2ban(IP6TABLES_OK, BAN6, "-j RETURN"),
    )
    assert banned.ok and "fail2ban: 3" in banned.detail, banned.detail
    for chain in (
        (BAN4.replace("/32", "/24"), "-j RETURN"),  # more than one address
        (BAN4.replace("203.0.113.7", "203.0.113.700"), "-j RETURN"),  # not an address
        (BAN4.replace("REJECT --reject-with icmp-port-unreachable", "ACCEPT"), "-j RETURN"),
        ("-j RETURN", BAN4),  # a ban after the return is never reached
    ):
        assert "not a ban of one address" in firewall(with_fail2ban(IPTABLES_OK, *chain)).detail
    assert "end returning" in firewall(with_fail2ban(IPTABLES_OK, BAN4)).detail
    unhooked = with_fail2ban(IPTABLES_OK, BAN4, "-j RETURN", jump=False)
    assert "without the jump" in firewall(unhooked).detail
    assert "jump without its chain" in firewall(with_fail2ban(IPTABLES_OK, declare=False)).detail
    # A jump that sends every connection to the chain, not only SSH, is not fail2ban's.
    everything = with_fail2ban(IPTABLES_OK, "-j RETURN").replace(F2B_JUMP, "-A INPUT -j f2b-sshd")
    assert "IPv4 INPUT: rule 1 is '-j f2b-sshd'" in firewall(everything).detail
    v6_as_v4 = with_fail2ban(IP6TABLES_OK, BAN6.replace("/128", "/32"), "-j RETURN")
    assert "IPv6 f2b-sshd: not a ban" in firewall(v6=v6_as_v4).detail


def test_the_firewall_check_fails_on_any_other_change() -> None:
    web = IPTABLES_OK.replace(
        SSH_IMAGE, SSH_IMAGE + "\n-A INPUT -p tcp -m tcp --dport 80 -j ACCEPT"
    )
    assert "IPv4 INPUT: rule 5 is" in firewall(web).detail
    no_final = IPTABLES_OK.replace("-A INPUT -j REJECT --reject-with icmp-host-prohibited\n", "")
    assert "IPv4 INPUT: missing rule 5" in firewall(no_final).detail
    assert (
        "IPv4: policies" in firewall(IPTABLES_OK.replace("FORWARD ACCEPT", "FORWARD DROP")).detail
    )
    # Oracle's rules: one changed, or two of them swapped.
    no_iscsi = IPTABLES_OK.replace(ISCSI, "-A InstanceServices -d 169.254.9.9/32")
    assert "IPv4 InstanceServices: rule 1 is" in firewall(no_iscsi).detail
    lines = IPTABLES_OK.splitlines()
    udp, tcp = (i for i, line in enumerate(lines) if "--dport 53 " in line)
    lines[udp], lines[tcp] = lines[tcp], lines[udp]
    assert "IPv4 InstanceServices: rule 6 is" in firewall("\n".join(lines)).detail
    assert "unexpected chain DOCKER" in firewall(IPTABLES_OK + "-N DOCKER\n").detail
    # IPv6: open, or with something more.
    assert (
        "IPv6: policies" in firewall(v6=IP6TABLES_OK.replace("INPUT DROP", "INPUT ACCEPT")).detail
    )
    ssh_v6 = IP6TABLES_OK + "-A INPUT -p tcp -m tcp --dport 22 -j ACCEPT\n"
    assert "IPv6 INPUT: unexpected rule 4" in firewall(v6=ssh_v6).detail
    assert (
        "IPv6 OUTPUT: unexpected rule 1" in firewall(v6=IP6TABLES_OK + "-A OUTPUT -j DROP\n").detail
    )
    # An output the parser does not understand, or none at all.
    assert (
        "line not understood" in firewall("iptables: Permission denied (you must be root)\n").detail
    )
    assert not firewall("", "").ok


def test_postgresql_without_tcp_and_peer_only() -> None:
    assert check.postgres_no_tcp("", HBA_OK).ok
    assert not check.postgres_no_tcp("localhost", HBA_OK).ok
    assert not check.postgres_no_tcp("", HBA_OK + "host all all 127.0.0.1/32 md5\n").ok
    assert check.postgres_no_tcp("", "local all all peer map=kbot_ci\n").ok  # a map is still peer


def test_unattended_upgrades_daily_without_reboot() -> None:
    conf = 'APT::Periodic::Unattended-Upgrade "1";\nUnattended-Upgrade::Automatic-Reboot "false";\n'
    assert check.unattended_upgrades(conf).ok
    assert not check.unattended_upgrades(conf.replace('"false"', '"true"')).ok


def test_service_users_exist_without_a_shell() -> None:
    assert check.service_users(PASSWD).ok
    with_shell = PASSWD.replace(
        "kbot_ingest:x:990:990::/nonexistent:/usr/sbin/nologin",
        "kbot_ingest:x:990:990::/home/i:/bin/bash",
    )
    assert not check.service_users(with_shell).ok
    assert "kbot_sync missing" in check.service_users(PASSWD.replace("kbot_sync", "other")).detail


def test_metadata_guard_is_checked_structurally() -> None:
    """Review of E4a, E4a-04: the rule must sit in the chain hooked on output, be reachable
    and exempt exactly the allowed users; the kernel's JSON is what is parsed."""
    assert check.metadata_guard(NFT_OK).ok
    # By number when nft cannot name them: 0 is root, 584788 snap_daemon (snapd's fixed id).
    assert check.metadata_guard(nft_json(users=[0, 584788])).ok
    assert check.metadata_guard(nft_json(users=["root", "584788"])).ok
    # Root alone would cut off the Oracle Cloud Agent, which runs as snap_daemon (17 Nov).
    root_only = check.metadata_guard(nft_json(users=["root"]))
    assert not root_only.ok and "users exempted: ['root']" in root_only.detail
    assert not check.metadata_guard("").ok
    assert not check.metadata_guard("table inet kbot_guard 169.254.169.254 skuid reject").ok
    assert "hooked" in check.metadata_guard(nft_json(hooked=False)).detail
    # The reviewer's example: the rule in a chain nothing calls filters nothing.
    assert "reachable" in check.metadata_guard(nft_json(rule_chain="unused")).detail
    # A rule behind an unconditional accept is never reached either.
    assert "reachable" in check.metadata_guard(nft_json(accept_first=True)).detail
    extra = check.metadata_guard(nft_json(users=["root", "snap_daemon", "kbot_ingest"]))
    assert not extra.ok and "kbot_ingest" in extra.detail
    # Third round: an element the parser does not interpret (a range of user ids) must
    # fail the check, never be dropped to leave only root.
    ranged = check.metadata_guard(nft_json(users=[0, {"range": [100, 65535]}]))
    assert not ranged.ok and "not understood" in ranged.detail
    prefix = check.metadata_guard(nft_json(users=[{"prefix": {"addr": "0", "len": 8}}]))
    assert not prefix.ok
    e4c = nft_json(users=["root", "snap_daemon", "kbot_backup"])
    assert not check.metadata_guard(e4c).ok
    assert check.metadata_guard(e4c, check.METADATA_ALLOWED | {"kbot_backup"}).ok


def test_run_all_uses_the_commands_and_files_of_the_server() -> None:
    commands: list[list[str]] = []

    def run(command: list[str]) -> Command:
        commands.append(command)
        outputs = {
            "ss": SS_OK,
            "sshd": SSHD_OK,
            "iptables": IPTABLES_OK,
            "ip6tables": IP6TABLES_OK,
            "systemctl": "active\n",
            "nft": NFT_OK,
        }
        return Command(outputs[command[0]], 0)

    def read(path: str) -> str:
        return {
            "/etc/apt/apt.conf.d/52kbot-unattended": (
                'Unattended-Upgrade "1"; Automatic-Reboot "false";'
            ),
            "/etc/passwd": PASSWD,
        }[path]

    verdicts = check.run_all(run, read, pg_conf_dir="/nonexistent")
    assert [v.name for v in verdicts][-1] == "postgresql"
    assert not verdicts[-1].ok  # not installed yet
    assert all(v.ok for v in verdicts[:-1])
    assert ["ss", "-Hltn"] in commands
    assert ["systemctl", "is-active", "kbot-metadata-guard"] in commands
    assert ["nft", "-j", "list", "table", "inet", "kbot_guard"] in commands
    text = check.report(verdicts, "2026-11-11 12:00 UTC")
    assert "1 comprobaciones fallan" in text
    assert check.report([Verdict("x", True, "y")], "now").endswith("todo en verde\n")


def test_a_failed_command_or_unreadable_file_fails_its_check() -> None:
    """Review of E4a, E4a-06: exit codes and exceptions reach the verdict."""

    def run(command: list[str]) -> Command:
        if command[0] == "ss":
            return Command("", 1, "ss: not permitted")
        outputs = {"systemctl": "active\n", "iptables": IPTABLES_OK, "ip6tables": IP6TABLES_OK}
        return Command(outputs.get(command[0], NFT_OK), 0)

    def read(path: str) -> str:
        raise OSError(f"cannot read {path}")

    verdicts = {v.name: v for v in check.run_all(run, read, pg_conf_dir="/nonexistent")}
    assert not verdicts["listening ports"].ok and "exit 1" in verdicts["listening ports"].detail
    assert not verdicts["service users"].ok and "cannot read" in verdicts["service users"].detail
    assert verdicts["metadata guard"].ok


@pytest.mark.parametrize(
    ("case", "user", "names"),
    [
        ("b19", "kbot_ingest", {"postgresql socket accepts", "kucoin unreachable"}),
        ("b19", "kbot_sync", {"postgresql socket refuses", "kucoin unreachable"}),
        ("guard", "kbot_ingest", {"metadata rejected"}),
        ("guard", "kbot_backup", {"metadata rejected"}),  # exempted only from E4c
        ("guard", "root", {"metadata reachable"}),
        ("guard", "snap_daemon", {"metadata reachable"}),  # the Oracle Cloud Agent
    ],
)
def test_netcheck_expectations_depend_on_the_case_and_the_user(
    case: str, user: str, names: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of E4a, E4a-04: B19 and the guard are separate cases, and a user without a
    SQL role is expected to be refused by peer."""
    monkeypatch.setattr(netcheck, "can_connect", lambda *_a, **_k: False)
    monkeypatch.setattr(netcheck, "postgres_socket_accepts", lambda u: u in ("kbot_ingest",))
    results = netcheck.expectations(case, user)
    assert set(results) == names
    allowed = user in netcheck.METADATA_ALLOWED
    expected_ok = {"guard": not allowed, "b19": True}[case]  # can_connect is False
    assert all(results.values()) is expected_ok
