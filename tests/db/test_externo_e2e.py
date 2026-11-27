"""The external disk from end to end, in the CI (02 §13.1, point 4, E4c-3, point 7).

As root: the real Match block of sshd for kbot_sync in a test sshd on 127.0.0.1:2222, the
chroot /srv/kbot/sftp with its three read-only bind mounts and the 256 MiB image of
recibos/. A day is sealed and archived in PostgreSQL; a sealed-day copy and a hot copy that
relies on it are published. Then the owner's script, tools/pc/sincronizar.ps1 under pwsh,
makes three visits, and the verifier runs after each: the first gives ``replicado_fuera``,
the challenges and the recoverable point; the second answers the challenges from the disk
and gives ``verificado_fuera``, while a newer hot copy that relies on another identity of
its day copy (S1 in the bucket, S2 on the server: E4C3-02) does not move the point; the
third gets a hot copy that does not match its register, which is not kept and does not move
the point either (E4C3-03). What kbot_sync must not do is tried through the same sftp.

The verifier runs in the test as the runner with the role kbot_archive (the only OS user
the CI maps to the roles), so recibos/ belongs here to the runner's group instead of
kbot_archive; the real ownership of the server is checked by 06_externo.sh, as each user.
What the verifier reads, though, is tried as its unit runs it (E4C3-07): the OS user
kbot_archive with the group kbot_upload validates a recoverable point from the register and
the manifest, and can modify no copy nor read /srv/kbot/subidas.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

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
getent group kbot_upload >/dev/null || groupadd --system kbot_upload
getent group kbot_backup >/dev/null || groupadd --system kbot_backup
for user in kbot_sync kbot_archive; do
  getent passwd "$user" >/dev/null ||
    useradd --system --shell /usr/sbin/nologin --no-create-home "$user"
done
getent passwd kbot_upload >/dev/null ||
  useradd --system --shell /usr/sbin/nologin --no-create-home -g kbot_upload kbot_upload
usermod -a -G kbot_upload kbot_sync
install -d -o root -g root -m 0755 /srv /srv/kbot /srv/kbot/sftp /etc/kbot
install -d -o "$RUNNER" -g kbot_upload -m 2750 /srv/kbot/backups
install -d -o kbot_upload -g kbot_backup -m 2750 /srv/kbot/subidas
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
install -d -o root -g root -m 0755 /run/sshd  # sshd's privilege separation
cat > "$CONFIG" <<EOF
Port 2222
ListenAddress 127.0.0.1
HostKey $HOSTKEY
PidFile $PIDFILE
UsePAM yes
# The line of the image of Ubuntu 24.04: ForceCommand needs a subsystem to replace.
Subsystem sftp /usr/lib/openssh/sftp-server
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


# E4C3-07, as root: the identity of kbot-archive-externo.service (User=kbot_archive,
# SupplementaryGroups=kbot_upload). The home of the runner is not open to other users on the
# images of GitHub, so only the traversal (o+x) of the ancestors of the repository and of the
# interpreter is opened, and closed again at the end.
AS_THE_VERIFIER = r"""
set -euo pipefail
opened=()
restore() { for p in "${opened[@]}"; do chmod o-x "$p"; done; }
trap restore EXIT
for path in "$REPO" "$PYTHON" "$BASE"; do
  p="$path"
  while [ "$p" != / ]; do
    p="$(dirname "$p")"
    if (( (8#$(stat -c %a "$p") & 1) == 0 )); then chmod o+x "$p"; opened+=("$p"); fi
  done
done
setpriv --reuid=kbot_archive --regid=kbot_archive --groups=kbot_upload -- \
  "$PYTHON" -B -c "$CODE" "$HOT" "$DISK"
if setpriv --reuid=kbot_archive --regid=kbot_archive --init-groups -- \
  cat /srv/kbot/backups/registro.jsonl >/dev/null 2>&1; then
  echo "kbot_archive read the register without the group of its unit" >&2
  exit 1
fi
"""

VERIFIER_CODE = """
import base64, hashlib, json, os, sys
from pathlib import Path
from kbot.archive import externo
name, disk = sys.argv[1], sys.argv[2]
copies = Path("/srv/kbot/backups")
dump = (copies / name / "volcado.age").read_bytes()
manifest = (copies / name / "manifiesto.json").read_bytes()
held = [
    {k: item["copia_de_dia"][k] for k in ("copia", "sha256", "sha256_manifiesto")}
    for item in json.loads(manifest)["dias_excluidos"]
]
verifier = externo.Verifier(None, base=Path("/nonexistent"), copies=copies)
verifier._admit(disk)
report = externo.Report()
verifier._point(
    {
        "tipo": "punto", "disco": disk, "copia": name,
        "sha256": hashlib.sha256(dump).hexdigest(),
        "sha256_manifiesto": hashlib.sha256(manifest).hexdigest(),
        "manifiesto_base64": base64.b64encode(manifest).decode(),
        "dependencias": held, "sin_errores": True, "secuencia": 1,
    },
    "0" * 64,
    report,
    f"recibo_punto_{disk}.json",
)
assert report.points == [name], report
for attempt in (
    lambda: (copies / "x").touch(),
    lambda: (copies / name / "volcado.age").open("ab").close(),
    lambda: os.listdir("/srv/kbot/subidas"),
):
    try:
        attempt()
    except PermissionError:
        continue
    raise SystemExit("kbot_archive was allowed something it must not do")
print("point valid as kbot_archive")
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

    for name in ("sudo", "sshd", "sftp", "ssh-keygen", "pwsh", "mkfs.ext4", "setpriv"):
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


def disk_record() -> dict[str, Any]:
    state = json.loads(Path("/srv/kbot/externo/privado/estado.json").read_text(encoding="utf-8"))
    [record] = state["discos"].values()
    return dict(record)


def states(connect: Connect, generation: str) -> set[str]:
    with connect("kbot_archive") as conn:
        rows = conn.execute(
            "SELECT state FROM market.archive_generation_states WHERE generation_id = %s",
            (generation,),
        ).fetchall()
    return {row[0] for row in rows}


def test_two_visits_of_the_pc_verify_a_generation_through_the_real_sftp(
    connect: Connect,
    partitions: Callable[[date, date], None],
    server: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pwd  # only on Linux, like the server

    # The database of the CI is shared with the other tests, whose generations keep their
    # files in their own directories: the verifier sees here only the day of this test.
    every = externo.server_generations
    monkeypatch.setattr(
        externo,
        "server_generations",
        lambda conn: {g: v for g, v in every(conn).items() if v[0] == DAY},
    )

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

    # Without the disk, or with a folder that is not the disk, nothing is written.
    missing = visit(server)
    assert missing.returncode != 0 and not server["disk"].exists(), missing.stdout
    server["disk"].mkdir()
    unmarked = visit(server)
    assert unmarked.returncode != 0 and not any(server["disk"].iterdir()), unmarked.stdout

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

    # E4C3-02: a newer hot copy fixes its day copy with the identity S1, which only the
    # bucket has; the server and the disk have S2. The disk does not credit it.
    s1 = dict(identity, sha256="0" * 64)
    newer = publish(
        backups,
        "caliente-20150409T004000Z",
        b"newer hot copy",
        dias_excluidos=[{"dia": "2015-04-04", "copia_de_dia": s1, "generacion": generation}],
    )
    # E4C3-11: the cache and the state of the disk, cut as by an unplugged disk.
    (disk / ".kbot-huellas.json").write_text('{"archivo/x": {"sel', encoding="utf-8")
    (disk / ".kbot-estado.json").write_text("", encoding="utf-8")
    second = visit(server)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Desafíos respondidos: 1." in second.stdout, second.stdout
    for name in (".kbot-huellas.json", ".kbot-estado.json"):
        assert f"({name}) no se puede leer" in second.stdout, second.stdout
        json.loads((disk / name).read_text(encoding="utf-8"))  # written whole again
    assert f"{newer.path.name} no es recuperable desde el disco: falta dia-2015-04-04" in (
        second.stdout
    )
    report = verifier.run()
    assert generation in report.verified, report
    assert "verificado_fuera" in states(connect, generation)
    assert "elegible_para_exclusion_backup" not in states(connect, generation)
    record = disk_record()
    assert record["punto"]["copia"] == hot.path.name  # not the newer one

    # E4C3-03: a hot copy whose bytes do not match its register (as a download cut short) is
    # not kept, the visit ends with an error, and neither the point nor the last sync
    # without errors moves.
    broken = publish(backups, "caliente-20150410T004000Z", b"broken hot copy")
    (broken.path / "volcado.age").write_bytes(b"cut")
    third = visit(server)
    assert third.returncode == 1 and "no coincide con su SHA-256" in third.stdout, third.stdout
    assert not (disk / "copias" / broken.path.name).exists()
    verifier.run()
    after = disk_record()
    assert after["punto"] == record["punto"]
    assert after["ultima_sin_errores"] == record["ultima_sin_errores"]
    assert after["ultima_visita"] > record["ultima_visita"]

    # E4C3-10: the disk loses its only complete hot copy; the server keeps no point.
    shutil.rmtree(disk / "copias" / hot.path.name)
    fourth = visit(server)
    assert "el disco aún no tiene ninguna copia caliente completa" in fourth.stdout, fourth.stdout
    verifier.run()
    assert disk_record()["punto"] is None

    # E4C3-07: what the verifier reads, as its unit runs it.
    marker = (disk / ".kbot-disco").read_text(encoding="utf-8").strip()
    as_verifier = as_root(
        AS_THE_VERIFIER,
        {
            "REPO": str(ROOT),
            "PYTHON": sys.executable,
            "BASE": os.path.realpath(sys.executable),
            "CODE": VERIFIER_CODE,
            "HOT": hot.path.name,
            "DISK": marker,
        },
    )
    assert as_verifier.returncode == 0, as_verifier.stdout + as_verifier.stderr
    assert "point valid as kbot_archive" in as_verifier.stdout

    # What kbot_sync cannot do, through the very same SFTP.
    probe = server["disk"].parent / "probe.txt"
    probe.write_text("x", encoding="utf-8")
    for command in (f"put {probe} copias/x", f"put {probe} archivo/x", f"put {probe} desafios/x"):
        assert sftp(server, command).returncode != 0, command
    assert sftp(server, f"put {probe} recibos/x", "rm recibos/x").returncode == 0
    assert sftp(server, f"put {probe} recibos/a", "symlink recibos/a recibos/b").returncode != 0
    assert sftp(server, "ln recibos/a recibos/c").returncode != 0  # nor a hard link
    assert sftp(server, "cd /srv/kbot/backups").returncode != 0  # the chroot hides the server
    copy = server["disk"].parent / "estado.json"
    assert sftp(server, f"get /srv/kbot/externo/privado/estado.json {copy}").returncode != 0
    assert not copy.exists()
    big = server["disk"].parent / "grande.bin"
    with big.open("wb") as handle:
        handle.truncate(300 << 20)  # more than the 256 MiB of recibos/
    assert sftp(server, f"put {big} recibos/grande").returncode != 0
    sftp(server, "-rm recibos/grande")
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
