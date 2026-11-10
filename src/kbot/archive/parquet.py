"""Parquet files of the archive (spec 02 §3.9): exact types, zstd, written and read back.

``numeric`` is ``decimal128(38, 18)``: a value that does not fit without losing digits makes
the write fail (``pyarrow`` raises ``ArrowInvalid``), it is never rounded. Moments are UTC
microseconds, and ``jsonb`` is stored as its JCS text, so the content fingerprint of the file
read back is the same as that of the rows read from PostgreSQL.
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

FORMAT_VERSION = "1"
COMPRESSION = "zstd"
COMPRESSION_LEVEL = 3  # provisional until the measurement of point 66 (stage E3c)

ARROW_TYPES: dict[str, pa.DataType] = {
    "numeric": pa.decimal128(38, 18),
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
