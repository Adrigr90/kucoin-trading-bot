"""The day files of the history on the PC (``02`` §12.1, point 1; F3-E1).

* **One Parquet file per market, symbol and UTC day,** zstd, with the columns and types of
  ``candles`` (E3, format 2: decimals in ``decimal128(38, 24)``, moments in UTC
  microseconds), in ``<root>/<market>/<symbol>/<day>.parquet``. Each row is a candle of
  ``last`` that KuCoin returned: ``cerrada_rest``, ``vela_rest_suficiente``, and flow S3,
  because there is no recorded flow (§3.4 [H12]). A minute without a candle has no row.
* **Next to it, its record** (``<day>.json``): the SHA-256 and the rows of the Parquet file,
  the window of the day, every request with its outcome and the minutes in conflict, each
  with the different candles that KuCoin gave for it, none of which is in the Parquet
  file. With them the coverage report is rebuilt from the files alone (``coverage``). A
  record without the minutes in conflict was written before 19 November 2026, when a
  conflict stopped the download, so it has none.
* **Both are written atomically:** a temporary name, synced, then renamed, the Parquet file
  first. A record whose SHA-256 does not match its file does not count.
* **What was asked, apart from the days:** ``<root>/<market>/<symbol>/solicitado.json``
  keeps the windows asked for the instrument, merged, and the download writes it before
  any request. The report walks those windows, not the files: a day without files is
  ``no_descargado``, and one whose files are not a valid pair too, named (F3E1-02).
* **A day is closed** when it has ended, none of its minutes is left undownloaded and it was
  downloaded at least ``SETTLING`` (48 h) after its end: KuCoin may still change a recent
  day, as it did on 19 November 2026 (CD-19 and CD-20). Until then each download writes
  it again. A closed day is never written again: the download is resumable and idempotent.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from kbot.archive import parquet
from kbot.archive.parquet import Column
from kbot.history.coverage import Presence, Request, Window, merged, presence
from kbot.market.last_candles import CandleEvidence, CandleState, Ohlcv
from kbot.market.mark_candles import FlowState
from kbot.market.timeutil import as_utc

FORMAT = "kbot.history 1"
REQUESTED = "solicitado.json"
REQUESTED_FORMAT = "kbot.history solicitado 1"
DAY = timedelta(days=1)
# A day closes only if downloaded this long after its end: the 48 h during which the
# recorder still asks for its REST candles (02 §3.8 and §12.1, point 1; CD-20).
SETTLING = timedelta(hours=48)

# The columns of market.candles (migration 0003), in its order.
CANDLE_COLUMNS: tuple[Column, ...] = (
    Column("series", "text"),
    Column("market", "text"),
    Column("symbol", "text"),
    Column("open_time", "timestamp with time zone"),
    Column("open", "numeric"),
    Column("high", "numeric"),
    Column("low", "numeric"),
    Column("close", "numeric"),
    Column("volume", "numeric"),
    Column("turnover", "numeric"),
    Column("ws_volume", "numeric"),
    Column("ws_turnover", "numeric"),
    Column("state", "text"),
    Column("evidence", "text"),
    Column("samples", "integer"),
    Column("flow_channel", "text"),
    Column("flow_state", "text"),
    Column("recorded_at", "timestamp with time zone"),
)


@dataclass(frozen=True)
class Conflict:
    """A minute for which one answer of KuCoin brought two or more different candles: none
    is used, and all of them are kept, as KuCoin gave them."""

    minute: datetime
    candles: tuple[Ohlcv, ...]


@dataclass(frozen=True)
class DayRecord:
    market: str
    symbol: str
    day: date
    window: Window
    requests: tuple[Request, ...]
    rows: int
    sha256: str
    downloaded_at: datetime
    conflicts: tuple[Conflict, ...] = ()

    @property
    def in_conflict(self) -> list[datetime]:
        return [conflict.minute for conflict in self.conflicts]

    @property
    def closed(self) -> bool:
        """Ended, downloaded at least ``SETTLING`` after its end, and none of its minutes
        left undownloaded. A minute in conflict was answered, so it does not keep its day
        open."""
        end = datetime.combine(self.day, datetime.min.time(), UTC) + DAY
        if self.window.end < end:
            return False  # the window stops before the end of the day: not over yet
        if self.downloaded_at < end + SETTLING:
            return False  # KuCoin may still change it: the next download asks again
        states = presence(self.window, self.requests, (), self.in_conflict)
        return Presence.NO_DESCARGADO not in states.values()


def paths(root: Path, market: str, symbol: str, day: date) -> tuple[Path, Path]:
    folder = root / market / symbol
    return folder / f"{day.isoformat()}.parquet", folder / f"{day.isoformat()}.json"


def write_day(
    root: Path,
    market: str,
    symbol: str,
    day: date,
    window: Window,
    candles: Sequence[Ohlcv],
    requests: Sequence[Request],
    downloaded_at: datetime,
    conflicts: Sequence[Conflict] = (),
) -> DayRecord:
    """Write the candles of a day and its record. Refuses to write over a closed day."""
    held = read_record(root, market, symbol, day)
    if held is not None and held.closed:
        raise FileExistsError(f"{market} {symbol} {day}: closed, never written again")
    start = datetime.combine(day, datetime.min.time(), UTC)
    if not (start <= window.start and window.end <= start + DAY):
        raise ValueError(f"{day}: the window must lie within the day")
    for candle in candles:
        if not (window.start <= as_utc(candle.open_time, "open_time") < window.end):
            raise ValueError(f"{day}: a candle outside the window: {candle.open_time}")
    for conflict in conflicts:
        if not (window.start <= as_utc(conflict.minute, "minute") < window.end):
            raise ValueError(f"{day}: a conflict outside the window: {conflict.minute}")
        different = len(set(conflict.candles)) == len(conflict.candles) >= 2
        if not different or any(c.open_time != conflict.minute for c in conflict.candles):
            raise ValueError(f"{day}: a conflict needs two different candles of its minute")
    # Present and in conflict at once, or a conflict no answer covers: refused here.
    presence(window, requests, [c.open_time for c in candles], [c.minute for c in conflicts])
    target, record_path = paths(root, market, symbol, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    recorded = as_utc(downloaded_at, "downloaded_at")
    rows = [_row(market, symbol, candle, recorded) for candle in sorted(candles, key=_open)]
    temporary = target.with_suffix(".parquet.tmp")
    metadata = {"format": FORMAT, "market": market, "symbol": symbol, "day": day.isoformat()}
    parquet.write(temporary, CANDLE_COLUMNS, [rows], metadata)
    os.replace(temporary, target)
    parquet.sync(target.parent)
    record = DayRecord(
        market,
        symbol,
        day,
        window,
        tuple(requests),
        len(rows),
        parquet.sha256(target),
        recorded,
        tuple(sorted(conflicts, key=_minute)),
    )
    _write_json(record_path, _encode(record))
    return record


def read_record(root: Path, market: str, symbol: str, day: date) -> DayRecord | None:
    """The record of a day, or None if there is none or it does not match its file."""
    return read_day(root, market, symbol, day)[0]


def read_day(root: Path, market: str, symbol: str, day: date) -> tuple[DayRecord | None, str]:
    """The record of a day and ``""`` if its two files are a valid pair; else None and why
    not: ``""`` too if the day has no file at all (never downloaded), and a reason if its
    files do not make a valid day (a write stopped halfway, or damage)."""
    target, record_path = paths(root, market, symbol, day)
    if not target.exists() and not record_path.exists():
        return None, ""
    if not record_path.exists():
        return None, "sin registro"
    if not target.exists():
        return None, "sin archivo Parquet"
    record = _decode(json.loads(record_path.read_text(encoding="utf-8")))
    if record.sha256 != parquet.sha256(target):
        return None, "la huella no coincide"
    return record, ""


def record_request(root: Path, market: str, symbol: str, window: Window) -> list[Window]:
    """Add ``window`` to what was asked for the instrument, before asking anything, and
    return all of it, merged (F3E1-02)."""
    asked = merged([*requested(root, market, symbol), window])
    path = root / market / symbol / REQUESTED
    path.parent.mkdir(parents=True, exist_ok=True)
    content = {
        "formato": REQUESTED_FORMAT,
        "mercado": market,
        "simbolo": symbol,
        "ventanas": [[w.start.isoformat(), w.end.isoformat()] for w in asked],
    }
    _write_json(path, content)
    return asked


def requested(root: Path, market: str, symbol: str) -> list[Window]:
    """The windows asked for the instrument, merged; none if it was never asked for."""
    path = root / market / symbol / REQUESTED
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("formato") != REQUESTED_FORMAT:
        raise ValueError(f"unknown format of {path}: {data.get('formato')!r}")
    windows = [
        Window(datetime.fromisoformat(s), datetime.fromisoformat(e)) for s, e in data["ventanas"]
    ]
    if merged(windows) != windows:
        raise ValueError(f"{path}: the windows are not merged and in order")
    return windows


def stored_days(root: Path, market: str, symbol: str) -> list[date]:
    """The days with some file of the instrument, valid or not."""
    folder = root / market / symbol
    days = set()
    for path in folder.iterdir() if folder.exists() else []:
        name = path.name.split(".", 1)[0]
        if path.suffix in (".parquet", ".json") and path.name != REQUESTED:
            days.add(date.fromisoformat(name))
    return sorted(days)


def read_candles(root: Path, market: str, symbol: str, day: date) -> list[Ohlcv]:
    """The candles of a day file, in order, as they were written."""
    target, _record = paths(root, market, symbol, day)
    table = pq.read_table(target)
    names = [c.name for c in CANDLE_COLUMNS]
    if table.schema.names != names:
        raise ValueError(f"{target.name}: columns {table.schema.names}, expected {names}")
    data = table.to_pydict()
    return [
        Ohlcv(
            data["open_time"][i],
            data["open"][i],
            data["high"][i],
            data["low"][i],
            data["close"][i],
            data["volume"][i],
            data["turnover"][i],
        )
        for i in range(table.num_rows)
    ]


def _open(candle: Ohlcv) -> datetime:
    return candle.open_time


def _minute(conflict: Conflict) -> datetime:
    return conflict.minute


def _row(market: str, symbol: str, candle: Ohlcv, recorded: datetime) -> list[Any]:
    return [
        "last",
        market,
        symbol,
        candle.open_time,
        candle.open,
        candle.high,
        candle.low,
        candle.close,
        candle.volume,
        candle.turnover,
        None,
        None,
        CandleState.CERRADA_REST.value,
        CandleEvidence.REST_SUFICIENTE.value,
        None,
        "trade",
        FlowState.S3.value,
        recorded,
    ]


def _encode(record: DayRecord) -> dict[str, Any]:
    return {
        "formato": FORMAT,
        "mercado": record.market,
        "simbolo": record.symbol,
        "dia": record.day.isoformat(),
        "ventana": [record.window.start.isoformat(), record.window.end.isoformat()],
        "peticiones": [
            {"desde": r.first.isoformat(), "hasta": r.last.isoformat(), "correcta": r.succeeded}
            for r in record.requests
        ],
        "filas": record.rows,
        "sha256": record.sha256,
        "descargado": record.downloaded_at.isoformat(),
        "en_conflicto": [
            {
                "apertura": conflict.minute.isoformat(),
                "velas": [[format(v, "f") for v in _values(c)] for c in conflict.candles],
            }
            for conflict in record.conflicts
        ],
    }


def _values(candle: Ohlcv) -> tuple[Decimal, ...]:
    """Open, high, low, close, volume and turnover: a REST row without its time."""
    return candle.open, candle.high, candle.low, candle.close, candle.volume, candle.turnover


def _decode(data: dict[str, Any]) -> DayRecord:
    if data.get("formato") != FORMAT:
        raise ValueError(f"unknown record format: {data.get('formato')!r}")
    start, end = (datetime.fromisoformat(v) for v in data["ventana"])
    return DayRecord(
        market=data["mercado"],
        symbol=data["simbolo"],
        day=date.fromisoformat(data["dia"]),
        window=Window(start, end),
        requests=tuple(
            Request(
                datetime.fromisoformat(r["desde"]),
                datetime.fromisoformat(r["hasta"]),
                bool(r["correcta"]),
            )
            for r in data["peticiones"]
        ),
        rows=int(data["filas"]),
        sha256=str(data["sha256"]),
        downloaded_at=datetime.fromisoformat(data["descargado"]),
        conflicts=tuple(_conflict(c) for c in data.get("en_conflicto", [])),
    )


def _conflict(data: dict[str, Any]) -> Conflict:
    minute = datetime.fromisoformat(data["apertura"])
    candles = tuple(Ohlcv(minute, *(Decimal(v) for v in values)) for values in data["velas"])
    return Conflict(minute, candles)


def _write_json(path: Path, content: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(content, file, ensure_ascii=False, indent=1)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
    parquet.sync(path.parent)
