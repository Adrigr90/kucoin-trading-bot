"""The external disk from end to end, in the CI (02 §13.1, point 4, E4c-3, point 7).

As root: the real Match block of sshd for kbot_sync in a test sshd on 127.0.0.1:2222, the
chroot /srv/kbot/sftp with its three read-only bind mounts and the 256 MiB image of
recibos/. A day is sealed and archived in PostgreSQL; a sealed-day copy and a hot copy that
relies on it are published. Then the owner's script, tools/pc/sincronizar.ps1 under pwsh,
makes two visits, and the verifier runs after each: the first gives ``replicado_fuera`` and
the challenges, the second answers them from the disk and gives ``verificado_fuera`` and the
recoverable point. What kbot_sync must not do is tried through the same sftp.

The verifier runs in the test, as the runner with the role kbot_archive (the only OS user
the CI maps to the roles), so recibos/ belongs here to the runner's group instead of
kbot_archive; the real ownership of the server is checked by 06_externo.sh, as each user.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import psycopg
import pytest

from kbot.archive import archiver, externo
from kbot.backup import local
from tests.db.test_backup import sealed_day
from tests.unit.upload.support import publish

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "pc" / "sincronizar.ps1"
DROP_IN = ROOT / "deploy" / "etc" / "ssh" / "sshd_config.d" / "10-kbot.conf"
DAY = date(2015, 4, 4)
Connect = Callable[..., psycopg.Connection]

SETUP = r"""
set -euo pipefail
group="$(id -gn "$RUNNER")"
getent passwd kbot_sync >/dev/null ||
  useradd --system --shell /usr/sbin/nologin --no-create-home kbot_sync
getent group kbot_upload >/dev/null || groupadd --system kbot_upload
usermod -a -G kbot_upload kbot_sync
install -d -o root -g root -m 0755 /srv /srv/kbot /srv/kbot/sftp /etc/kbot
install -d -o "$RUNNER" -g kbot_upload -m 2755 /srv/kbot/backups
install -d -o "$RUNNER" -g kbot_sync -m 2750 /srv/kbot/archive
install -d -o "$RUNNER" -g "$group" -m 0700 /srv/kbot/externo
install -d -o "$RUNNER" -g kbot_sync -m 2750 /srv/kbot/externo/publico
install -d -o "$RUNNER" -g "$group" -m 0700 /srv/kbot/externo/privado
truncate -s 256M /srv/kbot/externo/recibos.img
mkfs.ext4 -q -F /srv/kbot/externo/recibos.img
for view in copias archivo desafios recibos; do
  install -d -o root -g root -m 0755 /srv/kbot/sftp/$view
done
mount --bind -o ro /srv/kbot/backups /srv/kbot/sftp/copias
mount -o remount,bind,ro /srv/kbot/sftp/copias
mount --bind -o ro /srv/kbot/archive /srv/kbot/sftp/archivo
mount -o remount,bind,ro /srv/kbot/sftp/archivo
mount --bind -o ro /srv/kbot/externo/publico /srv/kbot/sftp/desafios
mount -o remount,bind,ro /srv/kbot/sftp/desafios
mount -o loop,nodev,nosuid,noexec /srv/kbot/externo/recibos.img /srv/kbot/sftp/recibos
chown kbot_sync:"$group" /srv/kbot/sftp/recibos
chmod 2750 /srv/kbot/sftp/recibos
install -o root -g root -m 0644 "$KEY" /etc/kbot/kbot_sync_authorized_keys
cat > "$CONFIG" <<EOF
Port 2222
ListenAddress 127.0.0.1
HostKey $HOSTKEY
PidFile $PIDFILE
UsePAM yes
Include $DROPIN
EOF
/usr/sbin/sshd -t -f "$CONFIG"
/usr/sbin/sshd -f "$CONFIG"
"""

TEARDOWN = r"""
set +e
[ -s "$PIDFILE" ] && kill "$(cat "$PIDFILE")"
for view in copias archivo desafios recibos; do umount /srv/kbot/sftp/$view; done
rm -rf /srv/kbot /etc/kbot/kbot_sync_authorized_keys
true
"""


def tool(name: str) -> str:
    path = shutil.which(name) or shutil.which(name, path="/usr/sbin:/sbin")
    if path is None:
        pytest.fail(f"{name} is required by the end-to-end test of the external disk")
    return path


def as_root(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sudo", "-n", "env", *(f"{k}={v}" for k, v in env.items()), "bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def server(tmp_path: Path) -> Iterator[dict[str, Path]]:
    if os.name == "nt":
        pytest.skip("the server side is Linux")
    import pwd

    for name in ("sudo", "sshd", "sftp", "ssh-keygen", "pwsh", "mkfs.ext4"):
        tool(name)
    keys = tmp_path / "claves"
    keys.mkdir()
    key, host = keys / "kbot_sync", keys / "host"
    for path in (key, host):
        made = subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
            capture_output=True,
            check=False,
        )
        assert made.returncode == 0, made.stderr
    known = keys / "known_hosts"
    known.write_text("[127.0.0.1]:2222 " + (keys / "host.pub").read_text(), encoding="utf-8")
    env = {
        "RUNNER": pwd.getpwuid(os.getuid()).pw_name,
        "KEY": str(keys / "kbot_sync.pub"),
        "HOSTKEY": str(host),
        "PIDFILE": str(tmp_path / "sshd.pid"),
        "CONFIG": str(tmp_path / "sshd_config"),
        "DROPIN": str(DROP_IN),
    }
    done = as_root(SETUP, env)
    assert done.returncode == 0, done.stdout + done.stderr
    try:
        yield {"key": key, "known": known, "disk": tmp_path / "disco"}
    finally:
        as_root(TEARDOWN, env)


def visit(server: dict[str, Path], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT),
            "-Destino",
            str(server["disk"]),
            "-Clave",
            str(server["key"]),
            "-Servidor",
            "127.0.0.1",
            "-Puerto",
            "2222",
            "-KnownHosts",
            str(server["known"]),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )


def sftp(server: dict[str, Path], *commands: str) -> subprocess.CompletedProcess[str]:
    batch = server["disk"].parent / "lote.txt"
    batch.write_text("\n".join(commands) + "\n", encoding="utf-8")
    return subprocess.run(
        [
            "sftp",
            "-b",
            str(batch),
            "-i",
            str(server["key"]),
            "-P",
            "2222",
            "-o",
            "BatchMode=yes",
            "-o",
            f"UserKnownHostsFile={server['known']}",
            "-o",
            "StrictHostKeyChecking=yes",
            "kbot_sync@127.0.0.1",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def states(connect: Connect, generation: str) -> set[str]:
    with connect("kbot_archive") as conn:
        rows = conn.execute(
            "SELECT state FROM market.archive_generation_states WHERE generation_id = %s",
            (generation,),
        ).fetchall()
    return {row[0] for row in rows}


def test_two_visits_of_the_pc_verify_a_generation_through_the_real_sftp(
    connect: Connect, partitions: Callable[[date, date], None], server: dict[str, Path]
) -> None:
    import pwd  # only on Linux, like the server

    sealed_day(connect, partitions, DAY)
    archiver.archive_pending(lambda: connect("kbot_archive"), Path("/srv/kbot/archive"))
    with connect("kbot_archive") as conn:
        row = conn.execute(
            "SELECT generation_id FROM market.archive_generations WHERE day = %s", (DAY,)
        ).fetchone()
    assert row is not None
    generation = str(row[0])
    backups = Path("/srv/kbot/backups")
    day_copy = publish(backups, "dia-2015-04-04", b"day copy")
    identity = {
        "copia": day_copy.path.name,
        "objeto": "dia/dia-2015-04-04/volcado.age",
        "sha256": day_copy.manifest["volcado"]["sha256_cifrado"],
        "objeto_manifiesto": "dia/dia-2015-04-04/manifiesto.json",
        "sha256_manifiesto": local.sha256_file(day_copy.path / local.MANIFEST),
        "verificada": "2015-04-08T00:55:00+00:00",
    }
    hot = publish(
        backups,
        "caliente-20150408T004000Z",
        b"hot copy",
        dias_excluidos=[{"dia": "2015-04-04", "copia_de_dia": identity, "generacion": generation}],
    )
    verifier = externo.Verifier(
        lambda: connect("kbot_archive"), owner=pwd.getpwnam("kbot_sync").pw_uid
    )

    # Without the disk, nothing happens.
    missing = visit(server)
    assert missing.returncode != 0 and not server["disk"].exists(), missing.stdout

    verifier.run()  # publishes the index before the first visit
    first = visit(server, "-Inicializar")
    assert first.returncode == 0, first.stdout + first.stderr
    disk = server["disk"]
    assert (disk / "copias" / hot.path.name / "volcado.age").read_bytes() == b"hot copy"
    assert (disk / "copias" / day_copy.path.name / "volcado.age").read_bytes() == b"day copy"
    report = verifier.run()
    assert generation in report.accepted, report
    assert report.points == [hot.path.name], report
    assert "replicado_fuera" in states(connect, generation)
    assert "verificado_fuera" not in states(connect, generation)

    second = visit(server)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Desafíos respondidos:" in second.stdout
    report = verifier.run()
    assert generation in report.verified, report
    assert "verificado_fuera" in states(connect, generation)
    assert "elegible_para_exclusion_backup" not in states(connect, generation)
    state = json.loads(Path("/srv/kbot/externo/privado/estado.json").read_text(encoding="utf-8"))
    [record] = state["discos"].values()
    assert record["punto"]["copia"] == hot.path.name

    # What kbot_sync cannot do, through the very same SFTP.
    probe = server["disk"].parent / "probe.txt"
    probe.write_text("x", encoding="utf-8")
    for command in (f"put {probe} copias/x", f"put {probe} archivo/x", f"put {probe} desafios/x"):
        assert sftp(server, command).returncode != 0, command
    assert sftp(server, f"put {probe} recibos/x", "rm recibos/x").returncode == 0
    assert sftp(server, f"put {probe} recibos/a", "symlink recibos/a recibos/b").returncode != 0
    assert sftp(server, "cd /srv/kbot/backups").returncode != 0  # the chroot hides the server
    shell = subprocess.run(
        [
            "ssh",
            "-i",
            str(server["key"]),
            "-p",
            "2222",
            "-o",
            "BatchMode=yes",
            "-o",
            f"UserKnownHostsFile={server['known']}",
            "kbot_sync@127.0.0.1",
            "id",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    assert "uid=" not in shell.stdout, shell.stdout  # only SFTP: no command runs
    tunnel = subprocess.run(
        [
            "ssh",
            "-i",
            str(server["key"]),
            "-p",
            "2222",
            "-o",
            "BatchMode=yes",
            "-o",
            f"UserKnownHostsFile={server['known']}",
            "-W",
            "127.0.0.1:5432",
            "kbot_sync@127.0.0.1",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    assert tunnel.returncode != 0  # no forwarding of any kind
    assert hashlib.sha256(b"day copy").hexdigest() == identity["sha256"]
