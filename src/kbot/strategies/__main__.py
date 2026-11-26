"""``svc-strategy``: ``python -m kbot.strategies`` (``02`` §12.6.4; F3-E5b).

Connects only through the Unix socket of PostgreSQL, with ``peer``, as ``kbot_strategy``
(``KBOT_STRATEGY_DSN`` changes it), and never imports the KuCoin client. Without a version
of the code (a followed file changed, or no commit) it does not start. It runs a cycle at
once and then at minutes 01, 16, 31 and 46 of each hour, second 30. ``--una-vez`` runs a
single cycle and ends, with an error code if it failed.

It is not deployed in F3-E5b: the deployment comes with its own review, once the owner
approves a version (§12.6.6).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import psycopg
import structlog

from kbot.core.clock import SystemClock
from kbot.db.roles import DATABASE, SOCKET_DIR
from kbot.strategies.breakout import Breakout
from kbot.strategies.mean_reversion import MeanReversion
from kbot.strategies.service import Service, next_cycle, postgres
from kbot.strategies.trend import Trend
from kbot.strategies.version import NoVersion, repository_commit

log = structlog.get_logger("kbot.strategies")
DSN = f"host={SOCKET_DIR} dbname={DATABASE} user=kbot_strategy"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="svc-strategy (02 §12.6.4)")
    parser.add_argument("--repositorio", type=Path, default=Path())
    parser.add_argument("--una-vez", action="store_true", help="un solo ciclo")
    arguments = parser.parse_args(argv)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
    try:
        commit = repository_commit(arguments.repositorio)
    except NoVersion as error:
        log.error("strategy_without_version", error=str(error))
        return 2
    dsn = os.environ.get("KBOT_STRATEGY_DSN", DSN)
    open_view, write = postgres(lambda: psycopg.connect(dsn))
    # The three fichas with the parameters approved by the owner on 20 November 2026; only
    # those whose version is in config.strategy_versions run.
    service = Service([Trend(), Breakout(), MeanReversion()], commit)
    clock = SystemClock()
    log.info("strategy_started", commit=commit, run_id=str(service.run_id))
    while True:
        try:
            done = service.cycle(open_view, write, clock.now())
            log.info(
                "strategy_cycle",
                corte=done.corte.isoformat(),
                computed=done.computed,
                signals=done.signals,
            )
        except Exception:  # a failed cycle writes nothing; the next retries
            log.exception("strategy_cycle_failed")
            if arguments.una_vez:
                return 1
        if arguments.una_vez:
            return 0
        wait = (next_cycle(clock.now()) - clock.now()).total_seconds()
        time.sleep(max(wait, 0.0))


if __name__ == "__main__":
    sys.exit(main())
