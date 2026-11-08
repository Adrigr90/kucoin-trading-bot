"""Data gaps (02 §3.8; acceptance F2-A5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kbot.market.gaps import GapCause, GapTracker, cause_from_state_detail

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def tracker(resumed_from: datetime | None = None, started: float = 0) -> GapTracker:
    return GapTracker("SPOT", "obu5", "BTC-USDT", started_at=at(started), resumed_from=resumed_from)


def test_a_first_run_records_the_time_until_the_first_subscription() -> None:
    gaps = tracker()
    gap = gaps.up(at(2))
    assert gap is not None and (gap.start_at, gap.end_at) == (at(0), at(2))
    assert gap.cause is GapCause.DESCONEXION
    assert gaps.up(at(3)) is None, "nothing open any more"


def test_a_restart_records_the_stop_as_proceso_detenido() -> None:
    gaps = tracker(resumed_from=at(60), started=300)
    gap = gaps.up(at(302))
    assert gap is not None
    assert (gap.start_at, gap.end_at, gap.cause) == (at(60), at(302), GapCause.PROCESO_DETENIDO)


def test_each_disconnection_is_one_gap_with_its_cause() -> None:
    gaps = tracker()
    gaps.up(at(1))
    gaps.down(at(10), GapCause.SIN_PONG, "ProtocolViolation: no pong in time")
    gaps.down(at(12), GapCause.DESCONEXION)  # already open: the first cause stays
    gap = gaps.up(at(15))
    assert gap is not None
    assert (gap.start_at, gap.end_at, gap.cause) == (at(10), at(15), GapCause.SIN_PONG)


@pytest.mark.parametrize(
    ("detail", "cause"),
    [
        ("SessionRotation: planned reconnection after 84600 s", GapCause.ROTACION_PLANIFICADA),
        ("ProtocolViolation: no pong in time", GapCause.SIN_PONG),
        ("ProtocolViolation: subscription refused: {...}", GapCause.SUSCRIPCION_RECHAZADA),
        ("ProtocolViolation: invalid JSON from the server: x", GapCause.MENSAJE_INVALIDO),
        ("ConnectionClosedError: no close frame received or sent", GapCause.DESCONEXION),
        ("OSError: [Errno 101] Network is unreachable", GapCause.DESCONEXION),
    ],
)
def test_causes_come_from_the_stream_events(detail: str, cause: GapCause) -> None:
    assert cause_from_state_detail(detail) is cause


def test_times_must_carry_a_zone() -> None:
    with pytest.raises(ValueError, match="time zone"):
        GapTracker("SPOT", "obu5", "BTC-USDT", started_at=datetime(2026, 11, 7), resumed_from=None)
