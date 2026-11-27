"""Local backups of the production database (02 §10.1 and §13.1, point 4; stage E4c-1).

``python -m kbot.backup run`` runs every hour, as kbot_backup:

1. it removes the attempts that a stopped run left (temporary directories) and every
   published copy that the register does not vouch for (``reconcile``);
2. it copies each sealed day that has no copy yet, once (``dia-YYYY-MM-DD``);
3. it makes the hot copy (``caliente-YYYYMMDDTHHMMSSZ``);
4. it applies the local retention.

``python -m kbot.backup globals`` runs every week, as postgres, and copies the global
objects (``globales-...``), which ``pg_read_all_data`` cannot read.

Every copy is a directory with the encrypted dump (``volcado.age``) and its manifest
(``manifiesto.json``). The dump comes from one exported snapshot, which stays open until
the counts, the fingerprints and the dump are done. It is encrypted with age for the
recipients of /etc/kbot/age-recipients.txt (the principal key and the recovery key), and
the plain dump is deleted before the copy is published. The copy is then registered (the
SHA-256 of its plain and encrypted dumps go to ``registro.jsonl``, which only its writer
can touch) and only after that published, by renaming its directory to the final name.
So a published copy always has its register entry, which a restore needs to authenticate
the dump before it runs anything (R-E4-04; review of E4c-1, E4c1-04). If a step fails, the
attempt goes at once with whatever it held; only a crash can leave one, and the next run
removes it, with any published copy that the register does not vouch for.

The manifest follows 02 §10.1: every table and every partition with its mode (in E4c-1
always ``incluido``), rows and canonical fingerprint (E4c1-02); the fingerprint of the
catalog, partitioning included (E4c1-03); and the versions of PostgreSQL, pg_dump, the
extensions, Alembic, the system and the architecture. A sealed partition cannot change
(``CHECK (false) NOT VALID``, and the daily check of E3 compares it with its archive), so
its fingerprint is the one its sealed-day copy took once; the hot copy counts it again in
its own snapshot and stops if the counts differ. Every other fingerprint is taken in the
snapshot of the dump.

In E4c-1 nothing left the server. From E4c-2, kbot-upload (another user, with network
and no access to the database) uploads the published copies to Object Storage, reads them
again and writes its state in /srv/kbot/subidas/estado.json. The hot copy leaves out the
data of a sealed day only when that state says the copy of the day is verified in the
bucket and was in the last listing, and that listing finished well, is less than an hour
old and authorised no earlier hot copy (R-E4-01; 02 §13.1, point 4, E4c-2, point 4;
E4C2-02). The manifest fixes the identity of each such copy, and without a usable state
nothing is left out: the copy grows, but is never incomplete. No copy of a sealed day is
deleted here until its day is ``elegible_para_exclusion_backup``.

``python -m kbot.backup run --previa`` makes the copy taken before an update: like the hot
copy, but self-contained, so it never leaves anything out (``previa-...``).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

import kbot
from kbot.archive import archiver, parquet
from kbot.archive.canonical import Fingerprint, json_text
from kbot.db.roles import DATABASE, SOCKET_DIR
from kbot.upload import durable
from kbot.upload import state as upload_state

DSN = f"host={SOCKET_DIR} dbname={DATABASE} user=kbot_backup"
BACKUP_DIR = Path("/srv/kbot/backups")
GLOBALS_DIR = BACKUP_DIR / "globales"
RECIPIENTS = Path("/etc/kbot/age-recipients.txt")
UPLOAD_STATE = upload_state.UPLOAD_DIR / upload_state.STATE
# The last listing of the uploads a hot copy relied on (E4C2-04), written by kbot_backup.
CONSUMED_LISTING = "listado_consumido.json"
REGISTER = "registro.jsonl"
DUMP = "volcado.age"
MANIFEST = "manifiesto.json"
FORMAT = "kbot-copia-1"
HOT_PREFIX = "caliente-"
DAY_PREFIX = "dia-"
GLOBALS_PREFIX = "globales-"
PREVIA_PREFIX = "previa-"
COPY_PREFIXES = (HOT_PREFIX, DAY_PREFIX, GLOBALS_PREFIX, PREVIA_PREFIX)
HOT_KEEP = 3  # the last three hourly copies, plus the last daily one (02 §13.1, point 4)
GLOBALS_KEEP = 2
PREVIA_KEEP = 2
BATCH = 20_000
# The schemas the copies describe: every schema of the database except the system ones.
_USER_SCHEMAS = sql.SQL(
    "n.nspname NOT IN ('pg_catalog', 'information_schema')"
    " AND n.nspname NOT LIKE 'pg\\_toast%' AND n.nspname NOT LIKE 'pg\\_temp%'"
)


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


def _run(command: Sequence[str]) -> str:
    done = subprocess.run(list(command), capture_output=True, text=True, check=False)  # noqa: S603
    if done.returncode != 0:
        raise BackupError(f"{Path(command[0]).name} failed: {done.stderr.strip()}")
    return done.stdout.strip()


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


def _over_user_schemas(query: str) -> sql.Composed:
    """A catalog query whose ``{schemas}`` is the filter of the kbot schemas."""
    return sql.SQL(query).format(schemas=_USER_SCHEMAS)  # type: ignore[arg-type]


_CATALOG_QUERIES = (
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
    # The partitioning (E4c1-03): the key of each partitioned table, and the parent and
    # the bounds of each partition, by name.
    "SELECT n.nspname, c.relname, pg_catalog.pg_get_partkeydef(c.oid)"
    " FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
    " WHERE {schemas} AND c.relkind = 'p' ORDER BY 1, 2",
    "SELECT n.nspname, c.relname, pn.nspname, p.relname,"
    " pg_catalog.pg_get_expr(c.relpartbound, c.oid)"
    " FROM pg_catalog.pg_inherits i"
    " JOIN pg_catalog.pg_class c ON c.oid = i.inhrelid"
    " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
    " JOIN pg_catalog.pg_class p ON p.oid = i.inhparent"
    " JOIN pg_catalog.pg_namespace pn ON pn.oid = p.relnamespace"
    " WHERE {schemas} ORDER BY 1, 2",
)


def catalog_fingerprint(conn: psycopg.Connection) -> str:
    """SHA-256 of the catalog of the database by name, never by object id: relations,
    columns, constraints (with whether they are validated, because the seals are
    ``CHECK (false) NOT VALID``), indexes, functions and the partitioning. A restore
    compares it once every row is loaded (02 §13.1, point 4, step 4)."""
    digest = hashlib.sha256()
    for query in _CATALOG_QUERIES:
        for row in conn.execute(_over_user_schemas(query)).fetchall():
            digest.update(json.dumps(list(row), default=str).encode("utf-8") + b"\n")
        digest.update(b"--\n")
    return digest.hexdigest()


def versions(conn: psycopg.Connection) -> dict[str, Any]:
    """The versions that 02 §10.1 asks every manifest to fix."""
    return {
        "postgresql": _one(conn, "SHOW server_version"),
        "pg_dump": _run([_tool("pg_dump"), "--version"]),
        "age": _run([_tool("age"), "--version"]),
        "extensiones": dict(
            conn.execute("SELECT extname, extversion FROM pg_catalog.pg_extension ORDER BY 1")
        ),
        "alembic": [row[0] for row in conn.execute("SELECT version_num FROM alembic_version")],
        "sistema": platform.platform(),
        "arquitectura": platform.machine(),
        "python": platform.python_version(),
        "kbot": kbot.__version__,
    }


def _qualified(conn: psycopg.Connection, schema: str, name: str) -> str:
    return sql.Identifier(schema, name).as_string(conn)


def _relation_fingerprint(conn: psycopg.Connection, schema: str, name: str) -> Fingerprint:
    """The canonical fingerprint of every row of a table, in primary key order (in the order
    of all its columns when it has none), with jsonb as its JCS text, as in E3."""
    relation = _qualified(conn, schema, name)
    columns = conn.execute(
        "SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod)"
        " FROM pg_catalog.pg_attribute a WHERE a.attrelid = %s::regclass"
        " AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
        (relation,),
    ).fetchall()
    key = [
        row[0]
        for row in conn.execute(
            "SELECT a.attname FROM pg_catalog.pg_index i"
            " JOIN pg_catalog.pg_attribute a"
            " ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
            " WHERE i.indrelid = %s::regclass AND i.indisprimary"
            " ORDER BY array_position(i.indkey::int2[], a.attnum)",
            (relation,),
        )
    ]
    order = key or [column for column, _ in columns]
    json_columns = {i for i, (_, sql_type) in enumerate(columns) if sql_type == "jsonb"}
    selected = [
        sql.SQL("{}::text").format(sql.Identifier(column))
        if i in json_columns
        else sql.Identifier(column)
        for i, (column, _) in enumerate(columns)
    ]
    query = sql.SQL("SELECT {columns} FROM {relation} ORDER BY {order}").format(
        columns=sql.SQL(", ").join(selected),
        relation=sql.Identifier(schema, name),
        order=sql.SQL(", ").join(sql.Identifier(column) for column in order),
    )
    fingerprint = Fingerprint([column for column, _ in columns])
    with conn.cursor(name="kbot_backup_fingerprint") as cursor:
        cursor.execute(query)
        while batch := cursor.fetchmany(BATCH):
            for row in batch:
                fingerprint.add(
                    tuple(
                        json_text(json.loads(v)) if i in json_columns and v is not None else v
                        for i, v in enumerate(row)
                    )
                    if json_columns
                    else row
                )
    return fingerprint


def _count(conn: psycopg.Connection, schema: str, name: str) -> int:
    query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, name))
    row = conn.execute(query).fetchone()
    return int(row[0]) if row else 0


def _sealed_fingerprints(directory: Path) -> dict[str, tuple[int, str, str]]:
    """Rows and fingerprint of every partition of the registered sealed-day copies, by the
    qualified name of the partition, with the copy they come from."""
    found: dict[str, tuple[int, str, str]] = {}
    for copy in published(directory, DAY_PREFIX):
        for partition in copy.manifest.get("particiones", []):
            found[partition["particion"]] = (
                int(partition["filas"]),
                str(partition["huella"]),
                copy.path.name,
            )
    return found


def table_fingerprints(
    conn: psycopg.Connection,
    sealed: set[date],
    from_copies: dict[str, tuple[int, str, str]],
    excluded: set[date] | None = None,
) -> dict[str, dict[str, Any]]:
    """Mode, rows and fingerprint of every table and every partition (02 §10.1, E4c1-02),
    in the open snapshot. A sealed partition with a registered copy reuses that copy's
    fingerprint, after counting it here: a different count stops the copy. The partitions
    of an excluded day (E4c-2) are ``excluido``: their data stay out of the dump, and
    their rows and fingerprint are those of the copy of the day, which must exist."""
    excluded = excluded or set()
    relations = conn.execute(
        _over_user_schemas(
            "SELECT n.nspname, c.relname, p.relname"
            " FROM pg_catalog.pg_class c"
            " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
            " LEFT JOIN pg_catalog.pg_inherits i ON i.inhrelid = c.oid"
            " LEFT JOIN pg_catalog.pg_class p ON p.oid = i.inhparent"
            " WHERE {schemas} AND c.relkind = 'r' ORDER BY 1, 2"
        )
    ).fetchall()
    tables: dict[str, dict[str, Any]] = {}
    described: dict[str, archiver.Table] = {}
    for schema, name, parent in relations:
        qualified = f"{schema}.{name}"
        day = None
        if schema == "market" and parent in archiver.TABLES and name.startswith(f"{parent}_p"):
            try:
                day = datetime.strptime(name[len(parent) + 2 :], "%Y%m%d").date()
            except ValueError:
                day = None  # not a daily partition: fingerprinted as any other table
        if day is not None and day in excluded and qualified not in from_copies:
            raise BackupError(f"{qualified} cannot be left out: no copy of its day")
        if day is not None and day in sealed and qualified in from_copies:
            rows = _count(conn, schema, name)
            copied_rows, fingerprint, origin = from_copies[qualified]
            if rows != copied_rows:
                raise BackupError(
                    f"{qualified} is sealed but has {rows} rows, its copy {copied_rows}"
                )
            tables[qualified] = {
                "modo": "excluido" if day in excluded else "incluido",
                "filas": rows,
                "huella": fingerprint,
                "origen": origin,
            }
            continue
        if day is not None:
            if parent not in described:
                described[parent] = archiver.describe(conn, parent)
            fingerprint = archiver.day_fingerprint(conn, described[parent], day)
        else:
            fingerprint = _relation_fingerprint(conn, schema, name)
        tables[qualified] = {
            "modo": "incluido",
            "filas": fingerprint.rows,
            "huella": fingerprint.hexdigest(),
            "origen": "instantanea",
        }
    return tables


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


# The register: one JSON line per copy, written before the copy is published (E4c1-04).
# It binds the name of the copy, its plain and encrypted dumps and its whole manifest, so a
# manifest changed afterwards, even in one fingerprint, is no longer vouched for (residual of
# E4c1-02): it is never reused, and the copy is removed and made again.
_ENTRY_KEYS = ("copia", "sha256_claro", "sha256_cifrado", "sha256_manifiesto")


def _entry(manifest: dict[str, Any], manifest_sha256: str) -> dict[str, Any]:
    return {
        "copia": manifest["nombre"],
        "clase": manifest["clase"],
        "creada": manifest["creada"],
        "sha256_claro": manifest["volcado"]["sha256_claro"],
        "sha256_cifrado": manifest["volcado"]["sha256_cifrado"],
        "sha256_manifiesto": manifest_sha256,
    }


def _register(directory: Path, manifest: dict[str, Any], manifest_sha256: str) -> None:
    """Appends the entry of a copy, durably, before the copy is published. A line that an
    earlier failure cut is left as it is and never read; the new entry starts a new line."""
    register = directory / REGISTER
    separator = ""
    if register.exists() and register.stat().st_size > 0:
        with register.open("rb") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                separator = "\n"
    with register.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            separator + json.dumps(_entry(manifest, manifest_sha256), sort_keys=True) + "\n"
        )
    parquet.sync(register)


def register_entries(directory: Path) -> list[dict[str, Any]]:
    """The valid entries of the register; a line that does not parse is skipped."""
    register = directory / REGISTER
    if not register.exists():
        return []
    entries = []
    for line in register.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _registered(
    entries: Sequence[dict[str, Any]], manifest: dict[str, Any], manifest_sha256: str
) -> bool:
    try:
        wanted = _entry(manifest, manifest_sha256)
    except (KeyError, TypeError):
        return False
    return any(all(e.get(k) == wanted[k] for k in _ENTRY_KEYS) for e in entries)


def published(directory: Path, prefix: str) -> list[Copy]:
    """The published copies of a class whose register entry vouches for them: the same
    name, the same plain and encrypted dumps and the same bytes of the whole manifest."""
    entries = register_entries(directory)
    copies = []
    for path in sorted(p for p in directory.iterdir() if p.is_dir() and p.name.startswith(prefix)):
        try:
            manifest_sha256 = sha256_file(path / MANIFEST)
            manifest = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(manifest, dict)
            and manifest.get("nombre") == path.name
            and (path / DUMP).is_file()
            and _registered(entries, manifest, manifest_sha256)
        ):
            copies.append(Copy(path, manifest))
    return copies


def reconcile(directory: Path) -> list[Path]:
    """Removes every published copy that the register does not vouch for, or whose manifest
    cannot be read (E4c1-04). Such a copy cannot be authenticated before a restore, so it
    counts as never made: a sealed day goes back to pending."""
    valid = {copy.path for prefix in COPY_PREFIXES for copy in published(directory, prefix)}
    removed = []
    for path in sorted(directory.iterdir()):
        if path.is_dir() and path.name.startswith(COPY_PREFIXES) and path not in valid:
            shutil.rmtree(path)
            removed.append(path)
    return removed


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


def _finish(directory: Path, temporary: Path, final: Path, manifest: dict[str, Any]) -> Copy:
    """Writes the manifest, registers the copy with the SHA-256 of its dumps and of the
    manifest itself, and only then publishes it, by renaming its directory (E4c1-04). A
    failure before the rename leaves at most an entry without a copy, which authenticates
    nothing that exists."""
    with (temporary / MANIFEST).open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write("\n")
    for path in (temporary / DUMP, temporary / MANIFEST, temporary):
        parquet.sync(path)
    _register(directory, manifest, sha256_file(temporary / MANIFEST))
    temporary.rename(final)
    parquet.sync(final.parent)
    return Copy(final, manifest)


def _encrypt_and_drop(plain: Path, temporary: Path, recipients_file: Path) -> dict[str, Any]:
    encrypted = temporary / DUMP
    encrypt(plain, encrypted, recipients_file)
    info = _dump_info(plain, encrypted)
    plain.unlink()
    return info


def archive_knowledge(conn: psycopg.Connection) -> dict[str, list[str]]:
    """What kbot-upload, which has no access to the database, needs to decide whether a
    sealed-day copy may leave the bucket (residual of R-E4-01): the days that are
    ``elegible_para_exclusion_backup`` and the generations ``verificado_fuera``, read in the
    snapshot of the copy."""
    eligible = conn.execute(
        "SELECT DISTINCT g.day FROM market.archive_generations g"
        " JOIN market.archive_generation_states s ON s.generation_id = g.generation_id"
        " WHERE s.state = 'elegible_para_exclusion_backup' ORDER BY 1"
    ).fetchall()
    verified = conn.execute(
        "SELECT DISTINCT generation_id FROM market.archive_generation_states"
        " WHERE state = 'verificado_fuera' ORDER BY 1"
    ).fetchall()
    return {
        "dias_elegibles": [row[0].isoformat() for row in eligible],
        "generaciones_verificadas_fuera": [str(row[0]) for row in verified],
    }


def _newest_generations(conn: psycopg.Connection, days: Sequence[date]) -> dict[date, str]:
    """The newest archive generation of each day, which the manifest fixes with its omitted
    days (02 §13.1, point 4: a restore never looks for "the latest")."""
    if not days:
        return {}
    rows = conn.execute(
        "SELECT DISTINCT ON (day) day, generation_id FROM market.archive_generations"
        " WHERE day = ANY(%s) ORDER BY day, recorded_at DESC, generation_id",
        (list(days),),
    ).fetchall()
    return {row[0]: str(row[1]) for row in rows}


def omissions(
    directory: Path, state: dict[str, Any] | None, now: datetime
) -> tuple[str | None, dict[date, dict[str, Any]]]:
    """The listing a hot copy relies on, and the sealed days it leaves out with the identity
    of the copy of each in the bucket (R-E4-01; 02 §13.1, point 4, E4c-2, point 4).

    A day is left out only when its copy is published here and the state of the uploads
    says that copy is verified in the bucket, with the same partitions, and was seen by a
    listing that is the last attempt, finished well and is less than an hour old (E4C2-02).
    Each listing authorises one hot copy only (E4C2-04): ``claim_omissions`` records it
    durably as consumed before the copy is made, and a consumed listing authorises
    nothing more, whatever the copies in between did. Anything missing or unreadable
    leaves nothing out."""
    listing = upload_state.usable_listing(state, now)
    if state is None or listing is None:
        return None, {}
    readable, consumed = consumed_listing(directory)
    if not readable or consumed == listing["id"]:
        return None, {}
    omitted: dict[date, dict[str, Any]] = {}
    for copy in published(directory, DAY_PREFIX):
        entry = upload_state.verified_in(state, listing["id"], copy.path.name)
        if entry is None:
            continue
        try:
            same = entry["particiones"] == upload_state.partitions_digest(
                copy.manifest["particiones"]
            )
            dependency = {
                "copia": copy.path.name,
                **{k: str(entry[k]) for k in upload_state.IDENTITY},
            }
            day = date.fromisoformat(copy.manifest["dia"])
        except (KeyError, TypeError, ValueError):
            continue
        if same:
            omitted[day] = dependency
    return str(listing["id"]), omitted


def consumed_listing(directory: Path) -> tuple[bool, str | None]:
    """The last listing a hot copy relied on: (True, None) when none ever did, and
    (False, None) when the record cannot be read, which authorises nothing."""
    path = directory / CONSUMED_LISTING
    if not path.exists():
        return True, None
    try:
        value = durable.read_json(path)
        return True, str(value["listado"])
    except (OSError, ValueError, KeyError, TypeError):
        return False, None


def claim_omissions(
    directory: Path, state: dict[str, Any] | None, now: datetime
) -> tuple[str | None, dict[date, dict[str, Any]]]:
    """``omissions``, with the listing recorded durably as consumed before the copy is
    made (E4C2-04): a copy that then fails has used it anyway, a restart or the local
    retention cannot bring it back, and the next copy relies on no older listing."""
    listing, omitted = omissions(directory, state, now)
    if listing is not None:
        durable.write_json(directory / CONSUMED_LISTING, {"listado": listing})
    return listing, omitted


def _database_copy(
    directory: Path,
    recipients_file: Path,
    prefix: str,
    kind: str,
    *,
    now: datetime,
    dsn: str,
    before_dump: Callable[[], None],
    omit: Mapping[date, dict[str, Any]],
    listing: str | None,
) -> Copy:
    """A copy of the whole database from one snapshot, leaving out the data of the days of
    ``omit``, whose copies the manifest fixes as its dependencies."""
    keys = recipients(recipients_file)
    name = f"{prefix}{now.astimezone(UTC):%Y%m%dT%H%M%SZ}"
    from_copies = _sealed_fingerprints(directory)
    with _attempt(directory, name) as (temporary, final):
        plain = temporary / "volcado.dump"
        with _connect(dsn) as conn, conn.transaction():
            snapshot, snapshot_at = _snapshot(conn)
            sealed_days = [
                row[0] for row in conn.execute("SELECT day FROM market.archive_seals ORDER BY day")
            ]
            unsealed = sorted(set(omit) - set(sealed_days))
            if unsealed:
                raise BackupError(f"days not sealed cannot be left out: {unsealed}")
            tables = table_fingerprints(conn, set(sealed_days), from_copies, set(omit))
            generations = _newest_generations(conn, sorted(omit))
            knowledge = archive_knowledge(conn)
            catalog = catalog_fingerprint(conn)
            fixed = versions(conn)
            before_dump()
            _run(
                [
                    _tool("pg_dump"),
                    "--format=custom",
                    "--compress=zstd:3",
                    f"--snapshot={snapshot}",
                    # By partition, never with --exclude-table-data-and-children (02 §13.1).
                    *(
                        f"--exclude-table-data={qualified}"
                        for qualified, table in sorted(tables.items())
                        if table["modo"] == "excluido"
                    ),
                    f"--file={plain}",
                    f"--dbname={dsn}",
                ]
            )
        manifest = {
            "formato": FORMAT,
            "clase": kind,
            "nombre": name,
            "creada": now.astimezone(UTC).isoformat(),
            "instantanea": snapshot_at.astimezone(UTC).isoformat(),
            "dias_sellados": [day.isoformat() for day in sealed_days],
            "dias_excluidos": [
                {
                    "dia": day.isoformat(),
                    "copia_de_dia": omit[day],
                    "generacion": generations.get(day),
                }
                for day in sorted(omit)
            ],
            "listado": listing,
            "archivo": knowledge,
            "tablas": tables,
            "catalogo": catalog,
            "versiones": fixed,
            "destinatarios": keys,
            "volcado": _encrypt_and_drop(plain, temporary, recipients_file),
        }
        return _finish(directory, temporary, final, manifest)


def hot_copy(
    directory: Path,
    recipients_file: Path,
    *,
    now: datetime,
    dsn: str = DSN,
    before_dump: Callable[[], None] = lambda: None,
    omit: Mapping[date, dict[str, Any]] | None = None,
    listing: str | None = None,
) -> Copy:
    """The hourly copy of the whole database, from one snapshot (02 §13.1, point 4),
    without the data of the sealed days of ``omit`` (``omissions``)."""
    return _database_copy(
        directory,
        recipients_file,
        HOT_PREFIX,
        "caliente",
        now=now,
        dsn=dsn,
        before_dump=before_dump,
        omit=omit or {},
        listing=listing,
    )


def previa_copy(
    directory: Path,
    recipients_file: Path,
    *,
    now: datetime,
    dsn: str = DSN,
) -> Copy:
    """The copy taken before an update: self-contained, it never leaves out anything, so it
    has no dependency on any sealed-day copy (02 §13.1, point 4; E4c-2, point 4)."""
    return _database_copy(
        directory,
        recipients_file,
        PREVIA_PREFIX,
        "previa",
        now=now,
        dsn=dsn,
        before_dump=lambda: None,
        omit={},
        listing=None,
    )


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
            fixed = versions(conn)
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
            "versiones": fixed,
            "destinatarios": keys,
            "volcado": _encrypt_and_drop(plain, temporary, recipients_file),
        }
        return _finish(directory, temporary, final, manifest)


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
            "versiones": {
                "pg_dumpall": _run([_tool("pg_dumpall"), "--version"]),
                "age": _run([_tool("age"), "--version"]),
                "sistema": platform.platform(),
                "arquitectura": platform.machine(),
            },
            "destinatarios": keys,
            "volcado": _encrypt_and_drop(plain, temporary, recipients_file),
        }
        return _finish(directory, temporary, final, manifest)


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


def previa_retention(directory: Path) -> list[Path]:
    """The last two copies taken before an update stay, as in the bucket (D25)."""
    copies = _named(directory, PREVIA_PREFIX)
    for path in copies[:-PREVIA_KEEP]:
        shutil.rmtree(path)
    return copies[:-PREVIA_KEEP]


def pending_sealed_days(directory: Path, eligible: set[date], dsn: str = DSN) -> list[date]:
    """Sealed days without a registered copy that still need one (not yet eligible)."""
    with psycopg.connect(dsn) as conn:
        sealed = [
            row[0]
            for row in conn.execute(
                "SELECT s.day FROM market.archive_seals s WHERE NOT EXISTS"
                " (SELECT 1 FROM market.archive_retirements r WHERE r.day = s.day) ORDER BY s.day"
            )
        ]
    copied = {copy.path.name[len(DAY_PREFIX) :] for copy in published(directory, DAY_PREFIX)}
    return [d for d in sealed if d.isoformat() not in copied and d not in eligible]
