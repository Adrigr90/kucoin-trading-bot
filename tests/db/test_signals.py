"""The signals in a real PostgreSQL 18 (``02`` §12.6; F3-A10): what ``kbot_strategy`` can
do, the key and every constraint of ``trading.signals``, and ``svc-strategy`` over candles
written as the recorder writes them, without network."""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import sys
import textwrap
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from kbot.db.roles import DATABASE
from kbot.market.timeutil import as_utc
from kbot.strategies.recorded import CandleRow
from kbot.strategies.service import INSERT, Service, postgres
from tests.unit.strategies.recorder import COMMIT, Recorder, comparable, engine_rows
from tests.unit.strategies.support import START, short_fichas, walk_minutes

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]
REPO = Path(__file__).resolve().parents[2]
HASH = "9" * 64
T0 = datetime(2026, 1, 7, 10, 0, tzinfo=UTC)
PLAIN_INSERT = INSERT.removesuffix(" ON CONFLICT DO NOTHING")
DAY = timedelta(days=1)


@pytest.fixture
def strategy(connect: Connect) -> Iterator[psycopg.Connection]:
    with connect("kbot_strategy") as conn:
        yield conn


@pytest.fixture
def versions(admin: psycopg.Connection) -> Iterator[Callable[[str, str], None]]:
    """Approves versions as the migrations would, and removes them and their signals."""
    made: list[tuple[str, str]] = []

    def approve(family: str, params_hash: str) -> None:
        admin.execute(
            "INSERT INTO config.strategy_versions (family, params_hash, params, ficha,"
            " approved_on, approval) VALUES (%s, %s, '{}', '§12.3.4', '2026-11-23', 'prueba')",
            (family, params_hash),
        )
        made.append((family, params_hash))

    yield approve
    for family, params_hash in made:
        admin.execute(
            "DELETE FROM trading.signals WHERE family = %s AND params_hash = %s",
            (family, params_hash),
        )
        admin.execute(
            "DELETE FROM config.strategy_versions WHERE family = %s AND params_hash = %s",
            (family, params_hash),
        )


def signal(**changes: Any) -> dict[str, Any]:
    row = {
        "signal_id": uuid4(),
        "family": "tendencia",
        "code_commit": "a" * 40,
        "params_hash": HASH,
        "market": "FUTURES",
        "symbol": "T" + secrets.token_hex(6).upper(),
        "timeframe": "1h",
        "open_time": T0,
        "direction": 1,
        "signal_at": T0 + timedelta(hours=1, seconds=15),
        "context_open": T0 - timedelta(hours=4),
        "context_direction": "tendencia",
        "context_volatility": None,
        "signal_direction": "rango",
        "signal_volatility": "normal",
        "levels": Jsonb({"atr": 1.5}),
        "provenance": Jsonb([[T0.isoformat(), (T0 + timedelta(hours=1)).isoformat()]]),
        "provenance_minutes": 60,
        "rest_only_minutes": 60,
        "run_id": uuid4(),
    }
    row.update(changes)
    return row


def count(admin: psycopg.Connection, symbol: str) -> int:
    found = admin.execute("SELECT count(*) FROM trading.signals WHERE symbol = %s", (symbol,))
    return int(found.fetchone()[0])  # type: ignore[index]


# --- F3-A10: kbot_strategy inserts signals and can do nothing else -------------------------


def test_f3_a10_kbot_strategy_inserts_a_signal(
    strategy: psycopg.Connection, admin: psycopg.Connection, versions: Callable[[str, str], None]
) -> None:
    versions("tendencia", HASH)
    row = signal()
    strategy.execute(PLAIN_INSERT, row)
    assert count(admin, row["symbol"]) == 1


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM trading.signals",
        "UPDATE trading.signals SET direction = -1",
        "DELETE FROM trading.signals",
        "TRUNCATE trading.signals",
        "INSERT INTO config.strategy_versions (family, params_hash, params, ficha, approved_on,"
        " approval) VALUES ('tendencia', repeat('1', 64), '{}', '§12.3.4', '2026-11-23', 'x')",
        "INSERT INTO market.universe_snapshots (evaluated_at, market, symbol, rank, in_universe,"
        " l50_candidate, quote_volume_24h, criteria_version)"
        " VALUES (now(), 'SPOT', 'X-USDT', 1, true, false, 1, 'x')",
        "SELECT * FROM market.depth_metrics LIMIT 0",
        "SELECT * FROM config.risk_limits",
        "CREATE TABLE trading.x (a int)",
    ],
)
def test_f3_a10_kbot_strategy_can_do_nothing_else(
    strategy: psycopg.Connection, statement: str
) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        strategy.execute(statement)


@pytest.mark.parametrize(
    "table",
    [
        "market.candles",
        "market.instruments",
        "market.universe_snapshots",
        "config.strategy_versions",
    ],
)
def test_f3_a10_kbot_strategy_reads_what_it_needs(strategy: psycopg.Connection, table: str) -> None:
    strategy.execute(f"SELECT * FROM {table} LIMIT 0")


def test_only_kbot_strategy_reads_the_approved_versions(connect: Connect) -> None:
    for role in ("kbot_risk", "kbot_ingest", "kbot_web"):
        with connect(role) as conn, pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM config.strategy_versions")


# --- the key and the constraints of trading.signals ---------------------------------------


def test_the_key_of_a_signal_is_unique(
    strategy: psycopg.Connection, admin: psycopg.Connection, versions: Callable[[str, str], None]
) -> None:
    versions("tendencia", HASH)
    row = signal()
    strategy.execute(PLAIN_INSERT, row)
    with pytest.raises(errors.UniqueViolation):
        strategy.execute(PLAIN_INSERT, {**row, "signal_id": uuid4()})
    strategy.execute(INSERT, {**row, "signal_id": uuid4()})  # ON CONFLICT DO NOTHING: no error
    assert count(admin, row["symbol"]) == 1
    strategy.execute(INSERT, {**row, "signal_id": uuid4(), "code_commit": "b" * 40})
    assert count(admin, row["symbol"]) == 2  # another commit is another version


def test_a_signal_needs_an_approved_version(strategy: psycopg.Connection) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        strategy.execute(PLAIN_INSERT, signal(params_hash="8" * 64))


@pytest.mark.parametrize(
    "changes",
    [
        {"direction": 2},
        {"market": "SPOT", "symbol": "X-USDT", "direction": -1},
        {"family": "scalping"},
        {"code_commit": "A" * 40},
        {"code_commit": "a" * 39},
        {"timeframe": "2h"},
        {"symbol": "x-usdt"},
        {"context_direction": "subida"},
        {"signal_volatility": "alta"},
        {"provenance_minutes": 0, "rest_only_minutes": 0},
        {"rest_only_minutes": 61},
        {"rest_only_minutes": -1},
        {"open_time": T0 + timedelta(seconds=30)},
        {"signal_at": T0},
        {"context_open": T0 + timedelta(hours=1, seconds=15)},
        # Recorded before it exists: a signal in the future.
        {
            "open_time": datetime(2100, 1, 1, tzinfo=UTC),
            "signal_at": datetime(2100, 1, 1, 1, tzinfo=UTC),
            "context_open": datetime(2099, 12, 31, 20, tzinfo=UTC),
        },
    ],
)
def test_each_constraint_refuses_what_it_should(
    strategy: psycopg.Connection, versions: Callable[[str, str], None], changes: dict[str, Any]
) -> None:
    versions("tendencia", HASH)
    with pytest.raises(errors.CheckViolation):
        strategy.execute(PLAIN_INSERT, signal(**changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"signal_at": T0 + timedelta(microseconds=1), "context_open": T0},
        {"rest_only_minutes": 60, "provenance_minutes": 60},
        {"rest_only_minutes": 0},
        {"market": "SPOT", "symbol": "X-USDT", "direction": 1},
        {"direction": -1},
        {"context_direction": None, "context_volatility": None},
    ],
)
def test_each_constraint_accepts_its_frontier(
    strategy: psycopg.Connection,
    admin: psycopg.Connection,
    versions: Callable[[str, str], None],
    changes: dict[str, Any],
) -> None:
    versions("tendencia", HASH)
    row = signal(**changes)
    strategy.execute(PLAIN_INSERT, row)
    assert count(admin, row["symbol"]) == 1


# --- svc-strategy over what the recorder writes --------------------------------------------


@pytest.fixture
def recorded(
    connect: Connect, admin: psycopg.Connection, partitions: Callable[[date, date], None]
) -> Iterator[tuple[str, Recorder]]:
    """Three days of a random walk of one Futures contract, written as the recorder writes
    them, with the same rows in memory: mostly cerrada_rest, some cerrada_cruzada, one
    discrepante, one absent minute and one row written after the cut of the test."""
    symbol = "T" + secrets.token_hex(3).upper() + "USDTM"
    partitions(START.date(), (START + 3 * DAY).date())
    minutes = walk_minutes(3 * 1440, seed=9, symbol=symbol)
    absent = START + timedelta(days=2, hours=3, minutes=7)
    cross = {START + timedelta(days=2, hours=h) for h in range(0, 24, 5)}
    wrong = START + timedelta(days=2, hours=9, minutes=1)
    late = START + timedelta(days=2, hours=20, minutes=3)
    rows: list[CandleRow] = []
    quality: list[tuple[Any, ...]] = []
    candles: list[tuple[Any, ...]] = []
    for m in minutes:
        c = m.candle
        if c.open_time == absent:
            continue
        state = (
            "discrepante"
            if c.open_time == wrong
            else ("cerrada_cruzada" if c.open_time in cross else "cerrada_rest")
        )
        flow = "S3" if state == "cerrada_rest" else "S1"
        evidence = {
            "cerrada_rest": "vela_rest_suficiente",
            "cerrada_cruzada": "vela_cruzada_consistente",
            "discrepante": "vela_cruzada_discrepante",
        }[state]
        recorded_at = c.open_time + timedelta(minutes=1, seconds=15)
        if c.open_time == late:
            recorded_at = START + timedelta(days=3, hours=5)  # after the cut of the test
        ws = c.volume if state != "cerrada_rest" else None
        quality.append(("FUTURES", symbol, "trade", c.open_time, flow, 1 if flow != "S1" else 0))
        candles.append(
            (
                symbol,
                c.open_time,
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
                c.turnover,
                ws,
                c.turnover if ws is not None else None,
                state,
                evidence,
                flow,
                recorded_at,
            )
        )
        rows.append(
            CandleRow(
                c.open_time,
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
                c.turnover,
                state,
                recorded_at,
            )
        )
    with connect("kbot_ingest") as ingest, ingest.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO market.data_quality_minute (market, symbol, channel, minute_start,"
            " flow_state, messages, anomalies, sequence_breaks, reconnections)"
            " VALUES (%s, %s, %s, %s, %s, 60, %s, 0, %s)",
            [(*q, q[-1]) for q in quality],
        )
        cursor.executemany(
            "INSERT INTO market.candles (series, market, symbol, open_time, open, high, low,"
            " close, volume, turnover, ws_volume, ws_turnover, state, evidence, flow_channel,"
            " flow_state, recorded_at) VALUES ('last', 'FUTURES', %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, %s, %s, 'trade', %s, %s)",
            candles,
        )
        observed = START - DAY
        cursor.execute(
            "INSERT INTO market.instruments (market, symbol, observed_at, base_currency,"
            " quote_currency, settle_currency, tick_size, quantity_step, multiplier,"
            " min_quantity, min_funds, trading_status, is_trading, raw, recorded_at)"
            " VALUES ('FUTURES', %s, %s, 'T', 'USDT', 'USDT', 0.01, 1, 0.01, 1, NULL, 'Open',"
            " true, '{}', %s)",
            (symbol, observed, observed),
        )
        cursor.execute(
            "INSERT INTO market.universe_snapshots (evaluated_at, market, symbol, rank,"
            " in_universe, l50_candidate, quote_volume_24h, criteria_version, recorded_at)"
            " VALUES (%s, 'FUTURES', %s, 1, true, false, 1, 'prueba-f3e5b', %s)",
            (observed, symbol, observed),
        )
    memory = Recorder()
    memory.add("FUTURES", symbol, rows)
    memory.observe("FUTURES", symbol, observed, "0.01")
    memory.evaluate(observed, [("FUTURES", symbol)])
    try:
        yield symbol, memory
    finally:
        admin.execute(
            "DELETE FROM market.universe_snapshots WHERE criteria_version = 'prueba-f3e5b'"
        )
        admin.execute("DELETE FROM trading.signals WHERE symbol = %s", (symbol,))
        admin.execute("DELETE FROM market.instruments WHERE symbol = %s", (symbol,))


def stored(admin: psycopg.Connection, symbol: str) -> set[Any]:
    columns = (
        "family, code_commit, params_hash, market, symbol, timeframe, open_time, direction,"
        " signal_at, context_open, context_direction, context_volatility, signal_direction,"
        " signal_volatility, levels, provenance, provenance_minutes, rest_only_minutes"
    )
    with admin.cursor(row_factory=dict_row) as cursor:
        found = cursor.execute(
            f"SELECT {columns} FROM trading.signals WHERE symbol = %s",
            (symbol,),
        ).fetchall()
    rows = []
    for row in found:
        for key in ("open_time", "signal_at", "context_open"):
            row[key] = as_utc(row[key])
        row["direction"] = int(row["direction"])
        rows.append(comparable(row))
    return set(rows)


def test_svc_strategy_writes_the_engine_signals_of_the_recorded_candles(
    pghost: str,
    admin: psycopg.Connection,
    versions: Callable[[str, str], None],
    recorded: tuple[str, Recorder],
) -> None:
    symbol, memory = recorded
    fichas = short_fichas()[:1]
    for ficha in fichas:
        versions(ficha.family, ficha.params_hash())
    memory.approved = {(f.family, f.params_hash()) for f in fichas}

    def connect() -> psycopg.Connection:
        return psycopg.connect(host=pghost, dbname=DATABASE, user="kbot_strategy")

    open_view, write = postgres(connect)
    now = START + timedelta(days=3, minutes=31, seconds=30)
    done = Service(fichas, COMMIT).cycle(open_view, write, now)
    corte = done.corte
    wanted = set().union(
        *(
            engine_rows(memory, f, "FUTURES", symbol, START + n * DAY, corte)
            for f in fichas
            for n in (1, 2, 3)  # the days of the window of the cut: 6, 7 and 8 January
        )
    )
    assert wanted, "the synthetic candles must give signals"
    assert stored(admin, symbol) == wanted
    # Again, and from a new process: nothing new.
    Service(fichas, COMMIT).cycle(open_view, write, now)
    assert stored(admin, symbol) == wanted

    # Without network (F3E5B-07): a new process runs the same cycle in a network namespace
    # of its own, a barrier of the operating system that also stops the native connections of
    # libpq. A TCP connection of psycopg to a listener of the test is refused there, while
    # the same listener answers outside; the cycle works through the Unix socket.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    port = listener.getsockname()[1]
    with socket.create_connection(("127.0.0.1", port), timeout=5):
        accepted, _ = listener.accept()  # outside the namespace, the listener answers
        accepted.close()
    code = textwrap.dedent(
        f"""
        import sys
        from datetime import datetime
        sys.path.insert(0, {str(REPO)!r})
        import psycopg
        try:
            psycopg.connect(
                host="127.0.0.1", hostaddr="127.0.0.1", port={port}, dbname="kbot",
                user="kbot_strategy", connect_timeout=3,
            )
        except psycopg.OperationalError as error:
            print("tcp refused:", str(error).strip().splitlines()[0], file=sys.stderr)
        else:
            raise SystemExit("libpq reached the TCP listener: there is network")
        from kbot.strategies.service import Service, postgres
        from tests.unit.strategies.support import short_fichas
        open_view, write = postgres(
            lambda: psycopg.connect(host={pghost!r}, dbname="kbot", user="kbot_strategy")
        )
        done = Service(short_fichas()[:1], {COMMIT!r}).cycle(
            open_view, write, datetime.fromisoformat({now.isoformat()!r})
        )
        print(done.signals)
        """
    )
    finished = subprocess.run(
        [*without_network(), sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert "tcp refused" in finished.stderr
    assert int(finished.stdout.strip()) == len(wanted)
    assert stored(admin, symbol) == wanted
    listener.settimeout(0.5)
    with pytest.raises(TimeoutError):
        listener.accept()  # nothing from inside the namespace ever reached it
    listener.close()


def without_network() -> list[str]:
    """The prefix that runs a command in a new network namespace, as the user of the tests:
    ``unshare --net`` needs root, and ``setpriv`` gives the process back its user, so that
    PostgreSQL authenticates it by peer as before."""
    found = {
        name: shutil.which(name, path="/usr/bin:/bin:/usr/sbin:/sbin")
        for name in ("sudo", "unshare", "setpriv")
    }
    missing = [name for name, where in found.items() if where is None]
    if missing:
        if os.environ.get("KBOT_CI"):
            pytest.fail(f"{missing} are required in the CI")
        pytest.skip(f"{missing} not installed")
    return [
        str(found["sudo"]),
        "-n",
        str(found["unshare"]),
        "--net",
        str(found["setpriv"]),
        f"--reuid={os.getuid()}",
        f"--regid={os.getgid()}",
        "--init-groups",
    ]
