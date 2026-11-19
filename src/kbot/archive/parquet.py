"""Parquet files of the archive (spec 02 §3.9): exact types, zstd, written and read back.

``numeric`` is ``decimal128(38, 24)``: up to 24 decimals and 14 integer digits. A value that
does not fit without losing digits makes the write fail (``pyarrow`` raises
``ArrowInvalid``), it is never rounded. Moments are UTC microseconds, and ``jsonb`` is stored
as its JCS text, so the content fingerprint of the file read back is the same as that of the
rows read from PostgreSQL.

Format 2 (18 November 2026) widened the decimals from the ``decimal128(38, 18)`` of format 1:
KuCoin sends Spot volumes and turnovers with up to 21 decimals, measured in the recorder's
data (and up to 11 integer digits), which format 1 refused. It stays within a precision of 38
because DuckDB, the reader of the archive, turns a wider decimal into a ``DOUBLE`` without
an error and loses its digits (review DC-01); ``check_duckdb`` refuses such a reading before
anything is computed. The files of both formats read back as the same exact decimals, but
never in the same DuckDB reading, which takes the types of its first file (DC-02).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from kbot.archive.canonical import Fingerprint

FORMAT_VERSION = "2"
COMPRESSION = "zstd"
COMPRESSION_LEVEL = 3  # provisional until the measurement of point 66 (stage E3c)

ARROW_TYPES: dict[str, pa.DataType] = {
    "numeric": pa.decimal128(38, 24),
    "timestamp with time zone": pa.timestamp("us", tz="UTC"),
    "text": pa.string(),
    "jsonb": pa.string(),
    "smallint": pa.int16(),
    "integer": pa.int32(),
    "bigint": pa.int64(),
    "boolean": pa.bool_(),
    "date": pa.date32(),
}


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str  # as format_type() names it

    @property
    def arrow_type(self) -> pa.DataType:
        try:
            return ARROW_TYPES[self.sql_type]
        except KeyError:
            raise TypeError(f"{self.name}: no Parquet type for {self.sql_type}") from None


def schema(columns: Sequence[Column], metadata: dict[str, str]) -> pa.Schema:
    fields = [pa.field(c.name, c.arrow_type) for c in columns]
    return pa.schema(fields, metadata={k.encode(): v.encode() for k, v in metadata.items()})


def write(
    path: Path,
    columns: Sequence[Column],
    batches: Iterable[Sequence[Sequence[Any]]],
    metadata: dict[str, str],
) -> Fingerprint:
    """Writes the rows, in batches already in primary key order, and returns their
    fingerprint. The file is synced to disk before returning."""
    target = schema(columns, metadata)
    fingerprint = Fingerprint([c.name for c in columns])
    with pq.ParquetWriter(
        path, target, compression=COMPRESSION, compression_level=COMPRESSION_LEVEL
    ) as writer:
        wrote = False
        for rows in batches:
            if not rows:
                continue
            for row in rows:
                fingerprint.add(row)
            arrays = [
                pa.array([row[i] for row in rows], type=column.arrow_type)
                for i, column in enumerate(columns)
            ]
            writer.write_table(pa.Table.from_arrays(arrays, schema=target))
            wrote = True
        if not wrote:
            writer.write_table(target.empty_table())
    sync(path)
    return fingerprint


def read_fingerprint(path: Path, columns: Sequence[Column]) -> Fingerprint:
    """The fingerprint of a file read back, with the same serialization as the rows it was
    written from."""
    parquet = pq.ParquetFile(path)
    names = [c.name for c in columns]
    if parquet.schema_arrow.names != names:
        raise ValueError(f"{path.name}: columns {parquet.schema_arrow.names}, expected {names}")
    fingerprint = Fingerprint(names)
    for batch in parquet.iter_batches():
        columns_data = [batch.column(i).to_pylist() for i in range(batch.num_columns)]
        for row in zip(*columns_data, strict=True):
            fingerprint.add(row)
    return fingerprint


def check_duckdb(connection: Any, paths: Iterable[str | Path]) -> None:
    """Before computing in DuckDB with the Parquet files that one query reads together: read
    as the query reads them (``read_parquet`` over the same list, in its order and without
    options), every decimal of every file, also inside lists and structs, must reach DuckDB
    with its own type, and nothing else may become a decimal. Otherwise this raises
    ``ValueError`` and nothing is computed.

    DuckDB 2.0 changes decimals without an error. It reads a precision over 38 as a
    ``DOUBLE`` (review DC-01). It reads several files with the types of the first one, so
    after an empty file of format 1 the values of format 2 come back with 18 decimals, and
    ``union_by_name`` rounds them too, in either order (DC-02). A text or a ``DOUBLE`` in a
    file read after one with a decimal is cast to that decimal, and rounded. No later ``CAST``
    brings the digits back. ``connection`` is a DuckDB connection; DuckDB is a development
    dependency, the server never reads the archive with it."""
    names = [str(path) for path in paths]
    if not names:
        return
    relation = connection.sql("SELECT * FROM read_parquet(?)", params=[names])
    read = _leaves(relation.limit(0).to_arrow_table().schema)
    for name in dict.fromkeys(names):
        held = _leaves(pq.read_schema(name))
        changed = [
            f"{place} is {held.get(place, 'missing')} in the file"
            f" and {read.get(place, 'missing')} in DuckDB"
            for place in sorted(held.keys() | read.keys())
            if (_decimal(held.get(place)) or _decimal(read.get(place)))
            and held.get(place) != read.get(place)
        ]
        if changed:
            raise ValueError(f"{name}: {'; '.join(changed)}, so it would not be read exactly")


def _decimal(kind: pa.DataType | None) -> bool:
    return kind is not None and pa.types.is_decimal(kind)


_LISTS = (
    pa.types.is_list,
    pa.types.is_large_list,
    pa.types.is_fixed_size_list,
    pa.types.is_list_view,
    pa.types.is_large_list_view,
)


def _leaves(schema: pa.Schema) -> dict[str, pa.DataType]:
    """The type of every value of a schema by its place: a column, a field of a struct
    (``column.field``) or the items of a list of any kind (``column[]``), whatever name the
    writer gave them (``item`` in ``pyarrow``, ``l`` in DuckDB)."""
    leaves: dict[str, pa.DataType] = {}
    pending = [(field.name, field.type) for field in schema]
    while pending:
        place, kind = pending.pop()
        if pa.types.is_struct(kind):
            fields = [kind.field(i) for i in range(kind.num_fields)]
            pending += [(f"{place}.{field.name}", field.type) for field in fields]
        elif any(is_list(kind) for is_list in _LISTS):
            pending.append((f"{place}[]", kind.value_type))
        else:
            leaves[place] = kind
    return leaves


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync(path: Path) -> None:
    """Flushes a file, or the entries of a directory, to the disk. Every error of the system
    is raised (review of E3b, E3b-02): a generation is published only after all its syncs.
    The one exception is a directory on Windows, which cannot be opened to sync it; the
    archive runs on the Linux server, and Windows is only the owner's PC for tests."""
    if path.is_dir() and os.name == "nt":
        return
    # A file is opened for writing too: Windows refuses to sync a read-only descriptor.
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) if path.is_dir() else os.O_RDWR
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
