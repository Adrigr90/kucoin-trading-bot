"""Size and query time of the Parquet archive, measured on a recording (02 §3.9, point 8).

``python -m kbot.ingest --sink rows`` writes, per table, the rows PostgreSQL would receive
(``<folder>/<table>.jsonl``); a CI test proves they are the rows ``DatabaseSink`` stores
(``tests/db/test_rows_equivalence.py``). This tool turns them into the files of the archive:
one per table and UTC day, with the columns, types and primary key order of migration 0003,
in the format of ``kbot.archive.parquet`` (row groups of ``BATCH`` rows). Per table it
measures the bytes without compression and with zstd 3 and 9, and for ``depth_snapshots``
also with each book as a typed list instead of its JCS text. Then it times typical queries
with DuckDB, over the recorded minutes and over copies of them that make 90 days.

What the recording does not hold is made up, close to its real value:
- ``recorded_at``, the moment of the insert, which only PostgreSQL sets: the partition key
  plus 70 s and a few microseconds that differ from row to row;
- the rows of ``minute_status``, which the close of a day computes in SQL: one per minute
  of each instrument's trade flow, with data and its candle (``datos``);
- a column no INSERT names takes its constant default, as in PostgreSQL.

The sizes per day are those of the recording scaled to 1,440 minutes. With the bytes per row
that a CI test measured in PostgreSQL 18 (``tests/db/test_storage_scale.py``), it also projects
the 90 hot days of the database and the data volume after one and two years (02 §3.1 and
§10.3).

Usage: ``uv run python -m tools.measure_archive <recording folder> [--out <folder>]``
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pglast import ast, parse_sql
from pglast.enums import ConstrType

from kbot.archive.archiver import BATCH, TABLES
from kbot.archive.canonical import json_text
from kbot.archive.parquet import ARROW_TYPES
from kbot.ingest.rows import PROGRESS

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0003_particiones.sql"
VARIANTS: dict[str, tuple[str, int | None]] = {
    "none": ("NONE", None),
    "zstd3": ("zstd", 3),
    "zstd9": ("zstd", 9),
}
HOT_DAYS = 90  # in PostgreSQL (02 §3.9)
QUERY_DAYS = HOT_DAYS  # the DuckDB queries also run over this many days of copies
RUNS = 5
MINUTES_PER_DAY = 1440
INSERT_DELAY = timedelta(seconds=70)
SQL_TYPES = {
    "int2": "smallint",
    "int4": "integer",
    "int8": "bigint",
    "bool": "boolean",
    "numeric": "numeric",
    "timestamptz": "timestamp with time zone",
    "text": "text",
    "jsonb": "jsonb",
    "date": "date",
}
BOOK_LEVEL = pa.struct([("price", pa.decimal128(38, 18)), ("size", pa.decimal128(38, 18))])

# Bytes per row in PostgreSQL 18 (heap, TOAST, indexes), measured in the CI by
# tests/db/test_storage_scale.py on 11 Nov 2026 (commit dafe12c), with an hour of a realistic
# universe with the mix of 02 §3.1: two books of level 5 for each of level 50 (E3c-02).
# A table it does not fill (rare rows, and data_gaps) takes the largest total.
POSTGRES_ROW_BYTES: dict[str, tuple[int, int, int]] = {
    "data_quality_minute": (102, 6, 199),
    "candles": (190, 8, 228),
    "ws_candles": (137, 14, 96),
    "minute_status": (123, 14, 82),
    "depth_slot_status": (97, 2, 184),
    "depth_metrics": (206, 2, 82),
    "depth_snapshots": (910, 9, 100),
}
LARGEST_ROW = max(POSTGRES_ROW_BYTES.values(), key=sum)
WAL_BYTES_PER_ROW = 495  # written, same test
EMPTY_PARTITION_BYTES = 25_486  # files of its indexes and TOAST, and catalog rows; same CI
PARTITIONS = 12 * (HOT_DAYS + 1 + 14)  # the hot days, today and the 14 prepared ahead
# The WAL on disk: max_wal_size by default, as an estimated reserve. It is a soft limit that
# PostgreSQL may exceed (heavy writes, a failing archive or a lagging replica; the design has
# neither), so it is no bound (review of E3c, E3c-06).
WAL_RESERVE_BYTES = 1 << 30
MARGIN = 1.2  # the margin of the estimate of 02 §3.1
DATA_VOLUME_GB = 110  # 02 §10.3, option B


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str  # as format_type() names it
    default: Any = None  # constant default, if the column has one


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]

    @property
    def key(self) -> str:
        return TABLES[self.name]


def _constant(expr: Any) -> Any:
    if isinstance(expr, ast.A_Const) and not expr.isnull:
        value = expr.val
        if isinstance(value, ast.String):
            return value.sval
        if isinstance(value, ast.Integer):
            return value.ival
        if isinstance(value, ast.Boolean):
            return value.boolval
    return None  # clock_timestamp() of recorded_at, made up below


def table_specs(text: str) -> dict[str, TableSpec]:
    """The twelve partitioned tables as migration 0003 creates them."""
    specs: dict[str, TableSpec] = {}
    for raw in parse_sql(text):
        stmt = raw.stmt
        if not isinstance(stmt, ast.CreateStmt) or stmt.relation.relname not in TABLES:
            continue
        columns: list[Column] = []
        primary_key: tuple[str, ...] = ()
        for element in stmt.tableElts or ():
            if isinstance(element, ast.ColumnDef):
                names = [name.sval for name in element.typeName.names]
                default = next(
                    (
                        _constant(c.raw_expr)
                        for c in element.constraints or ()
                        if c.contype == ConstrType.CONSTR_DEFAULT
                    ),
                    None,
                )
                columns.append(Column(element.colname, SQL_TYPES[names[-1]], default))
            elif (
                isinstance(element, ast.Constraint) and element.contype == ConstrType.CONSTR_PRIMARY
            ):
                primary_key = tuple(key.sval for key in element.keys)
        specs[stmt.relation.relname] = TableSpec(stmt.relation.relname, tuple(columns), primary_key)
    if set(specs) != set(TABLES):
        raise ValueError(f"tables of 0003 not found: {sorted(set(TABLES) - set(specs))}")
    return specs


def read_rows(path: Path) -> list[dict[str, Any]]:
    """The rows of one file. A last line without its newline is a row still being written
    by a recording that goes on, and is left out. A recording stopped abruptly may leave
    zeros where its last buffered rows never reached the disk: the file ends there."""
    text = path.read_text(encoding="utf-8").split("\x00", 1)[0]
    lines = text.split("\n")
    return [json.loads(line) for line in lines[:-1] if line]


def minute_status_rows(quality: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per minute of each trade flow, as the close of a day writes most of them."""
    return [
        {
            "market": row["market"],
            "symbol": row["symbol"],
            "minute_start": row["minute_start"],
            "status": "datos",
            "candle_state": "cerrada_cruzada" if row["flow_state"] == "S1" else "cerrada_rest",
            "flow_channel": "trade",
            "flow_state": row["flow_state"],
            "candle_series": "last",
            "gap_start": None,
        }
        for row in quality
        if row["channel"] == "trade"
    ]


def _value(raw: Any, sql_type: str) -> Any:
    if raw is None:
        return None
    if sql_type == "numeric":
        return Decimal(raw)
    if sql_type == "timestamp with time zone":
        return datetime.fromisoformat(raw)
    if sql_type == "date":
        return date.fromisoformat(raw)
    if sql_type == "jsonb":
        return json_text(raw)
    return raw


def typed_rows(spec: TableSpec, rows: Sequence[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """The rows with the values PostgreSQL would hold, in primary key order."""
    position = {c.name: i for i, c in enumerate(spec.columns)}
    key = position[spec.key]
    result: list[tuple[Any, ...]] = []
    for number, row in enumerate(rows):
        unknown = set(row) - set(position)
        if unknown:
            raise ValueError(f"{spec.name}: columns not in 0003: {sorted(unknown)}")
        values = [
            _value(row[c.name], c.sql_type) if c.name in row else c.default for c in spec.columns
        ]
        if "recorded_at" in position and "recorded_at" not in row:
            jitter = timedelta(microseconds=number * 251 % 1_000_000)
            values[position["recorded_at"]] = values[key] + INSERT_DELAY + jitter
        result.append(tuple(values))
    order = [position[name] for name in spec.primary_key]
    result.sort(key=lambda values: tuple(values[i] for i in order))
    return result


def arrow_table(
    spec: TableSpec, rows: Sequence[tuple[Any, ...]], *, typed_books: bool = False
) -> pa.Table:
    """The table of the archive; with ``typed_books``, each ``jsonb`` book (a list of
    [price, size] pairs) as a list of decimal pairs instead of its JCS text."""
    fields = []
    arrays = []
    for i, column in enumerate(spec.columns):
        values = [row[i] for row in rows]
        arrow_type = ARROW_TYPES[column.sql_type]
        if typed_books and column.sql_type == "jsonb":
            arrow_type = pa.list_(BOOK_LEVEL)
            values = [
                None
                if v is None
                else [{"price": Decimal(p), "size": Decimal(s)} for p, s in json.loads(v)]
                for v in values
            ]
        fields.append(pa.field(column.name, arrow_type))
        arrays.append(pa.array(values, type=arrow_type))
    return pa.Table.from_arrays(arrays, schema=pa.schema(fields))


def write(table: pa.Table, path: Path, variant: str) -> int:
    compression, level = VARIANTS[variant]
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table, path, compression=compression, compression_level=level, row_group_size=BATCH
    )
    return path.stat().st_size


@dataclass
class Measure:
    table: str
    rows: int = 0
    days: dict[str, int] = field(default_factory=dict)  # rows per UTC day
    bytes: Counter[str] = field(default_factory=Counter)  # per variant, every day


def by_day(spec: TableSpec, rows: Sequence[tuple[Any, ...]]) -> Iterator[tuple[date, list[Any]]]:
    key = [c.name for c in spec.columns].index(spec.key)
    days: dict[date, list[Any]] = defaultdict(list)
    for row in rows:
        days[row[key].date()].append(row)
    yield from sorted(days.items())


def window(loaded: dict[str, list[dict[str, Any]]]) -> tuple[datetime, datetime]:
    """The minutes ``[first, end)`` whose writing the recording proves complete (review of
    E3c, E3c-01). The clocks are the two tables written for every channel without fail:
    a minute is complete when it has as many rows of ``data_quality_minute`` as the fullest
    minute, and each of its six slots as many rows of ``depth_slot_status`` as the fullest
    slot. The window runs from the first complete minute to the last one, so the edges a
    start or an abrupt stop leave half written are out; a minute inside it that lacks rows
    lacked them in the recording too. Tables of rare rows, such as late books, never set it."""
    minutes = Counter(
        datetime.fromisoformat(r["minute_start"]) for r in loaded["data_quality_minute"]
    )
    slots = Counter(datetime.fromisoformat(r["slot_start"]) for r in loaded["depth_slot_status"])
    full_minute, full_slot = max(minutes.values()), max(slots.values())
    complete = sorted(
        minute
        for minute, rows in minutes.items()
        if rows == full_minute
        and all(slots[minute + timedelta(seconds=10 * i)] == full_slot for i in range(6))
    )
    if not complete:
        raise ValueError("the recording has no complete minute")
    return complete[0], complete[-1] + timedelta(minutes=1)


def _within(
    rows: list[dict[str, Any]], column: str, span: tuple[datetime, datetime]
) -> list[dict[str, Any]]:
    first, end = span
    return [row for row in rows if first <= datetime.fromisoformat(row[column]) < end]


class Unproven(ValueError):
    """The recording cannot prove that its files are complete."""


def load(recording: Path, *, unmarked: bool = False) -> dict[str, list[dict[str, Any]]]:
    """The rows of every table and of data_gaps, as of the latest progress mark that every
    file reaches (review of E3c, E3c-01). A recording without marks, made before them, is
    only read when ``unmarked`` says so; ``consistent`` then checks it."""
    names = [*TABLES, "data_gaps"]
    files = {name: recording / f"{name}.jsonl" for name in names}
    rows = {name: read_rows(path) for name, path in files.items() if path.exists()}
    progress = recording / PROGRESS
    if not progress.exists():
        if not unmarked:
            raise Unproven(f"no progress marks ({PROGRESS}): its completeness cannot be proved")
        return rows
    for mark in reversed(read_rows(progress)):
        if all(len(rows.get(name, [])) >= count for name, count in mark.items()):
            return {name: rows.get(name, [])[: mark.get(name, 0)] for name in names}
    raise Unproven("no progress mark is reached by every file")


def consistent(
    loaded: dict[str, list[dict[str, Any]]], span: tuple[datetime, datetime]
) -> tuple[datetime, datetime]:
    """The window, without the last minutes whose metrics are fewer than their valid slots:
    the writers put a slot's metrics right after its state, so a minute that lacks them is
    a file cut short. A minute inside the window that lacks them makes the recording
    unproven (review of E3c, E3c-01)."""
    first, end = span

    def per_minute(rows: list[dict[str, Any]]) -> Counter[datetime]:
        return Counter(
            datetime.fromisoformat(r["slot_start"]).replace(second=0)
            for r in rows
            if first <= datetime.fromisoformat(r["slot_start"]) < end
        )

    valid = per_minute([r for r in loaded["depth_slot_status"] if r["state"] == "valida"])
    metrics = per_minute(loaded.get("depth_metrics", []))
    minutes = [first + timedelta(minutes=n) for n in range((end - first) // timedelta(minutes=1))]
    wrong = [m for m in minutes if metrics[m] != valid[m]]
    while wrong and wrong[-1] == end - timedelta(minutes=1):
        end = wrong.pop()
    if wrong:
        raise Unproven(f"metrics and valid slots differ inside the recording: {wrong[0]}")
    if end <= first:
        raise Unproven("no complete minute left")
    return first, end


def measure(
    recording: Path, out: Path, *, unmarked: bool = False
) -> tuple[int, dict[str, Measure], int]:
    """Minutes recorded, the measures per table, and the rows of data_gaps."""
    specs = table_specs(MIGRATION.read_text(encoding="utf-8"))
    loaded = load(recording, unmarked=unmarked)
    span = consistent(loaded, window(loaded))
    gaps = len(_within(loaded.pop("data_gaps", []), "start_at", span))
    loaded = {name: _within(rows, TABLES[name], span) for name, rows in loaded.items() if rows}
    quality = loaded.get("data_quality_minute", [])
    loaded["minute_status"] = minute_status_rows(quality)
    measures: dict[str, Measure] = {}
    for name, raw_rows in sorted(loaded.items()):
        if not raw_rows:
            continue
        spec = specs[name]
        result = measures[name] = Measure(name, len(raw_rows))
        for day, rows in by_day(spec, typed_rows(spec, raw_rows)):
            result.days[day.isoformat()] = len(rows)
            table = arrow_table(spec, rows)
            for variant in VARIANTS:
                path = out / variant / name / f"{day.isoformat()}.parquet"
                result.bytes[variant] += write(table, path, variant)
            if any(c.sql_type == "jsonb" for c in spec.columns):
                typed = arrow_table(spec, rows, typed_books=True)
                for variant in ("zstd3", "zstd9"):
                    path = out / f"{variant}_typed" / name / f"{day.isoformat()}.parquet"
                    result.bytes[f"{variant}_typed"] += write(typed, path, variant)
    return (span[1] - span[0]) // timedelta(minutes=1), measures, gaps


SERIES = (
    "SELECT slot_start, mid, spread FROM read_parquet(?)"
    " WHERE market = 'SPOT' AND symbol = ? AND depth = 5 ORDER BY slot_start"
)
UNIVERSE = (
    "SELECT market, symbol, depth, count(*), avg(spread / mid) FROM read_parquet(?) GROUP BY ALL"
)
FIRST_BID_TEXT = (
    "SELECT avg(CAST(json_extract_string(bids, '$[0][1]') AS DECIMAL(38, 18))) FROM read_parquet(?)"
)
FIRST_BID_TYPED = "SELECT avg(bids[1].size) FROM read_parquet(?)"


def timed(
    connection: duckdb.DuckDBPyConnection, query: str, *parameters: object
) -> tuple[float, float]:
    """Milliseconds of the first run and median of ``RUNS`` runs, with the result taken
    as an Arrow table, as an analysis would."""
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        connection.sql(query, params=list(parameters)).to_arrow_table()
        times.append((time.perf_counter() - start) * 1000)
    return times[0], statistics.median(times)


def queries(out: Path, symbol: str, minutes: int) -> dict[str, tuple[float, float]]:
    """Two typical queries over ``depth_metrics`` (one instrument's series, and the whole
    universe aggregated), over the recorded minutes and over as many copies of them as
    make ``QUERY_DAYS`` days; and the first bid size of every snapshot, from the JCS text and
    from the typed lists."""

    def files(folder: str, table: str) -> list[str]:
        return [str(p) for p in sorted((out / folder / table).glob("*.parquet"))]

    metrics = files("zstd3", "depth_metrics")
    copies = math.ceil(QUERY_DAYS * MINUTES_PER_DAY / minutes)
    # A synthetic test: the same files read again and again. The rows are those of so many
    # days, but not their files, their cache or their disk (review of E3c).
    days = f"{QUERY_DAYS} days as {copies} repetitions of the same files"
    connection = duckdb.connect()
    return {
        "series, recorded": timed(connection, SERIES, metrics, symbol),
        "universe, recorded": timed(connection, UNIVERSE, metrics),
        f"series, {days}": timed(connection, SERIES, metrics * copies, symbol),
        f"universe, {days}": timed(connection, UNIVERSE, metrics * copies),
        "first bid size, JCS text": timed(
            connection, FIRST_BID_TEXT, files("zstd3", "depth_snapshots")
        ),
        "first bid size, typed lists": timed(
            connection, FIRST_BID_TYPED, files("zstd3_typed", "depth_snapshots")
        ),
    }


def report(minutes: int, measures: dict[str, Measure], times: dict[str, Any]) -> list[str]:
    scale = MINUTES_PER_DAY / minutes
    lines = [f"Recorded minutes: {minutes} (sizes per day scaled by {scale:.2f})", ""]
    header = f"{'table':<26}{'rows':>9}{'rows/day':>11}"
    variants = ["none", "zstd3", "zstd9", "zstd3_typed", "zstd9_typed"]
    lines.append(header + "".join(f"{v + ' MB/day':>20}" for v in variants) + f"{'B/row z3':>10}")
    totals: Counter[str] = Counter()
    for name, m in sorted(measures.items()):
        cells = []
        for variant in variants:
            if variant in m.bytes:
                per_day = m.bytes[variant] * scale
                totals[variant] += per_day
                cells.append(f"{per_day / 1e6:>20.3f}")
            else:
                totals[variant] += m.bytes[variant.removesuffix("_typed")] * scale
                cells.append(f"{'':>20}")
        per_row = m.bytes["zstd3"] / m.rows
        lines.append(
            f"{name:<26}{m.rows:>9}{m.rows * scale:>11.0f}" + "".join(cells) + f"{per_row:>10.1f}"
        )
    lines.append(f"{'total':<46}" + "".join(f"{totals[v] / 1e6:>20.3f}" for v in variants))
    lines.append("")
    lines.append(
        "Per year (365 days), GB: "
        + ", ".join(f"{v} {totals[v] * 365 / 1e9:.2f}" for v in variants)
    )
    lines.append("")
    lines.append("DuckDB, ms (first run / median of 5):")
    lines.extend(
        f"  {name:<34}{first:>9.1f} {median:>9.1f}" for name, (first, median) in times.items()
    )
    return lines


def postgres(
    minutes: int, measures: dict[str, Measure], gaps: int, parquet_per_day: float
) -> tuple[list[str], dict[str, float]]:
    """The 90 hot days in PostgreSQL, with the bytes per row measured in the CI and the
    margins for the partitions, the WAL and the 20 % of 02 §3.1, and the data volume of
    02 §10.3 after one and two years of Parquet (``parquet_per_day``, in bytes).

    ``data_gaps`` is not partitioned and its retention is still to be defined (02 §3.9), so
    it is not part of the 90 days: it grows with every day recorded, 365 days in the first
    year and 730 in the second (review of E3c, E3c-03). Its bytes per row are the largest
    measured, an upper bound. The WAL kept is an estimated reserve, not a bound:
    ``max_wal_size`` is a soft limit that PostgreSQL may exceed (E3c-06)."""
    scale = MINUTES_PER_DAY / minutes
    per_day = {name: m.rows * scale for name, m in measures.items()}
    heap, toast, index = (
        sum(
            rows * POSTGRES_ROW_BYTES.get(name, LARGEST_ROW)[part] for name, rows in per_day.items()
        )
        for part in range(3)
    )
    day = heap + toast + index
    gaps_day = gaps * scale * sum(LARGEST_ROW)
    hot = day * HOT_DAYS + PARTITIONS * EMPTY_PARTITION_BYTES + WAL_RESERVE_BYTES
    database = hot * MARGIN
    year = parquet_per_day * 365
    gaps_1, gaps_2 = (gaps_day * days * MARGIN for days in (365, 730))
    values = {
        "day": day,
        "heap": heap,
        "toast": toast,
        "index": index,
        "wal_per_day": (sum(per_day.values()) + gaps * scale) * WAL_BYTES_PER_ROW,
        "hot": hot,
        "database": database,
        "data_gaps_per_day": gaps_day,
        "data_gaps_year_1": gaps_1,
        "data_gaps_year_2": gaps_2,
        "parquet_year": year,
        "year_1": database + gaps_1 + year,
        "year_2": database + gaps_2 + 2 * year,
    }
    gb = {name: value / 1e9 for name, value in values.items()}
    lines = [
        "PostgreSQL, with the bytes per row measured in the CI (heap + TOAST + indexes):",
        f"  per day {day / 1e6:.0f} MB: heap {heap / 1e6:.0f}, TOAST {toast / 1e6:.0f},"
        f" indexes {index / 1e6:.0f}; WAL written {values['wal_per_day'] / 1e6:.0f} MB per day",
        f"  {HOT_DAYS} hot days {day * HOT_DAYS / 1e9:.1f} GB + {PARTITIONS} partitions"
        f" {PARTITIONS * EMPTY_PARTITION_BYTES / 1e9:.2f} + WAL reserve (max_wal_size, a soft"
        f" limit) {WAL_RESERVE_BYTES / 1e9:.2f} = {gb['hot']:.1f} GB;"
        f" with {MARGIN - 1:.0%} more, {gb['database']:.1f} GB",
        f"  data_gaps without retention: {gaps * scale:.0f} rows per day,"
        f" {gb['data_gaps_year_1']:.2f} GB after year 1 and {gb['data_gaps_year_2']:.2f} after"
        f" year 2, with the margin",
        f"Data volume of {DATA_VOLUME_GB} GB (02 §10.3), with Parquet zstd 3:"
        f" year 1 {gb['database']:.1f} + {gb['data_gaps_year_1']:.1f} + {gb['parquet_year']:.1f}"
        f" = {gb['year_1']:.1f} GB; year 2 {gb['database']:.1f} + {gb['data_gaps_year_2']:.1f}"
        f" + {2 * gb['parquet_year']:.1f} = {gb['year_2']:.1f} GB",
    ]
    return lines, values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("recording", type=Path, help="folder written by --sink rows")
    parser.add_argument(
        "--out", type=Path, help="folder of the Parquet files (default: <recording>/parquet)"
    )
    parser.add_argument(
        "--unmarked",
        action="store_true",
        help="read a recording without progress marks (made before them), checked only by"
        " its metrics against its valid slots",
    )
    arguments = parser.parse_args(argv)
    out = arguments.out or arguments.recording / "parquet"
    if out.exists() and any(out.iterdir()):
        # Files of an earlier run would mix with this one's (review of E3c, E3c-05).
        parser.error(f"the output folder must be new or empty: {out}")
    try:
        minutes, measures, gaps = measure(arguments.recording, out, unmarked=arguments.unmarked)
    except Unproven as error:
        parser.error(f"the recording is not proven complete: {error}")
    metrics = measures.get("depth_metrics")
    symbol = "BTC-USDT"
    times = queries(out, symbol, minutes) if metrics is not None else {}
    lines = report(minutes, measures, times)
    parquet_per_day = sum(m.bytes["zstd3"] for m in measures.values()) * MINUTES_PER_DAY / minutes
    projection, values = postgres(minutes, measures, gaps, parquet_per_day)
    print("\n".join([*lines, "", *projection]))
    summary = {
        "minutes": minutes,
        "tables": {
            name: {"rows": m.rows, "days": m.days, "bytes": dict(m.bytes)}
            for name, m in measures.items()
        },
        "data_gaps_rows": gaps,
        "duckdb_ms": {name: {"first": f, "median": md} for name, (f, md) in times.items()},
        "postgres_bytes": values,
    }
    (out / "measure.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
