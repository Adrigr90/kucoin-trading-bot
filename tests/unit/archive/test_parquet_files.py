"""Parquet files of the archive: exact types, zstd, read back (02 §3.9)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

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
    assert file.schema_arrow.field("price").type == pa.decimal128(38, 18)


def test_an_empty_day_is_a_file_with_its_columns_and_no_row(tmp_path: Path) -> None:
    path = tmp_path / "e.parquet"
    written = parquet.write(path, COLUMNS, [], {})
    read = parquet.read_fingerprint(path, COLUMNS)
    assert (read.rows, read.hexdigest()) == (0, written.hexdigest())
    assert pq.ParquetFile(path).schema_arrow.names == [c.name for c in COLUMNS]


@pytest.mark.parametrize("value", ["1.0000000000000000001", "123456789012345678901"])
def test_a_number_that_does_not_fit_is_refused_never_rounded(tmp_path: Path, value: str) -> None:
    with pytest.raises(pa.ArrowInvalid):
        parquet.write(
            tmp_path / "x.parquet", (Column("price", "numeric"),), [[(Decimal(value),)]], {}
        )


def test_a_file_with_other_columns_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "t.parquet"
    parquet.write(path, COLUMNS, [ROWS], {})
    with pytest.raises(ValueError, match="columns"):
        parquet.read_fingerprint(path, COLUMNS[:-1])


def test_a_type_without_a_parquet_form_is_refused() -> None:
    with pytest.raises(TypeError):
        _ = Column("x", "uuid").arrow_type
