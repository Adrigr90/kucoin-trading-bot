"""The files of deploy/ are valid before they reach the server (02 §13.1, point 8).

Each check needs a tool of the Ubuntu runner: shellcheck and bash for the scripts,
systemd-analyze for the units, sshd for the SSH drop-in, nft for the metadata guard and
iptables (in a throwaway network namespace) for the firewall. On the PC (no such tools)
the checks are skipped; in the CI (KBOT_CI) they are required.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from kbot.ops import check

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
FIREWALL = ROOT / "tests" / "fixtures" / "firewall"  # recorded on the instance, 16 Nov 2026
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


def run(*command: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, input=input_text)


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


def test_the_units_are_verified_only_when_everything_they_need_exists() -> None:
    """Reviews of E4a (E4a-02) and E4b (E4b-02): 01 installs the units, 02 the code, 03
    PostgreSQL, and only 04 verifies them, when the interpreter and postgresql.service are
    both in place."""
    scripts = sorted(p.name for p in (DEPLOY / "install").glob("0*.sh"))
    assert scripts == [
        "01_sistema.sh",
        "02_codigo.sh",
        "03_postgresql.sh",
        "04_verificar.sh",
        "05_copias.sh",  # stage E4c, after the verification
    ]
    system = (DEPLOY / "install" / "01_sistema.sh").read_text(encoding="utf-8")
    verify = (DEPLOY / "install" / "04_verificar.sh").read_text(encoding="utf-8")
    for name in scripts[:3]:
        text = (DEPLOY / "install" / name).read_text(encoding="utf-8")
        assert "systemd-analyze verify" not in text, name
    assert "systemd-analyze verify" in verify
    assert "kbot.ops.pgcheck" in verify and "f1a6_servidor.sh" in verify and "-m kbot.ops" in verify
    # E4a-01: the guard never goes through /etc/nftables.conf (Ubuntu's flushes everything).
    assert "/etc/nftables.conf" not in system
    assert "kbot-metadata-guard" in system
    # E4a-01, residual: the generic nftables.service is retired before the firewall is
    # loaded, because stopping it flushes the whole ruleset.
    assert system.index("systemctl disable --now nftables") < system.index(
        'iptables-restore <"$v4"'
    )
    # Owner decisions of 16 and 17 Nov 2026: the firewall of the Oracle image, never ufw,
    # and its IPv4 file only read, never written (review FW-02); only rules.v6 is installed.
    commands = [line for line in system.splitlines() if not line.lstrip().startswith("#")]
    assert not any("ufw" in line for line in commands)
    # The installation on the instance (17 Nov 2026): the image makes `netfilter-persistent
    # reload` append instead of replace, and `iptables -S | grep -q` breaks under pipefail.
    assert not any("netfilter-persistent reload" in line for line in commands)
    assert 'iptables-restore <"$v4"' in system and "--noflush" not in "\n".join(commands)
    assert not any("iptables -S" in line and "| grep" in line for line in commands)
    # Only SSH listens: the image's rpcbind (port 111, for NFS) is stopped and masked.
    assert "systemctl mask rpcbind.socket rpcbind.service" in system
    # And commands run as postgres need a current directory they can read (Alembic looks
    # for pyproject.toml there): 03 and 04 run from the clone.
    postgres = (DEPLOY / "install" / "03_postgresql.sh").read_text(encoding="utf-8")
    assert postgres.index('cd "$repo"') < postgres.index('alembic" -c')
    assert verify.index('cd "$repo"') < verify.index("kbot.ops.pgcheck")
    v4_uses = [line.strip() for line in commands if '"$v4"' in line]
    assert v4_uses and all("grep" in u or u == 'iptables-restore <"$v4"' for u in v4_uses)
    assert "owner_ip" not in system and "rules.v4.oracle" not in system
    assert "ip6tables-restore --test" in system and "/etc/iptables/rules.v6" in system
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
    assert "After=netfilter-persistent.service" in guard and "RemainAfterExit=yes" in guard


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
    # Root and snap_daemon (the Oracle Cloud Agent) by their fixed ids and, from E4c-2,
    # kbot_upload by name, in IPv4 and IPv6; never kbot_backup.
    assert text.count("meta skuid != { 0, 584788, kbot_upload } counter reject") == 2
    assert "kbot_backup }" not in text
    nft = tool("nft")
    # nft resolves the name when it reads the rule: the user must exist, as on the server.
    made = run(*as_root("bash", "-c", _USER.replace("@USER@", "kbot_upload")))
    assert made.returncode == 0, made.stderr
    done = run(*as_root(nft, "-c", "-f", str(guard)))
    assert done.returncode == 0, done.stderr


def test_the_guard_check_reads_the_real_json_of_the_kernel(tmp_path: Path) -> None:
    """E4C2-07: the guard loaded by the kernel, in a throwaway network namespace, with
    kbot_upload created with an id that is not fixed; ``nft -j`` prints it by number, and
    the check names it through /etc/passwd. With another user in its place it fails."""
    nft = tool("nft")
    unshare = tool("unshare")
    for user in ("kbot_upload", "kbot_backup"):
        made = run(*as_root("bash", "-c", _USER.replace("@USER@", user)))
        assert made.returncode == 0, made.stderr
    names = check.uid_names(Path("/etc/passwd").read_text(encoding="utf-8"))
    assert {"kbot_upload", "kbot_backup"} <= set(names.values())
    guard = DEPLOY / "etc" / "kbot" / "metadata-guard.nft"
    swapped = tmp_path / "swapped.nft"
    swapped.write_text(
        guard.read_text(encoding="utf-8").replace("kbot_upload }", "kbot_backup }"),
        encoding="utf-8",
    )
    swapped.chmod(0o644)
    tmp_path.chmod(0o755)
    verdicts = []
    for rules in (guard, swapped):
        listed = run(
            *as_root(
                unshare,
                "--net",
                "sh",
                "-c",
                f"{nft} -f {rules} && {nft} -j list table inet kbot_guard",
            )
        )
        assert listed.returncode == 0, listed.stderr
        verdicts.append(check.metadata_guard(listed.stdout, names=names))
    assert verdicts[0].ok, verdicts[0].detail
    assert not verdicts[1].ok and "kbot_backup" in verdicts[1].detail


_USER = (
    "getent passwd @USER@ >/dev/null || "
    "useradd --system --shell /usr/sbin/nologin --no-create-home @USER@"
)


def test_uv_checksum_is_pinned_for_the_server() -> None:
    """Review of E4a, E4a-07: the checksum of the pinned uv tarball lives in the repository."""
    text = (DEPLOY / "install" / "uv.sha256").read_text(encoding="utf-8")
    digest, _, name = text.split()[0], None, text.split()[-1]
    assert len(digest) == 64 and int(digest, 16)
    assert name == "uv-aarch64-unknown-linux-gnu.tar.gz"
    code = (DEPLOY / "install" / "02_codigo.sh").read_text(encoding="utf-8")
    assert "uv.sha256" in code and "KBOT_UV_SHA256" not in code


def test_the_local_backups_run_without_network_and_keep_no_plain_dump() -> None:
    """E4c-1 (02 §13.1, point 4): the hourly copy as kbot_backup and the weekly global
    objects as postgres, neither with any network (only the socket of PostgreSQL); the
    installation stops on a private key or a plain dump left on the disk."""
    backup = (DEPLOY / "systemd" / "kbot-backup.service").read_text(encoding="utf-8")
    previa = (DEPLOY / "systemd" / "kbot-backup-previa.service").read_text(encoding="utf-8")
    globals_unit = (DEPLOY / "systemd" / "kbot-backup-globals.service").read_text(encoding="utf-8")
    assert "User=kbot_backup" in backup and "-m kbot.backup run\n" in backup
    assert "User=kbot_backup" in previa and "-m kbot.backup run --previa\n" in previa
    assert "User=postgres" in globals_unit and "-m kbot.backup globals" in globals_unit
    for unit in (backup, previa, globals_unit):
        assert "IPAddressDeny=any" in unit and "RestrictAddressFamilies=AF_UNIX\n" in unit
        assert "@resources" not in unit.split("[Service]")[1].replace("#", "")  # age is Go
        assert "UMask=0027" in unit  # E4c-2: the group kbot_upload reads each copy
    assert "ReadWritePaths=/srv/kbot/backups\n" in backup
    assert "ReadWritePaths=/srv/kbot/backups\n" in previa
    assert "ReadWritePaths=/srv/kbot/backups/globales\n" in globals_unit
    timer = (DEPLOY / "systemd" / "kbot-backup.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* *:40:00" in timer  # far from the archive at 00:20 UTC
    install = (DEPLOY / "install" / "05_copias.sh").read_text(encoding="utf-8")
    assert "AGE-SECRET-KEY" in install and "volcado.dump" in install
    assert "install -d -o postgres -g kbot_upload -m 2750" in install
    assert "SupplementaryGroups=kbot_upload" in globals_unit


# As root: the directories of the copies and of the uploads with the very lines of
# 05_copias.sh, and the real users (02 §13.1, point 4, E4c-2, point 7). Each writer runs as
# its unit runs it, with UMask=0027 (postgres with the group of its unit). kbot_upload
# reads every copy and the register and cannot change any of them; kbot_backup reads the
# state of the uploads and cannot change it; postgres without its group does not even
# reach its directory (review of E4c-1, E4c1-01).
_LAYOUT = r"""
set -e
root="$(mktemp -d /tmp/kbot-copias.XXXXXX)"
trap 'rm -rf "$root"' EXIT
chmod 0755 "$root"
for user in kbot_backup kbot_upload; do
  getent passwd "$user" >/dev/null ||
    useradd --system --shell /usr/sbin/nologin --no-create-home "$user"
done
getent passwd postgres >/dev/null
backups="$root/backups"
uploads="$root/subidas"
@LAYOUT@
as() { user="$1"; shift; setpriv --reuid="$user" --regid="$user" --init-groups -- "$@"; }
must_fail() { if "$@" 2>/dev/null; then echo "allowed: $*"; exit 1; fi; }
if setpriv --reuid=postgres --regid=postgres --clear-groups -- \
  ls "$backups/globales" 2>/dev/null; then
  echo "postgres reached the globals without the group of its unit"
  exit 1
fi
setpriv --reuid=postgres --regid=postgres --groups=kbot_upload -- sh -c '
  umask 0027
  d="$1/globales"
  mkdir "$d/.globales-1.tmp"
  echo cifrado > "$d/.globales-1.tmp/volcado.age"
  mv "$d/.globales-1.tmp" "$d/globales-1"
' sh "$backups"
as kbot_backup sh -c '
  umask 0027
  mkdir "$1/.caliente-1.tmp"
  echo cifrada > "$1/.caliente-1.tmp/volcado.age"
  mv "$1/.caliente-1.tmp" "$1/caliente-1"
  echo "{}" >> "$1/registro.jsonl"
' sh "$backups"
as kbot_upload sh -c 'umask 0027; echo "{}" > "$1/estado.json"' sh "$uploads"
as kbot_upload cat "$backups/globales/globales-1/volcado.age" "$backups/caliente-1/volcado.age"
as kbot_upload cat "$backups/registro.jsonl" >/dev/null
must_fail as kbot_upload touch "$backups/x"
must_fail as kbot_upload touch "$backups/caliente-1/x"
must_fail as kbot_upload sh -c "echo x >> '$backups/caliente-1/volcado.age'"
must_fail as kbot_upload touch "$backups/globales/x"
as kbot_backup cat "$uploads/estado.json" >/dev/null
must_fail as kbot_backup touch "$uploads/x"
must_fail as kbot_backup sh -c "echo x >> '$uploads/estado.json'"
stat -c 'mode %n %G %a' "$backups/globales/globales-1/volcado.age" \
  "$backups/caliente-1" "$backups/caliente-1/volcado.age" "$uploads/estado.json"
"""


def test_the_copies_and_the_state_of_the_uploads_have_the_real_modes() -> None:
    tool("setpriv")
    install = (DEPLOY / "install" / "05_copias.sh").read_text(encoding="utf-8")
    layout = [
        line.strip()
        for line in install.splitlines()
        if line.strip().startswith("install -d -o") and ('"$backups' in line or '"$uploads' in line)
    ]
    assert len(layout) == 3, layout  # the copies, the global objects and the uploads
    done = run(*as_root("bash", "-c", _LAYOUT.replace("@LAYOUT@", "\n".join(layout))))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "cifrado\ncifrada" in done.stdout, done.stdout
    for line in (
        "globales-1/volcado.age kbot_upload 640",
        "caliente-1 kbot_upload 2750",
        "caliente-1/volcado.age kbot_upload 640",
        "estado.json kbot_backup 640",
    ):
        assert line in done.stdout, done.stdout


def test_the_uploads_have_network_and_no_database_and_the_backups_the_opposite() -> None:
    """E4c-2 (02 §13.1, point 4, E4c-2, point 1): kbot_upload, with no role in PostgreSQL,
    reaches Oracle's public endpoints and the metadata, and nothing private or link-local
    else; it writes only its state. The timer runs it after the hot copy."""
    unit = (DEPLOY / "systemd" / "kbot-upload.service").read_text(encoding="utf-8")
    assert "User=kbot_upload" in unit and "-m kbot.upload run\n" in unit
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n" in unit
    deny = next(line for line in unit.splitlines() if line.startswith("IPAddressDeny="))
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"):
        assert network in deny
    assert "IPAddressAllow=169.254.169.254\n" in unit
    assert "ReadWritePaths=/srv/kbot/subidas\n" in unit and "UMask=0027" in unit
    assert "kbot.backup" not in unit and "SupplementaryGroups" not in unit
    timer = (DEPLOY / "systemd" / "kbot-upload.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* *:55:00" in timer
    roles = (ROOT / "sql" / "roles.sql").read_text(encoding="utf-8")
    assert "kbot_upload" not in roles  # no role in PostgreSQL
    # The user exists before the guard names it: in 01 for a new server, in 05 for this one.
    system = (DEPLOY / "install" / "01_sistema.sh").read_text(encoding="utf-8")
    assert system.index("kbot_backup kbot_upload") < system.index("metadata-guard.nft")
    install = (DEPLOY / "install" / "05_copias.sh").read_text(encoding="utf-8")
    assert (
        install.index("useradd")
        < install.index("nft -c -f")
        < install.index("systemctl reload kbot-metadata-guard")
    )
    assert "kbot-guardcheck@$user" in install and "kbot_upload kbot_backup" in install
    assert install.index("systemctl start kbot-upload.service") < install.index("enable --now")
    # From E4c-2 every update starts with the self-contained copy (E4c-2, point 4).
    code = (DEPLOY / "install" / "02_codigo.sh").read_text(encoding="utf-8")
    assert code.index("kbot-backup-previa.service") < code.index("checkout --quiet")


def test_backup_recipients_are_two_public_keys_and_no_secret() -> None:
    """E4c (02 §13.1, point 4): the copies are encrypted to the principal and the recovery
    key; the repository and the server only ever hold the public halves."""
    text = (DEPLOY / "etc" / "kbot" / "age-recipients.txt").read_text(encoding="utf-8")
    assert "AGE-SECRET-KEY" not in text.upper()
    keys = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert len(keys) == 2 and len(set(keys)) == 2
    assert all(re.fullmatch(r"age1[02-9ac-hj-np-z]{58}", key) for key in keys)


def test_tmpfiles_creates_the_shared_lock_owned_by_root() -> None:
    text = (DEPLOY / "etc" / "tmpfiles.d" / "kbot.conf").read_text(encoding="utf-8")
    assert "d /run/kbot 0750 root kbot" in text
    assert "f /run/kbot/kucoin-rest.lock 0660 root kbot" in text


def test_postgresql_production_files_match_02() -> None:
    """Stage E4b: no TCP, peer only without a map, the lock table of 02 §3.9."""
    hba = (DEPLOY / "etc" / "postgresql" / "pg_hba.conf").read_text(encoding="utf-8")
    rules = [line.split() for line in hba.splitlines() if line.strip() and not line.startswith("#")]
    assert rules == [["local", "all", "all", "peer"]]
    conf = (DEPLOY / "etc" / "postgresql" / "kbot.conf").read_text(encoding="utf-8")
    settings = dict(
        line.split("=", 1)
        for line in conf.splitlines()
        if line.strip() and not line.startswith("#")
    )
    settings = {k.strip(): v.strip() for k, v in settings.items()}
    assert settings["listen_addresses"] == "''"
    assert settings["max_locks_per_transaction"] == "256"
    assert settings["timezone"] == "'UTC'" and settings["log_timezone"] == "'UTC'"
    assert settings["log_statement"] == "'ddl'"
    script = (DEPLOY / "install" / "03_postgresql.sh").read_text(encoding="utf-8")
    assert "kbot.ops.bootstrap" in script  # E4b-03: resumable, never `psql -f roles.sql`
    assert 'roles.sql"' in script and "psql_admin -f" not in script
    assert "SET ROLE" not in script  # the migrations do it themselves (migrations/env.py)


def test_f1a6_counts_a_refusal_only_by_its_sqlstate() -> None:
    """Review of E4b, E4b-01: psql -c exits 1 on an SQL error, so exit codes cannot tell a
    permission refusal from any other error; the script reads the SQLSTATE."""
    text = (DEPLOY / "install" / "f1a6_servidor.sh").read_text(encoding="utf-8")
    assert "VERBOSITY=sqlstate" in text
    assert text.count('"SQLSTATE=42501"') >= 3
    assert '" 3 "' not in text and 'expect "$user cannot create in $schema" 3' not in text
    assert "ROLLBACK" in text  # nothing created by the test stays
    assert "SHOW statement_timeout" in text  # E4b-05: the timeout a real session gets


def test_ipv6_rules_close_input_and_load() -> None:
    """Owner decision of 16 Nov 2026: IPv6 closed, a second barrier."""
    text = (DEPLOY / "etc" / "iptables" / "rules.v6").read_text(encoding="utf-8")
    assert ":INPUT DROP" in text and ":FORWARD DROP" in text
    rules = [line for line in text.splitlines() if line.startswith("-A INPUT")]
    assert rules == [
        "-A INPUT -i lo -j ACCEPT",
        "-A INPUT -m state --state RELATED,ESTABLISHED -j ACCEPT",
        "-A INPUT -p ipv6-icmp -j ACCEPT",
    ]
    restore = tool("ip6tables-restore")
    done = run(*as_root(restore, "--test"), input_text=text)
    assert done.returncode == 0, done.stderr


def test_the_file_of_the_image_passes_the_check_of_01() -> None:
    """01_sistema.sh stops unless /etc/iptables/rules.v4 is still the image's: the file
    recorded on the instance (16 Nov 2026) meets its three conditions."""
    system = (DEPLOY / "install" / "01_sistema.sh").read_text(encoding="utf-8")
    header = "# iptables configuration for Oracle Cloud Infrastructure"
    iscsi = "-A InstanceServices -d 169.254.0.2/32 .*--dport 3260"
    assert f"grep -q '^{header}' \"$v4\"" in system
    assert f"grep -q -- '{iscsi}' \"$v4\"" in system
    ssh_image = re.search(r"^ssh_image='(.*)'$", system, re.MULTILINE)
    assert ssh_image is not None
    lines = (FIREWALL / "oracle_rules.v4").read_text(encoding="utf-8").splitlines()
    assert any(line.startswith(header) for line in lines)
    assert lines.count(ssh_image[1]) == 1
    assert any(re.search(iscsi, line) for line in lines)


# As root in a throwaway network namespace: the file of the image (as recorded on the
# instance) and rules.v6, loaded as 01_sistema.sh loads them; then what fail2ban's
# iptables-multiport action runs on its first ban (port "ssh", as in the jail); then the
# reviewer's example of FW-01; then the file loaded again as 01 does, and once more as the
# image's `netfilter-persistent reload` does (--noflush, from /etc/default). Each `-S`
# comes after a marker line.
_NETNS = r"""
set -e
iptables-restore < "$1"
ip6tables-restore < "$2"
echo '#image'; iptables -S; echo '#image6'; ip6tables -S
for ipt in iptables ip6tables; do
  $ipt -N f2b-sshd
  $ipt -A f2b-sshd -j RETURN
  $ipt -I INPUT -p tcp -m multiport --dports ssh -j f2b-sshd
done
iptables -I f2b-sshd 1 -s 203.0.113.7 -j REJECT --reject-with icmp-port-unreachable
ip6tables -I f2b-sshd 1 -s 2001:db8::7 -j REJECT --reject-with icmp6-port-unreachable
echo '#banned'; iptables -S; echo '#banned6'; ip6tables -S
iptables -I f2b-sshd 1 -j ACCEPT
echo '#accept'; iptables -S
iptables-restore < "$1"
echo '#reloaded'; iptables -S
iptables-restore --noflush < "$1"
echo '#noflush'; iptables -S
"""


def test_the_firewall_check_reads_what_iptables_really_prints() -> None:
    """Review FW-01: kbot.ops.check against the iptables of Ubuntu 24.04 (the runner's,
    the same version as the server's), not only against texts written by hand."""
    unshare = tool("unshare")
    tool("iptables-restore")
    image_file = FIREWALL / "oracle_rules.v4"
    rules_v6 = DEPLOY / "etc" / "iptables" / "rules.v6"
    done = run(
        *as_root(unshare, "--net", "bash", "-c", _NETNS, "netns", str(image_file), str(rules_v6))
    )
    assert done.returncode == 0, done.stderr
    printed: dict[str, list[str]] = {}
    current: list[str] = []
    for line in done.stdout.splitlines():
        if line.startswith("#"):
            current = printed.setdefault(line[1:], [])
        else:
            current.append(line)
    text = {name: "\n".join(lines) + "\n" for name, lines in printed.items()}
    # The runner prints for the image's file exactly what the instance printed.
    assert text["image"] == (FIREWALL / "oracle_iptables-S.txt").read_text(encoding="utf-8")
    image = check.firewall_status(text["image"], text["image6"])
    assert image.ok, image.detail
    banned = check.firewall_status(text["banned"], text["banned6"])
    assert banned.ok and "fail2ban: 2" in banned.detail, banned.detail
    opened = check.firewall_status(text["accept"], text["banned6"])
    assert not opened.ok and "not a ban of one address: '-j ACCEPT'" in opened.detail
    # Loading the file again as 01 does replaces everything, fail2ban's chain included;
    # the reload of the image appends a second copy of every rule of the built-in chains.
    assert text["reloaded"] == text["image"]
    twice = check.firewall_status(text["noflush"], text["image6"])
    assert not twice.ok and "IPv4 INPUT: unexpected rule 6" in twice.detail, twice.detail
