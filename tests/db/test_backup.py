"""The local backups against PostgreSQL (02 §13.1, point 4; stage E4c-1).

As kbot_backup, through the peer map of the CI, and with two throwaway test identities,
never the owner's keys:

* the hot copy reads one snapshot: a row inserted after it is neither counted nor dumped;
  only the encrypted dump stays, it decrypts with either identity to the plain dump whose
  SHA-256 the manifest and the register keep, and pg_restore reads it;
* the copy of a sealed day carries its twelve partitions with the same counts and
  fingerprints as the archive generation of the same day;
* a copy that fails leaves nothing on the disk, not even the plain dump;
* the global objects, as postgres, decrypt to the roles of the cluster.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from kbot.archive import archiver
from kbot.backup import local
from kbot.db.roles import DATABASE

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]
Partitions = Callable[[date, date], None]
NOW = datetime(2026, 11, 17, 10, 40, tzinfo=UTC)
SEALED_DAY = date(2015, 3, 14)
SYMBOL = "BACKUP-USDT"


def tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        pytest.fail(f"{name} is required by the backup tests (installed in the CI)")
    return path


@pytest.fixture
def identities(tmp_path: Path) -> tuple[list[Path], Path]:
    """Two throwaway identities and the recipients file of their public keys."""
    keys = tmp_path / "keys"
    keys.mkdir()
    identities, public = [], []
    for name in ("principal", "recovery"):
        identity = keys / f"{name}.txt"
        subprocess.run([tool("age-keygen"), "-o", str(identity)], check=True, capture_output=True)
        lines = identity.read_text(encoding="utf-8").splitlines()
        public.append(next(x for x in lines if x.startswith("# public key: ")).split(": ")[1])
        identities.append(identity)
    recipients = keys / "recipients.txt"
    recipients.write_text("\n".join(public) + "\n", encoding="utf-8")
    return identities, recipients


@pytest.fixture
def dsn(pghost: str) -> str:
    return f"host={pghost} dbname={DATABASE} user=kbot_backup"


def decrypt(identity: Path, encrypted: Path, out: Path) -> Path:
    subprocess.run(
        [tool("age"), "--decrypt", "-i", str(identity), "-o", str(out), str(encrypted)],
        check=True,
        capture_output=True,
    )
    return out


def restore_listing(plain: Path) -> str:
    done = subprocess.run(
        [tool("pg_restore"), "--list", str(plain)], check=True, capture_output=True, text=True
    )
    return done.stdout


def test_the_hot_copy_is_one_snapshot_and_only_its_encrypted_dump_stays(
    connect: Connect, dsn: str, identities: tuple[list[Path], Path], tmp_path: Path
) -> None:
    keys, recipients = identities
    directory = tmp_path / "copias"
    directory.mkdir()
    symbol = f"BACKUP-{uuid.uuid4().hex[:8]}"
    with connect("kbot_backup") as backup:
        row = backup.execute("SELECT count(*) FROM market.data_gaps").fetchone()
    before = row[0] if row else 0

    def insert_after_the_snapshot() -> None:
        with connect("kbot_ingest") as ingest:
            ingest.execute(
                "INSERT INTO market.data_gaps (market, channel, symbol, start_at, end_at, cause)"
                " VALUES ('SPOT', 'trade', %s, %s, %s, 'desconexion')",
                (symbol, datetime(2016, 1, 1, tzinfo=UTC), datetime(2016, 1, 1, 0, 1, tzinfo=UTC)),
            )

    try:
        copy = local.hot_copy(
            directory, recipients, now=NOW, dsn=dsn, before_dump=insert_after_the_snapshot
        )
        assert copy.path.name == "caliente-20261117T104000Z"
        assert sorted(p.name for p in copy.path.iterdir()) == [local.DUMP, local.MANIFEST]
        assert not list(directory.rglob("volcado.dump")), "a plain dump stayed on the disk"
        manifest = json.loads((copy.path / local.MANIFEST).read_text(encoding="utf-8"))
        assert manifest == copy.manifest
        assert manifest["clase"] == "caliente" and manifest["dias_excluidos"] == []
        assert manifest["recuentos"]["market.data_gaps"] == before  # not the late row
        dump = manifest["volcado"]
        assert local.sha256_file(copy.path / local.DUMP) == dump["sha256_cifrado"]
        for n, identity in enumerate(keys):  # the principal key and the recovery key
            plain = decrypt(identity, copy.path / local.DUMP, tmp_path / f"plain{n}.dump")
            assert local.sha256_file(plain) == dump["sha256_claro"]
        assert "TABLE DATA market data_gaps" in restore_listing(plain)
        rows = subprocess.run(
            [tool("pg_restore"), "--data-only", "--table=data_gaps", "--file=-", str(plain)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert symbol not in rows, "a row written after the snapshot is in the dump"
        register = (directory / local.REGISTER).read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["copia"] for line in register] == [copy.path.name]
        assert json.loads(register[0])["sha256_claro"] == dump["sha256_claro"]
    finally:
        with connect("postgres") as admin:
            admin.execute("DELETE FROM market.data_gaps WHERE symbol = %s", (symbol,))


def sealed_day(connect: Connect, partitions: Partitions, day: date) -> None:
    """A closed day with rows in four of its tables, sealed (as in test_archive.py)."""
    partitions(day, day)
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    with connect("kbot_ingest") as ingest:
        for i in range(3):
            ingest.execute(
                "INSERT INTO market.depth_slot_status (market, symbol, depth, slot_start, state,"
                " sequence, snapshot_age_ms) VALUES ('SPOT', %s, 5, %s, 'valida', %s, 0)",
                (SYMBOL, start + timedelta(seconds=10 * i), i + 1),
            )
        ingest.execute(
            "INSERT INTO market.depth_metrics (market, symbol, depth, slot_start, best_bid,"
            " best_ask, mid, spread, bid_levels, ask_levels, bid_quantity, ask_quantity,"
            " bid_notional, ask_notional, bid_notional_10bps, ask_notional_10bps,"
            " bid_notional_50bps, ask_notional_50bps, bid_deepest_price, ask_deepest_price)"
            " VALUES ('SPOT', %s, 5, %s, 100, 101, 100.5, 1, 1, 1, 2, 3, 200, 303,"
            " 0, 0, 0, 0, 100, 101)",
            (SYMBOL, start),
        )
        ingest.execute(
            "INSERT INTO market.depth_snapshots (market, symbol, depth, minute_start, slot_start,"
            " sequence, published_at, received_at, bids, asks)"
            " VALUES ('SPOT', %s, 5, %s, %s, 1, %s, %s, %s, %s)",
            (
                SYMBOL,
                start,
                start,
                start + timedelta(seconds=1),
                start + timedelta(seconds=2),
                '[["100", "2"]]',
                '[["101", "3"]]',
            ),
        )
        for minute in range(2):
            ingest.execute(
                "INSERT INTO market.data_quality_minute (market, symbol, channel, minute_start,"
                " flow_state, messages, anomalies, sequence_breaks, reconnections)"
                " VALUES ('SPOT', %s, 'obu5', %s, 'S1', 6, 0, 0, 0)",
                (SYMBOL, start + timedelta(minutes=minute)),
            )
    with connect("kbot_archive") as archive:
        assert archive.execute("SELECT market.seal_day(%s)", (day,)).fetchone() == (12,)


def test_a_sealed_day_copy_holds_its_partitions_with_the_fingerprints_of_its_archive(
    connect: Connect,
    partitions: Partitions,
    dsn: str,
    identities: tuple[list[Path], Path],
    tmp_path: Path,
) -> None:
    keys, recipients = identities
    directory = tmp_path / "copias"
    directory.mkdir()
    sealed_day(connect, partitions, SEALED_DAY)
    assert SEALED_DAY in local.pending_sealed_days(directory, set(), dsn)
    copy = local.sealed_day_copy(directory, recipients, SEALED_DAY, now=NOW, dsn=dsn)
    assert copy.path.name == "dia-2015-03-14"
    assert SEALED_DAY not in local.pending_sealed_days(directory, set(), dsn)
    assert SEALED_DAY not in local.pending_sealed_days(directory, {SEALED_DAY}, dsn)
    manifest = copy.manifest
    assert [p["tabla"] for p in manifest["particiones"]] == list(archiver.TABLES)
    root = tmp_path / "archivo"
    root.mkdir()
    archiver.archive_pending(lambda: connect("kbot_archive"), root)
    with connect("kbot_archive") as archive:
        files = archive.execute(
            "SELECT f.table_name, f.row_count, f.fingerprint FROM market.archive_files f"
            " WHERE f.day = %s",
            (SEALED_DAY,),
        ).fetchall()
    assert len(files) == len(archiver.TABLES)
    archived = {name: (rows, fingerprint) for name, rows, fingerprint in files}
    copied = {p["tabla"]: (p["filas"], p["huella"]) for p in manifest["particiones"]}
    assert copied == archived
    assert copied["depth_slot_status"][0] == 3 and copied["candles"][0] == 0
    plain = decrypt(keys[0], copy.path / local.DUMP, tmp_path / "day.dump")
    assert local.sha256_file(plain) == manifest["volcado"]["sha256_claro"]
    listing = restore_listing(plain)
    for name in archiver.TABLES:
        assert f"TABLE DATA market {name}_p20150314" in listing, name
    definitions = [
        x for x in listing.splitlines() if " TABLE market " in x and "TABLE DATA" not in x
    ]
    assert definitions == [], "a copy of a sealed day carries data only"


def test_a_copy_that_fails_leaves_nothing_on_the_disk(dsn: str, tmp_path: Path) -> None:
    """age rejects the recipients after pg_dump wrote the plain dump: the attempt goes."""
    broken = tmp_path / "recipients.txt"
    broken.write_text("age1notakey\nage1notakeyeither\n", encoding="utf-8")
    directory = tmp_path / "copias"
    directory.mkdir()
    with pytest.raises(local.BackupError, match="age failed"):
        local.hot_copy(directory, broken, now=NOW, dsn=dsn)
    assert list(directory.iterdir()) == []


def test_the_global_objects_decrypt_to_the_roles_of_the_cluster(
    pghost: str,
    identities: tuple[list[Path], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys, recipients = identities
    monkeypatch.setenv("PGUSER", "postgres")  # on the server the service runs as postgres
    directory = tmp_path / "globales"
    directory.mkdir()
    copy = local.globals_copy(directory, recipients, now=NOW, host=pghost)
    plain = decrypt(keys[1], copy.path / local.DUMP, tmp_path / "globals.sql")
    assert local.sha256_file(plain) == copy.manifest["volcado"]["sha256_claro"]
    text = plain.read_text(encoding="utf-8")
    assert "CREATE ROLE kbot_backup" in text and "CREATE ROLE kbot_ingest" in text
    assert not list(directory.rglob("volcado.sql"))
