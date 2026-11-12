"""The hardening checks of the server (kbot.ops.check, F2-A10), on recorded outputs."""

from __future__ import annotations

import json

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
UFW_HEADER = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
"""
UFW_OK = (
    UFW_HEADER
    + "22/tcp                     ALLOW IN    83.0.0.1                   # SSH from the owner\n"
)
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
    users = ["root"] if users is None else users
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


def test_ufw_active_deny_incoming_ssh_only_from_the_owner() -> None:
    assert check.ufw_status(UFW_OK).ok
    assert not check.ufw_status(UFW_OK.replace("Status: active", "Status: inactive")).ok
    anywhere = check.ufw_status(UFW_OK.replace("83.0.0.1  ", "Anywhere  "))
    assert not anywhere.ok and "anywhere" in anywhere.detail
    more = check.ufw_status(UFW_OK + "80/tcp                     ALLOW IN    Anywhere\n")
    assert not more.ok and "80" in more.detail


def test_ufw_ipv6_rows_count_too() -> None:
    """Review of E4a, E4a-05: the (v6) rows were skipped by the first parser."""
    v6_ssh = UFW_OK + "22/tcp (v6)                ALLOW IN    Anywhere (v6)\n"
    verdict = check.ufw_status(v6_ssh)
    assert not verdict.ok and "anywhere" in verdict.detail
    v6_port = UFW_OK + "80/tcp (v6)                ALLOW IN    Anywhere (v6)\n"
    assert "80" in check.ufw_status(v6_port).detail
    interface = UFW_OK + "22/tcp on eth0             ALLOW IN    83.0.0.1\n"
    assert check.ufw_status(interface).ok
    unknown = UFW_OK + "something odd here\n"
    assert not check.ufw_status(unknown).ok and "not understood" in check.ufw_status(unknown).detail
    outgoing = UFW_OK + "Anywhere                   ALLOW OUT   Anywhere\n"
    assert check.ufw_status(outgoing).ok


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
    assert check.metadata_guard(nft_json(users=[0])).ok  # root as a number
    assert not check.metadata_guard("").ok
    assert not check.metadata_guard("table inet kbot_guard 169.254.169.254 skuid reject").ok
    assert "hooked" in check.metadata_guard(nft_json(hooked=False)).detail
    # The reviewer's example: the rule in a chain nothing calls filters nothing.
    assert "reachable" in check.metadata_guard(nft_json(rule_chain="unused")).detail
    # A rule behind an unconditional accept is never reached either.
    assert "reachable" in check.metadata_guard(nft_json(accept_first=True)).detail
    extra = check.metadata_guard(nft_json(users=["root", "kbot_ingest"]))
    assert not extra.ok and "kbot_ingest" in extra.detail
    e4c = nft_json(users=["root", "kbot_backup"])
    assert not check.metadata_guard(e4c).ok
    assert check.metadata_guard(e4c, frozenset({"root", "kbot_backup"})).ok


def test_run_all_uses_the_commands_and_files_of_the_server() -> None:
    commands: list[list[str]] = []

    def run(command: list[str]) -> Command:
        commands.append(command)
        outputs = {
            "ss": SS_OK,
            "sshd": SSHD_OK,
            "ufw": UFW_OK,
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
        return Command("active\n" if command[0] == "systemctl" else NFT_OK, 0)

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
    expected_ok = {"guard": user != "root", "b19": True}[case]  # can_connect is False
    assert all(results.values()) is expected_ok
