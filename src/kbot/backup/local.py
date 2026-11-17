"""Local backups of the production database (02 §13.1, point 4; stage E4c-1).

``python -m kbot.backup run`` runs every hour, as kbot_backup:

1. it removes the attempts that a stopped run left (temporary directories);
2. it copies each sealed day that has no copy yet, once (``dia-YYYY-MM-DD``);
3. it makes the hot copy (``caliente-YYYYMMDDTHHMMSSZ``);
4. it applies the local retention.

``python -m kbot.backup globals`` runs every week, as postgres, and copies the global
objects (``globales-...``), which ``pg_read_all_data`` cannot read.

Every copy is a directory with the encrypted dump (``volcado.age``) and its manifest
(``manifiesto.json``). The dump comes from one exported snapshot, which stays open until
the counts, the fingerprints and the dump are done. It is encrypted with age for the
recipients of /etc/kbot/age-recipients.txt (the principal key and the recovery key), and
the plain dump is deleted before the copy is published. A copy is published by renaming
its directory to the final name, so until then it is an attempt that the next run
removes. The SHA-256 of the plain dump goes into the manifest and into the register,
which only kbot_backup writes; a restore authenticates the dump with it before it runs
anything (R-E4-04).

In E4c-1 nothing leaves the server. No sealed day has a verified external alternative
(R-E4-01), so the hot copy carries every day, and no copy of a sealed day is deleted
until its day is ``elegible_para_exclusion_backup``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from kbot.archive import archiver, parquet
from kbot.db.roles import DATABASE, SOCKET_DIR

DSN = f"host={SOCKET_DIR} dbname={DATABASE} user=kbot_backup"
BACKUP_DIR = Path("/srv/kbot/backups")
GLOBALS_DIR = BACKUP_DIR / "globales"
RECIPIENTS = Path("/etc/kbot/age-recipients.txt")
REGISTER = "registro.jsonl"
DUMP = "volcado.age"
MANIFEST = "manifiesto.json"
FORMAT = "kbot-copia-1"
HOT_PREFIX = "caliente-"
DAY_PREFIX = "dia-"
GLOBALS_PREFIX = "globales-"
HOT_KEEP = 3  # the last three hourly copies, plus the last daily one (02 §13.1, point 4)
GLOBALS_KEEP = 2
# The schemas the copies describe: every schema of the database except the system ones.
_USER_SCHEMAS = sql.SQL(
    "n.nspname NOT IN ('pg_catalog', 'information_schema')"
    " AND n.nspname NOT LIKE 'pg\\_toast%' AND n.nspname NOT LIKE 'pg\\_temp%'"
)


def _over_user_schemas(query: str) -> sql.Composed:
    """A catalog query whose ``{schemas}`` is the filter of the kbot schemas."""
    return sql.SQL(query).format(schemas=_USER_SCHEMAS)  # type: ignore[arg-type]


class BackupError(Exception):
    """A step of a copy failed; the copy is not published."""


@dataclass(frozen=True)
class Copy:
    """A published copy: its directory and its manifest."""

    path: Path
    manifest: dict[str, Any]


def sha256_file(path: Path) -> str:
    return parquet.sha256(path)


def recipients(path: Path) -> list[str]:
    """The public keys of the recipients file, which never holds a private key."""
    text = path.read_text(encoding="utf-8")
    if "AGE-SECRET-KEY" in text.upper():
        raise BackupError(f"{path} holds a private key: refusing to use it")
    keys = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if len(keys) < 2 or not all(key.startswith("age1") for key in keys):
        raise BackupError(f"{path} must hold the principal and the recovery public keys")
    return keys


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise BackupError(f"{name} is not installed")
    return path


def _run(command: Sequence[str]) -> None:
    done = subprocess.run(list(command), capture_output=True, text=True, check=False)  # noqa: S603
    if done.returncode != 0:
        raise BackupError(f"{Path(command[0]).name} failed: {done.stderr.strip()}")


def encrypt(plain: Path, out: Path, recipients_file: Path) -> None:
    _run(
        [
            _tool("age"),
            "--encrypt",
            "--recipients-file",
            str(recipients_file),
            "-o",
            str(out),
            str(plain),
        ]
    )


def _one(conn: psycopg.Connection, query: str) -> Any:
    row = conn.execute(query).fetchone()  # type: ignore[arg-type]  # a literal query
    if row is None:
        raise BackupError(f"no result for {query}")
    return row[0]


def catalog_fingerprint(conn: psycopg.Connection) -> str:
    """SHA-256 of the catalog of the database by name, never by object id: relations,
    columns, constraints (with whether they are validated, because the seals are
    ``CHECK (false) NOT VALID``), indexes and functions. A restore compares it once every
    row is loaded (02 §13.1, point 4, step 4)."""
    queries = (
        "SELECT n.nspname, c.relname, c.relkind::text, c.relispartition"
        " FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
        " WHERE {schemas} AND c.relkind IN ('r', 'p', 'v', 'm', 'S')"
        " ORDER BY 1, 2",
        "SELECT n.nspname, c.relname, a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod),"
        " a.attnotnull, pg_catalog.pg_get_expr(d.adbin, d.adrelid)"
        " FROM pg_catalog.pg_attribute a"
        " JOIN pg_catalog.pg_class c ON c.oid = a.attrelid"
        " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
        " LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum"
        " WHERE {schemas} AND c.relkind IN ('r', 'p') AND a.attnum > 0"
        " AND NOT a.attisdropped ORDER BY 1, 2, a.attnum",
        "SELECT n.nspname, c.relname, k.conname, pg_catalog.pg_get_constraintdef(k.oid),"
        " k.convalidated"
        " FROM pg_catalog.pg_constraint k"
        " JOIN pg_catalog.pg_class c ON c.oid = k.conrelid"
        " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
        " WHERE {schemas} ORDER BY 1, 2, 3",
        "SELECT n.nspname, c.relname, pg_catalog.pg_get_indexdef(c.oid)"
        " FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
        " WHERE {schemas} AND c.relkind IN ('i', 'I') ORDER BY 1, 2",
        "SELECT n.nspname, p.proname, pg_catalog.pg_get_function_identity_arguments(p.oid),"
        " pg_catalog.pg_get_functiondef(p.oid)"
        " FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace"
        " WHERE {schemas} AND p.prokind IN ('f', 'p') ORDER BY 1, 2, 3",
    )
    digest = hashlib.sha256()
    for query in queries:
        for row in conn.execute(_over_user_schemas(query)).fetchall():
            digest.update(json.dumps(list(row), default=str).encode("utf-8") + b"\n")
        digest.update(b"--\n")
    return digest.hexdigest()


def table_counts(conn: psycopg.Connection) -> dict[str, int]:
    """The rows of every table of the database (a partitioned table counts its partitions)."""
    tables = conn.execute(
        _over_user_schemas(
            "SELECT n.nspname, c.relname FROM pg_catalog.pg_class c"
            " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
            " WHERE {schemas} AND c.relkind IN ('r', 'p') AND NOT c.relispartition"
            " ORDER BY 1, 2"
        )
    ).fetchall()
    counts: dict[str, int] = {}
    for schema, name in tables:
        query = sql.SQL("SELECT count(*) FROM {}.{}").format(
            sql.Identifier(schema), sql.Identifier(name)
        )
        row = conn.execute(query).fetchone()
        counts[f"{schema}.{name}"] = int(row[0]) if row else 0
    return counts


def _snapshot(conn: psycopg.Connection) -> tuple[str, datetime]:
    """Exports the snapshot of the open REPEATABLE READ transaction, for ``pg_dump``."""
    return str(_one(conn, "SELECT pg_export_snapshot()")), _one(conn, "SELECT now()")


def _connect(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn)
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    conn.read_only = True
    return conn


def _dump_info(plain: Path, encrypted: Path) -> dict[str, Any]:
    return {
        "archivo": DUMP,
        "sha256_claro": sha256_file(plain),
        "bytes_claro": plain.stat().st_size,
        "sha256_cifrado": sha256_file(encrypted),
        "bytes_cifrado": encrypted.stat().st_size,
    }


def _publish(temporary: Path, final: Path, manifest: dict[str, Any]) -> Copy:
    """Writes the manifest and makes the copy durable, then renames it to its final name,
    which is what publishes it."""
    with (temporary / MANIFEST).open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write("\n")
    for path in (temporary / DUMP, temporary / MANIFEST, temporary):
        parquet.sync(path)
    temporary.rename(final)
    parquet.sync(final.parent)
    return Copy(final, manifest)


def _register(directory: Path, manifest: dict[str, Any]) -> None:
    """Appends the copy to the register of its directory, which only its writer can touch."""
    entry = {
        "copia": manifest["nombre"],
        "clase": manifest["clase"],
        "creada": manifest["creada"],
        "sha256_claro": manifest["volcado"]["sha256_claro"],
        "sha256_cifrado": manifest["volcado"]["sha256_cifrado"],
    }
    register = directory / REGISTER
    with register.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    parquet.sync(register)


@contextmanager
def _attempt(directory: Path, name: str) -> Iterator[tuple[Path, Path]]:
    """The temporary directory of a copy and its final name. If anything fails before the
    copy is published, the directory goes at once, with any plain dump in it; only a crash
    of the process can leave an attempt behind, and the next run removes it."""
    final = directory / name
    if final.exists():
        raise BackupError(f"{final} already exists")
    temporary = directory / f".{name}.tmp"
    temporary.mkdir()
    try:
        yield temporary, final
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _encrypt_and_drop(plain: Path, temporary: Path, recipients_file: Path) -> dict[str, Any]:
    encrypted = temporary / DUMP
    encrypt(plain, encrypted, recipients_file)
    info = _dump_info(plain, encrypted)
    plain.unlink()
    return info


def hot_copy(
    directory: Path,
    recipients_file: Path,
    *,
    now: datetime,
    dsn: str = DSN,
    before_dump: Callable[[], None] = lambda: None,
) -> Copy:
    """The hourly copy of the whole database, from one snapshot (02 §13.1, point 4)."""
    keys = recipients(recipients_file)
    name = f"{HOT_PREFIX}{now.astimezone(UTC):%Y%m%dT%H%M%SZ}"
    with _attempt(directory, name) as (temporary, final):
        plain = temporary / "volcado.dump"
        with _connect(dsn) as conn, conn.transaction():
            snapshot, snapshot_at = _snapshot(conn)
            sealed = [
                row[0].isoformat()
                for row in conn.execute("SELECT day FROM market.archive_seals ORDER BY day")
            ]
            counts = table_counts(conn)
            catalog = catalog_fingerprint(conn)
            before_dump()
            _run(
                [
                    _tool("pg_dump"),
                    "--format=custom",
                    "--compress=zstd:3",
                    f"--snapshot={snapshot}",
                    f"--file={plain}",
                    f"--dbname={dsn}",
                ]
            )
        manifest = {
            "formato": FORMAT,
            "clase": "caliente",
            "nombre": name,
            "creada": now.astimezone(UTC).isoformat(),
            "instantanea": snapshot_at.astimezone(UTC).isoformat(),
            "dias_sellados": sealed,
            # E4c-1: no sealed day has a verified external alternative yet (R-E4-01).
            "dias_excluidos": [],
            "recuentos": counts,
            "catalogo": catalog,
            "destinatarios": keys,
            "volcado": _encrypt_and_drop(plain, temporary, recipients_file),
        }
        copy = _publish(temporary, final, manifest)
    _register(directory, manifest)
    return copy


def sealed_day_copy(
    directory: Path,
    recipients_file: Path,
    day: date,
    *,
    now: datetime,
    dsn: str = DSN,
) -> Copy:
    """The copy of one sealed day, made once: the data of its twelve partitions, with the
    count and the canonical fingerprint of each, the same fingerprint as its archive file
    (02 §13.1, point 4)."""
    keys = recipients(recipients_file)
    name = f"{DAY_PREFIX}{day.isoformat()}"
    with _attempt(directory, name) as (temporary, final):
        plain = temporary / "volcado.dump"
        with _connect(dsn) as conn, conn.transaction():
            snapshot, snapshot_at = _snapshot(conn)
            sealed = conn.execute("SELECT 1 FROM market.archive_seals WHERE day = %s", (day,))
            if sealed.fetchone() is None:
                raise BackupError(f"{day} is not sealed")
            partitions = []
            for table_name in archiver.TABLES:
                table = archiver.describe(conn, table_name)
                fingerprint = archiver.day_fingerprint(conn, table, day)
                partitions.append(
                    {
                        "tabla": table_name,
                        "particion": f"market.{table_name}_p{day:%Y%m%d}",
                        "filas": fingerprint.rows,
                        "huella": fingerprint.hexdigest(),
                    }
                )
            catalog = catalog_fingerprint(conn)
            _run(
                [
                    _tool("pg_dump"),
                    "--format=custom",
                    "--compress=zstd:3",
                    "--data-only",
                    f"--snapshot={snapshot}",
                    *(f"--table={p['particion']}" for p in partitions),
                    f"--file={plain}",
                    f"--dbname={dsn}",
                ]
            )
        manifest = {
            "formato": FORMAT,
            "clase": "dia_sellado",
            "nombre": name,
            "dia": day.isoformat(),
            "creada": now.astimezone(UTC).isoformat(),
            "instantanea": snapshot_at.astimezone(UTC).isoformat(),
            "particiones": partitions,
            "catalogo": catalog,
            "destinatarios": keys,
            "volcado": _encrypt_and_drop(plain, temporary, recipients_file),
        }
        copy = _publish(temporary, final, manifest)
    _register(directory, manifest)
    return copy


def globals_copy(
    directory: Path, recipients_file: Path, *, now: datetime, host: str = SOCKET_DIR
) -> Copy:
    """The global objects (roles and their memberships), as postgres, once a week."""
    keys = recipients(recipients_file)
    name = f"{GLOBALS_PREFIX}{now.astimezone(UTC):%Y%m%dT%H%M%SZ}"
    with _attempt(directory, name) as (temporary, final):
        plain = temporary / "volcado.sql"
        _run([_tool("pg_dumpall"), "--globals-only", f"--host={host}", f"--file={plain}"])
        manifest = {
            "formato": FORMAT,
            "clase": "globales",
            "nombre": name,
            "creada": now.astimezone(UTC).isoformat(),
            "destinatarios": keys,
            "volcado": _encrypt_and_drop(plain, temporary, recipients_file),
        }
        copy = _publish(temporary, final, manifest)
    _register(directory, manifest)
    return copy


def remove_attempts(directory: Path) -> list[Path]:
    """Removes the temporary directories of copies that never got published."""
    removed = []
    for path in sorted(directory.glob(".*.tmp")):
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(path)
    return removed


def _named(directory: Path, prefix: str) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.is_dir() and p.name.startswith(prefix))


def hot_to_delete(names: Sequence[str]) -> list[str]:
    """The hot copies the local retention drops: all but the last three and the last daily
    one, which is the first copy of the most recent UTC day (names sort by time)."""
    ordered = sorted(names)
    keep = set(ordered[-HOT_KEEP:])
    if ordered:
        last_day = ordered[-1][len(HOT_PREFIX) : len(HOT_PREFIX) + 8]
        keep.add(next(n for n in ordered if n[len(HOT_PREFIX) : len(HOT_PREFIX) + 8] == last_day))
    return [n for n in ordered if n not in keep]


def eligible_days(dsn: str = DSN) -> set[date]:
    """Days whose generation is ``elegible_para_exclusion_backup`` (02 §3.9): only then
    may their local copy go."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT DISTINCT g.day FROM market.archive_generations g"
            " JOIN market.archive_generation_states s ON s.generation_id = g.generation_id"
            " WHERE s.state = 'elegible_para_exclusion_backup'"
        ).fetchall()
    return {row[0] for row in rows}


def retention(directory: Path, eligible: set[date]) -> list[Path]:
    """Applies the local retention and returns what it deleted."""
    deleted = []
    hot = {p.name: p for p in _named(directory, HOT_PREFIX)}
    for name in hot_to_delete(list(hot)):
        shutil.rmtree(hot[name])
        deleted.append(hot[name])
    for path in _named(directory, DAY_PREFIX):
        if date.fromisoformat(path.name[len(DAY_PREFIX) :]) in eligible:
            shutil.rmtree(path)
            deleted.append(path)
    return deleted


def globals_retention(directory: Path) -> list[Path]:
    copies = _named(directory, GLOBALS_PREFIX)
    for path in copies[:-GLOBALS_KEEP]:
        shutil.rmtree(path)
    return copies[:-GLOBALS_KEEP]


def pending_sealed_days(directory: Path, eligible: set[date], dsn: str = DSN) -> list[date]:
    """Sealed days without a published copy that still need one (not yet eligible)."""
    with psycopg.connect(dsn) as conn:
        sealed = [
            row[0]
            for row in conn.execute(
                "SELECT s.day FROM market.archive_seals s WHERE NOT EXISTS"
                " (SELECT 1 FROM market.archive_retirements r WHERE r.day = s.day) ORDER BY s.day"
            )
        ]
    copied = {p.name[len(DAY_PREFIX) :] for p in _named(directory, DAY_PREFIX)}
    return [d for d in sealed if d.isoformat() not in copied and d not in eligible]
