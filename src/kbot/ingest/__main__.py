"""Run the recorder: ``python -m kbot.ingest``.

On the server (E4) it writes to PostgreSQL as kbot_ingest over the Unix socket with peer
authentication (``--sink db``, the default). On a PC without PostgreSQL, ``--sink file``
writes JSON lines to a folder for a short live check, for example::

    uv run python -m kbot.ingest --sink file --out C:/temp/grabacion --minutes 15

``--sink rows`` writes instead the rows PostgreSQL would receive, one JSON line per row in
``<out>/<table>.jsonl``, to measure the archive with real data (02 §3.9, stage E3c).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import psycopg
import structlog

from kbot.db.roles import DATABASE, SOCKET_DIR
from kbot.ingest.rows import RowSink
from kbot.ingest.service import Recorder
from kbot.ingest.sinks import DatabaseSink, FileSink, Sink


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


async def _main(arguments: argparse.Namespace) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if os.name != "nt":
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stop.set)
    if arguments.minutes:
        loop.call_later(arguments.minutes * 60, stop.set)

    sink: Sink
    if arguments.sink == "rows":
        row_sink = RowSink(arguments.out)
        try:
            await Recorder(row_sink).run(stop)
        finally:
            row_sink.close()
        summary = "\n".join(f"{key}: {value}" for key, value in sorted(row_sink.counts.items()))
        print(summary)
        (arguments.out / "summary.txt").write_text(summary + "\n", encoding="utf-8")
        return 0
    if arguments.sink == "file":
        file_sink = FileSink(arguments.out)
        await Recorder(file_sink).run(stop)
        summary = "\n".join(f"{key}: {value}" for key, value in sorted(file_sink.counts.items()))
        print(summary)
        (arguments.out / "summary.txt").write_text(summary + "\n", encoding="utf-8")
        return 0
    dsn = os.environ.get("KBOT_INGEST_DSN", f"host={SOCKET_DIR} dbname={DATABASE} user=kbot_ingest")
    with psycopg.connect(dsn, autocommit=True) as conn:
        sink = DatabaseSink(conn)
        await Recorder(sink).run(stop)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="KuCoin public market data recorder")
    parser.add_argument("--sink", choices=("db", "file", "rows"), default="db")
    parser.add_argument("--out", type=Path, default=Path("grabacion"))
    parser.add_argument("--minutes", type=float, default=0, help="stop after this many minutes")
    arguments = parser.parse_args()
    if arguments.sink == "rows" and arguments.out.exists() and any(arguments.out.iterdir()):
        parser.error(f"--sink rows needs a new or empty --out folder: {arguments.out}")
    _configure_logging()
    try:
        return asyncio.run(_main(arguments))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
