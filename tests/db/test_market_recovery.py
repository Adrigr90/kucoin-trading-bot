"""Phase 2 acceptance A5 in a real PostgreSQL, written as kbot_ingest.

A run records depth slots, the process stops, and a second run resumes from what the
database says it recorded: the stop is a gap with cause ``proceso_detenido``, every slot
of the stop is ``desconectado``, the old book is not valid again after the reconnection,
and every slot from the first run to the second has exactly one terminal state. Writing a
batch twice changes nothing.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from kbot.market.depth import DepthChannel
from kbot.market.gaps import GapCause, GapTracker
from kbot.market.store import depth_channel_name, resume_point, write_depth

pytestmark = pytest.mark.db

Connect = Callable[..., psycopg.Connection]
T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
NONE = timedelta(0)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def book(sequence: int, published: datetime) -> dict[str, Any]:
    ns = (published - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1) * 1000
    return {
        "T": "obu.SPOT",
        "P": ns,
        "d": {
            "s": "x",
            "C": sequence,
            "O": sequence,
            "M": ns,
            "b": [["100.0", "2"], ["99.9", "1"]],
            "a": [["100.2", "1"], ["100.3", "3"]],
        },
    }


@pytest.fixture
def ingest(connect: Connect) -> Iterator[psycopg.Connection]:
    with connect("kbot_ingest") as conn:
        yield conn


def test_a5_a_stop_and_a_restart(ingest: psycopg.Connection) -> None:
    symbol = "R" + secrets.token_hex(5).upper() + "-USDT"
    channel_name = depth_channel_name(5)
    assert resume_point(ingest, "SPOT", symbol, 5) is None

    # First run: subscribed at 0, one book at 1 s, recorded until 60 s, then the process stops.
    first = DepthChannel("SPOT", symbol, 5, multiplier=Decimal(1), started_at=at(0))
    gaps = GapTracker("SPOT", channel_name, symbol, started_at=at(0), resumed_from=None)
    first.connection_up(at(0))
    opening = gaps.up(at(0))
    first.on_message(book(1, at(1)), at(1))
    slots, minutes = first.close_until(at(60), NONE)
    write_depth(ingest, slots, minutes, [g for g in [opening] if g is not None])
    assert [s.state.value for s in slots] == ["valida"] * 6
    assert len(minutes) == 1

    # Second run, five minutes later: it resumes from the database, not from memory.
    resumed = resume_point(ingest, "SPOT", symbol, 5)
    assert resumed == at(60)
    second = DepthChannel("SPOT", symbol, 5, multiplier=Decimal(1), started_at=resumed)
    gaps = GapTracker("SPOT", channel_name, symbol, started_at=at(300), resumed_from=resumed)
    second.connection_up(at(302))
    stop_gap = gaps.up(at(302))
    second.on_message(book(9, at(325)), at(325))
    slots, minutes = second.close_until(at(330), NONE)
    write_depth(ingest, slots, minutes, [g for g in [stop_gap] if g is not None])
    write_depth(ingest, slots, minutes, [g for g in [stop_gap] if g is not None])  # a rerun

    rows = ingest.execute(
        "SELECT slot_start, state, sequence FROM market.depth_slot_status"
        " WHERE market = 'SPOT' AND symbol = %s AND depth = 5 ORDER BY slot_start",
        (symbol,),
    ).fetchall()
    assert [r[0] for r in rows] == [at(10 * i) for i in range(33)], "contiguous, one per slot"
    states = {r[0]: r[1] for r in rows}
    assert all(states[at(10 * i)] == "desconectado" for i in range(6, 31))  # 60 s to 310 s
    assert states[at(310)] == "no_recibida", "the old book is not valid after reconnecting"
    assert states[at(320)] == "valida"
    assert [r[2] for r in rows if r[0] == at(320)] == [9]

    gap_rows = ingest.execute(
        "SELECT start_at, end_at, cause FROM market.data_gaps"
        " WHERE market = 'SPOT' AND channel = %s AND symbol = %s ORDER BY start_at",
        (channel_name, symbol),
    ).fetchall()
    assert gap_rows == [(at(60), at(302), GapCause.PROCESO_DETENIDO.value)]

    metric_slots = ingest.execute(
        "SELECT slot_start FROM market.depth_metrics WHERE symbol = %s ORDER BY slot_start",
        (symbol,),
    ).fetchall()
    assert [r[0] for r in metric_slots] == [at(10 * i) for i in range(6)] + [at(320)]
    snapshots = ingest.execute(
        "SELECT minute_start, slot_start, sequence FROM market.depth_snapshots"
        " WHERE symbol = %s ORDER BY minute_start",
        (symbol,),
    ).fetchall()
    assert snapshots == [(at(0), at(50), 1)]
