"""Parquet files of the archive: exact types, zstd, read back (02 §3.9)."""

from __future__ import annotations

import errno
import os
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kbot.archive import parquet
from kbot.archive.parquet import Column

COLUMNS = (
    Column("market", "text"),
    Column("depth", "smallint"),
    Column("sequence", "bigint"),
    Column("price", "numeric"),
    Column("at", "timestamp with time zone"),
    Column("ok", "boolean"),
    Column("levels", "jsonb"),
    Column("day", "date"),
    Column("count", "integer"),
)
AT = datetime(2026, 11, 10, 12, 0, 0, 500000, tzinfo=UTC)
ROWS = [
    ("SPOT", 5, 1, Decimal("100.5"), AT, True, '[["100.5","2"]]', date(2026, 11, 10), 3),
    ("SPOT", 50, 2, Decimal("-0.000000000000000001"), AT, False, None, date(2026, 11, 10), 0),
    ("FUTURES", 5, 3, Decimal(0), AT, None, "[]", None, None),
    ("FUTURES", 5, 4, None, AT, True, "[]", date(2026, 11, 10), -7),
]


def test_a_file_read_back_holds_exactly_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "t.parquet"
    written = parquet.write(path, COLUMNS, [ROWS[:2], ROWS[2:]], {"kbot.table": "t"})
    read = parquet.read_fingerprint(path, COLUMNS)
    assert (read.rows, read.hexdigest()) == (4, written.hexdigest())
    assert len(parquet.sha256(path)) == 64
    file = pq.ParquetFile(path)
    assert file.schema_arrow.metadata[b"kbot.table"] == b"t"
    assert file.metadata.row_group(0).column(0).compression == "ZSTD"
    assert file.schema_arrow.field("price").type == pa.decimal128(38, 24)
    assert file.schema_arrow.metadata.get(b"kbot.format") is None, "the archiver adds it"


def test_an_empty_day_is_a_file_with_its_columns_and_no_row(tmp_path: Path) -> None:
    path = tmp_path / "e.parquet"
    written = parquet.write(path, COLUMNS, [], {})
    read = parquet.read_fingerprint(path, COLUMNS)
    assert (read.rows, read.hexdigest()) == (0, written.hexdigest())
    assert pq.ParquetFile(path).schema_arrow.names == [c.name for c in COLUMNS]


@pytest.mark.parametrize(
    "value",
    ["1." + "0" * 24 + "1", "-0." + "0" * 24 + "1", "1" + "0" * 14, "-1" + "0" * 14],
    ids=["25 decimals", "25 decimals, negative", "15 integer digits", "15, negative"],
)
def test_a_number_that_does_not_fit_is_refused_never_rounded(tmp_path: Path, value: str) -> None:
    """Beyond 24 decimals or 14 integer digits the write fails before any reader can
    compute with the value: nothing is rounded and nothing is written."""
    path = tmp_path / "x.parquet"
    with pytest.raises(pa.ArrowInvalid):
        parquet.write(path, (Column("price", "numeric"),), [[(Decimal(value),)]], {})


# What KuCoin sends that decimal128(38, 18) refused, and the limits of the format.
REAL_AND_LIMITS = [
    "7685.59829732743707761776",  # NEAR-USDT turnover, 17 Nov 12:25: 20 decimals
    "31221.04204122215401008424",  # ZEC-USDT turnover, 17 Nov 18:01: 20 decimals
    "33.65669128230540883166",  # its volume: 20 decimals
    "7685.598297327437077617761",  # 21 decimals, as the WebSocket turnovers
    "99999999999.12345678901234567890",  # 11 integer digits and 20 decimals
    "1.000000000000000000000001",  # DuckDB read it as 1.0 from decimal256 (DC-01)
    "0." + "0" * 23 + "1",  # 24 decimals, the most
    "9" * 14 + "." + "9" * 24,  # 14 integer digits and 24 decimals, the largest
    "-" + "9" * 14 + "." + "9" * 24,
]


@pytest.mark.parametrize("value", REAL_AND_LIMITS)
def test_the_real_decimals_of_kucoin_fit_exactly(tmp_path: Path, value: str) -> None:
    """Format 2 (18 Nov 2026): the Spot volumes and turnovers KuCoin sends, with up to 21
    decimals, that decimal128(38, 18) refused and would have stopped the archive of the
    17th. They are read back exactly, with the same fingerprint."""
    path = tmp_path / "x.parquet"
    columns = (Column("turnover", "numeric"),)
    written = parquet.write(path, columns, [[(Decimal(value),)]], {})
    read = parquet.read_fingerprint(path, columns)
    assert (read.rows, read.hexdigest()) == (1, written.hexdigest())
    assert pq.read_table(path).column(0).to_pylist() == [Decimal(value)]


def test_duckdb_reads_the_decimals_exactly(tmp_path: Path) -> None:
    """Review DC-01: DuckDB, the reader of the archive, must see a decimal and the exact
    values. A precision over 38 (decimal256) it reads as a DOUBLE, without an error, and
    7685.59829732743707761776 comes back as 7685.598297327437."""
    path = tmp_path / "x.parquet"
    columns = (Column("turnover", "numeric"),)
    parquet.write(path, columns, [[(Decimal(v),) for v in REAL_AND_LIMITS]], {})
    query = "SELECT turnover, typeof(turnover) FROM read_parquet(?)"
    with duckdb.connect() as conn:
        parquet.check_duckdb(conn, [path])
        rows = conn.sql(query, params=[str(path)]).fetchall()
    assert [kind for _, kind in rows] == ["DECIMAL(38,24)"] * len(REAL_AND_LIMITS)
    assert [value for value, _ in rows] == [Decimal(v) for v in REAL_AND_LIMITS]


@pytest.mark.parametrize("nested", [False, True], ids=["column", "in a list of structs"])
def test_a_reading_that_would_lose_digits_is_refused_before_computing(
    tmp_path: Path, nested: bool
) -> None:
    """Review DC-01: a decimal wider than 38 digits reaches DuckDB as a DOUBLE, also inside a
    list of structs (as the typed books of the measurement), and 1.000000000000000000000001
    would be 1.0. The check refuses the file before any query; the decimals of formats 1
    and 2, each in its own reading, pass it."""
    value = Decimal("7685.598297327437077617")  # fits in format 1 too

    def file(name: str, kind: pa.DataType) -> Path:
        level = pa.list_(pa.struct([("price", kind)]))
        column = pa.array([[{"price": value}]], level) if nested else pa.array([value], kind)
        path = tmp_path / f"{name}.parquet"
        pq.write_table(pa.table({"turnover": column}), path)
        return path

    formats = [file("format_1", pa.decimal128(38, 18)), file("format_2", pa.decimal128(38, 24))]
    wide = file("wide", pa.decimal256(76, 24))
    place = "turnover[].price" if nested else "turnover"
    with duckdb.connect() as conn:
        for path in formats:
            parquet.check_duckdb(conn, [path])
        with pytest.raises(
            ValueError, match=rf"wide\.parquet: {re.escape(place)} is decimal256.*double"
        ):
            parquet.check_duckdb(conn, [wide])


DC_01 = ["7685.59829732743707761776", "1.000000000000000000000001"]


@pytest.mark.parametrize("empty_first", [True, False], ids=["empty file first", "empty last"])
def test_files_are_checked_as_the_query_reads_them_together(
    tmp_path: Path, empty_first: bool
) -> None:
    """Review DC-02: DuckDB reads several files with the types of the first one. After an
    empty file of format 1, the values of DC-01 in a file of format 2 would come back with
    18 decimals (1.000000000000000000000001 as 1.000000000000000000). The check reads the
    files as the query does, together and in its order: an empty file of format 1 and one of
    format 2 are refused in either order, before computing, and the file whose type would
    change is named; an empty file of format 2 and the full one are read exactly."""
    numeric = (Column("turnover", "numeric"),)
    full, empty_2 = tmp_path / "format_2.parquet", tmp_path / "empty_2.parquet"
    parquet.write(full, numeric, [[(Decimal(v),) for v in DC_01]], {})
    parquet.write(empty_2, numeric, [], {})
    empty_1 = tmp_path / "empty_1.parquet"
    pq.write_table(pa.table({"turnover": pa.array([], pa.decimal128(38, 18))}), empty_1)

    def together(empty: Path) -> list[str]:
        return [str(path) for path in ([empty, full] if empty_first else [full, empty])]

    changed, held, read = ("format_2", 24, 18) if empty_first else ("empty_1", 18, 24)
    refused = (
        rf"{changed}\.parquet: turnover is decimal128\(38, {held}\) in the file"
        rf" and decimal128\(38, {read}\) in DuckDB"
    )
    query = "SELECT turnover, typeof(turnover) FROM read_parquet(?)"
    with duckdb.connect() as conn:
        with pytest.raises(ValueError, match=refused):
            parquet.check_duckdb(conn, together(empty_1))
        parquet.check_duckdb(conn, together(empty_2))
        rows = conn.sql(query, params=[together(empty_2)]).fetchall()
    assert rows == [(Decimal(v), "DECIMAL(38,24)") for v in DC_01]


@pytest.mark.parametrize(
    "later", [pa.array(["1.0000000000000000000000009"]), pa.array([0.1])], ids=["text", "double"]
)
def test_nothing_read_after_a_decimal_may_become_one(tmp_path: Path, later: pa.Array) -> None:
    """Found while fixing DC-02: in a file read after one with a decimal, DuckDB casts the
    text or the DOUBLE of the same column to that decimal without an error, and rounds it
    (the text comes back as 1.000000000000000000000001). The check refuses it."""
    first, other = tmp_path / "first.parquet", tmp_path / "later.parquet"
    parquet.write(first, (Column("turnover", "numeric"),), [[(Decimal(DC_01[0]),)]], {})
    pq.write_table(pa.table({"turnover": later}), other)
    refused = rf"later\.parquet: turnover is {later.type} in the file and decimal128\(38, 24\)"
    with duckdb.connect() as conn, pytest.raises(ValueError, match=refused):
        parquet.check_duckdb(conn, [first, other])


def test_a_file_with_other_columns_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "t.parquet"
    parquet.write(path, COLUMNS, [ROWS], {})
    with pytest.raises(ValueError, match="columns"):
        parquet.read_fingerprint(path, COLUMNS[:-1])


def test_a_type_without_a_parquet_form_is_refused() -> None:
    with pytest.raises(TypeError):
        _ = Column("x", "uuid").arrow_type


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot open a directory to sync it")
def test_an_error_syncing_a_directory_is_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E3b-02: on the server, an I/O error syncing a directory stops the archive."""

    def eio(descriptor: int) -> None:
        raise OSError(errno.EIO, "input/output error")

    monkeypatch.setattr(parquet.os, "fsync", eio)
    with pytest.raises(OSError):
        parquet.sync(tmp_path)
