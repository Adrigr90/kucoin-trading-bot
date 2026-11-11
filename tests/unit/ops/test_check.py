"""The hardening checks of the server (kbot.ops.check, F2-A10), on recorded outputs."""

from __future__ import annotations

from kbot.ops import check
from kbot.ops.check import Verdict

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
UFW_OK = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    83.0.0.1                   # SSH from the owner
"""
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


def test_only_ssh_may_listen_on_the_network() -> None:
    assert check.listening_ports(SS_OK).ok
    bad = check.listening_ports(SS_BAD)
    assert not bad.ok and "5432" in bad.detail


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
    anywhere = check.ufw_status(UFW_OK.replace("83.0.0.1", "Anywhere"))
    assert not anywhere.ok and "anywhere" in anywhere.detail
    more = check.ufw_status(UFW_OK + "80/tcp                     ALLOW IN    Anywhere\n")
    assert not more.ok and "80" in more.detail


def test_postgresql_without_tcp_and_peer_only() -> None:
    assert check.postgres_no_tcp("", HBA_OK).ok
    assert not check.postgres_no_tcp("localhost", HBA_OK).ok
    assert not check.postgres_no_tcp("", HBA_OK + "host all all 127.0.0.1/32 md5\n").ok
    assert (
        check.postgres_no_tcp("", "local all all peer map=kbot_ci\n").ok is not False
    )  # a map is still peer


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


def test_metadata_guard_loaded() -> None:
    nft = "table inet kbot_guard {\n chain output {\n ip daddr 169.254.169.254 tcp dport { 80, 443 } meta skuid != { 0 } counter reject\n }\n}\n"
    assert check.metadata_guard(nft).ok
    assert not check.metadata_guard("").ok


def test_run_all_uses_the_commands_and_files_of_the_server() -> None:
    commands: list[list[str]] = []

    def run(command: list[str]) -> str:
        commands.append(command)
        outputs = {
            "ss": SS_OK,
            "sshd": SSHD_OK,
            "ufw": UFW_OK,
            "systemctl": "active\n",
            "nft": "table inet kbot_guard ip daddr 169.254.169.254 meta skuid reject",
            "sudo": "",
        }
        return outputs[command[0]]

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
    text = check.report(verdicts, "2026-11-11 12:00 UTC")
    assert "1 comprobaciones fallan" in text
    assert check.report([Verdict("x", True, "y")], "now").endswith("todo en verde\n")
