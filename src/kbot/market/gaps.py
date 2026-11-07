"""Data gaps of one channel and instrument (02 §3.5, §3.7 and §3.8; phase 2 acceptance A5).

A gap opens when the connection stops being healthy and is written to ``data_gaps`` once it
closes, with its cause. When the process restarts after a stop, the time since the last
slot it recorded is a gap with cause ``proceso_detenido``; the depth channel resumes at the
same moment, so every slot of that period is closed as ``desconectado``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from kbot.market.timeutil import as_utc


class GapCause(StrEnum):
    DESCONEXION = "desconexion"
    ROTACION_PLANIFICADA = "rotacion_planificada"
    SIN_PONG = "sin_pong"
    SUSCRIPCION_RECHAZADA = "suscripcion_rechazada"
    PROCESO_DETENIDO = "proceso_detenido"
    MENSAJE_INVALIDO = "mensaje_invalido"


@dataclass(frozen=True)
class DataGap:
    market: str
    channel: str
    symbol: str
    start_at: datetime
    end_at: datetime
    cause: GapCause
    detail: str | None


def cause_from_state_detail(detail: str) -> GapCause:
    """The cause of a ``disconnected`` event of PublicStream, from its detail text."""
    if detail.startswith("SessionRotation"):
        return GapCause.ROTACION_PLANIFICADA
    if "no pong" in detail:
        return GapCause.SIN_PONG
    if "subscription refused" in detail:
        return GapCause.SUSCRIPCION_RECHAZADA
    if "invalid JSON" in detail or "must be JSON objects" in detail:
        return GapCause.MENSAJE_INVALIDO
    return GapCause.DESCONEXION


class GapTracker:
    """Open and closed gaps of one (market, channel, symbol)."""

    def __init__(
        self,
        market: str,
        channel: str,
        symbol: str,
        *,
        started_at: datetime,
        resumed_from: datetime | None,
    ) -> None:
        """``resumed_from`` is where the previous run stopped recording (None on a first run).

        Until the first subscription is confirmed nothing is recorded, so a gap is open
        from the resume point (after a stop) or from the start (first run).
        """
        started_at = as_utc(started_at, "started_at")
        self.market = market
        self.channel = channel
        self.symbol = symbol
        if resumed_from is not None and as_utc(resumed_from, "resumed_from") < started_at:
            self._open: tuple[datetime, GapCause, str | None] | None = (
                as_utc(resumed_from),
                GapCause.PROCESO_DETENIDO,
                None,
            )
        else:
            self._open = (started_at, GapCause.DESCONEXION, "first connection")

    @property
    def is_open(self) -> bool:
        return self._open is not None

    def down(self, at: datetime, cause: GapCause, detail: str | None = None) -> None:
        if self._open is None:
            self._open = (as_utc(at, "at"), cause, detail)

    def up(self, at: datetime) -> DataGap | None:
        """The subscription is confirmed: close the open gap, if any, and return it."""
        at = as_utc(at, "at")
        if self._open is None:
            return None
        start, cause, detail = self._open
        self._open = None
        if at <= start:
            return None
        return DataGap(self.market, self.channel, self.symbol, start, at, cause, detail)
