"""The budget of the Object Storage bucket, simulated hour by hour (02 §13.1, point 4, E4c-2,
point 6; E4-D07 and R-E4-02).

    python -m tools.simular_copias [--dias 120] [--subida-caliente-cada 1] ...

The model follows the approved design: every hour at :40 kbot-backup makes the hot copy,
and at :55 kbot-upload lists the bucket, uploads what is missing (the sealed-day copies,
the newest hot copy, the global objects and the pre-update copies), applies the D25
retention and deletes what leaves it. A deleted object stays as a previous version until
the lifecycle rule removes it: one day after its deletion plus a declared delay, because
Oracle gives no deadline for the rule. Nothing has a fixed bound: the result is the
largest size of the bucket before each upload, the first day an upload no longer fits in
the 19 GB of the budget, and the largest count of requests in any 32 UTC days.

The hot copy carries the small tables, the days not sealed yet (a day is sealed at 00:20
UTC four days after it starts) and every sealed day whose copy is not verified in the
bucket. Until stage E4c-3 no day becomes ``elegible_para_exclusion_backup``, so no
sealed-day copy is ever deleted, and every pre-update copy, which is self-contained,
carries every day of the database.

The sizes by default were measured on the server on 26 Nov 2026: a sealed day with data,
63 MB encrypted; the hot copy, 580 MB with six sealed days and 3.7 open ones, which leaves
about 5 MB for the small tables; its manifest, 129 KB.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from kbot.upload import retention

MB = 1_000_000
GB = 1_000_000_000
BUDGET = 19 * GB
REQUEST_CAP = 45_000
REQUEST_WINDOW_DAYS = 32
SEAL_LAG_DAYS = 4  # day D is sealed at 00:20 UTC of day D + 4
PAGE = 1000  # versions per page of ListObjectVersions


@dataclass(frozen=True)
class Parameters:
    days: int = 120
    start: date = date(2026, 11, 30)
    first_day_with_data: date = date(2026, 11, 17)
    day_mb: float = 63.0
    small_mb: float = 5.0
    growth_per_30_days: float = 0.0  # declared growth of a day's size
    hot_manifest_kb: float = 130.0
    day_manifest_kb: float = 3.4
    globals_kb: float = 6.0
    hot_upload_every_hours: int = 6  # the cadence the owner chose on 26 Nov 2026
    hourly_keep: int = 3
    daily_keep: int = 7
    weekly_keep: int = 2
    previa_keep: int = 2
    globals_keep: int = 2
    previa_every_days: int = 7  # one update a week; 0 means none
    lifecycle_delay_days: float = 2.0
    eligible_after_days: int | None = None  # days after sealing; None until E4c-3


@dataclass
class _Object:
    name: str
    kind: str  # caliente, dia, globales, previa
    size: int
    made: datetime


@dataclass
class Result:
    parameters: Parameters
    daily_peak: dict[date, int] = field(default_factory=dict)
    first_refusal: datetime | None = None
    refused: int = 0
    requests_per_day: dict[date, int] = field(default_factory=dict)
    peak_breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def peak(self) -> int:
        return max(self.daily_peak.values(), default=0)

    @property
    def peak_day(self) -> date | None:
        return max(self.daily_peak, key=lambda d: self.daily_peak[d], default=None)

    def max_requests_window(self) -> int:
        days = sorted(self.requests_per_day)
        best = 0
        for i, first in enumerate(days):
            end = first + timedelta(days=REQUEST_WINDOW_DAYS)
            total = sum(self.requests_per_day[d] for d in days[i:] if d < end)
            best = max(best, total)
        return best


def _day_size(p: Parameters, day: date) -> int:
    if day < p.first_day_with_data:
        return 0  # before the recorder started: empty days
    months = (day - p.first_day_with_data).days / 30
    return int(p.day_mb * MB * (1 + p.growth_per_30_days) ** months)


def _sealed_by(moment: datetime) -> date:
    """The last sealed day at this moment (sealing runs at 00:20 UTC)."""
    today = moment.date()
    if moment.hour == 0 and moment.minute < 20:
        today -= timedelta(days=1)
    return today - timedelta(days=SEAL_LAG_DAYS)


def _eligible(p: Parameters, day: date, moment: datetime) -> bool:
    if p.eligible_after_days is None:
        return False
    sealed_at = datetime.combine(day + timedelta(days=SEAL_LAG_DAYS), datetime.min.time(), UTC)
    return moment >= sealed_at + timedelta(days=p.eligible_after_days)


def _open_part(p: Parameters, moment: datetime) -> int:
    """The days not sealed yet: whole ones, and the part of today that has passed."""
    sealed = _sealed_by(moment)
    size = 0
    day = sealed + timedelta(days=1)
    while day < moment.date():
        size += _day_size(p, day)
        day += timedelta(days=1)
    today = moment.date()
    fraction = (moment - datetime.combine(today, datetime.min.time(), UTC)).total_seconds() / 86400
    return size + int(_day_size(p, today) * fraction)


def keep_hot(names: Sequence[tuple[str, datetime]], p: Parameters) -> set[str]:
    """D25 on the verified hot copies, the very rule of the service (kbot.upload.retention)."""
    return retention.keep_hot(names, p.hourly_keep, p.daily_keep, p.weekly_keep)


def simulate(p: Parameters) -> Result:
    result = Result(p)
    current: dict[str, _Object] = {}
    previous: list[tuple[int, datetime]] = []  # size and the moment the rule removes it
    uploaded_days: set[date] = set()
    lifecycle = timedelta(days=1 + p.lifecycle_delay_days)
    start = datetime.combine(p.start, datetime.min.time(), UTC)
    last_previa: datetime | None = None

    def count(moment: datetime, requests: int) -> None:
        day = moment.date()
        result.requests_per_day[day] = result.requests_per_day.get(day, 0) + requests

    def delete(name: str, moment: datetime) -> None:
        obj = current.pop(name)
        previous.append((obj.size, moment + lifecycle))
        count(moment, 2)  # the dump and the manifest

    for hour in range(p.days * 24):
        copy_at = start + timedelta(hours=hour, minutes=40)
        upload_at = start + timedelta(hours=hour, minutes=55)
        previous[:] = [(size, gone) for size, gone in previous if gone > upload_at]
        versions = 2 * len(current) + len(previous)
        count(upload_at, 1 + max(1, math.ceil(versions / PAGE)))  # versioning + listing
        used = sum(o.size for o in current.values()) + sum(size for size, _ in previous)
        day = upload_at.date()
        result.daily_peak[day] = max(result.daily_peak.get(day, 0), used)
        if used >= result.daily_peak[day] and used >= result.peak:
            result.peak_breakdown = {
                kind: sum(o.size for o in current.values() if o.kind == kind)
                for kind in ("caliente", "dia", "previa", "globales")
            } | {"versiones_anteriores": sum(size for size, _ in previous)}

        sealed = _sealed_by(copy_at)
        wanted: list[_Object] = []
        day_ = p.first_day_with_data
        while day_ <= sealed:
            if day_ not in uploaded_days and not _eligible(p, day_, copy_at):
                manifest = int(p.day_manifest_kb * 1000)
                wanted.append(_Object(f"dia-{day_}", "dia", _day_size(p, day_) + manifest, copy_at))
            day_ += timedelta(days=1)
        if hour % p.hot_upload_every_hours == 0:
            missing = sum(
                _day_size(p, d)
                for d in _days(p.first_day_with_data, sealed)
                if d not in uploaded_days and not _eligible(p, d, copy_at)
            )
            size = int(p.small_mb * MB) + _open_part(p, copy_at) + missing
            wanted.append(
                _Object(
                    f"caliente-{copy_at:%Y%m%dT%H%M}",
                    "caliente",
                    size + int(p.hot_manifest_kb * 1000),
                    copy_at,
                )
            )
        if copy_at.weekday() == 6 and copy_at.hour == 3:  # Sundays at 03:40 UTC
            wanted.append(
                _Object(f"globales-{copy_at:%Y%m%d}", "globales", int(p.globals_kb * 1000), copy_at)
            )
        if (
            p.previa_every_days
            and copy_at.hour == 12
            and (
                last_previa is None or copy_at - last_previa >= timedelta(days=p.previa_every_days)
            )
        ):
            last_previa = copy_at
            full = sum(
                _day_size(p, d)
                for d in _days(p.first_day_with_data, sealed)
                if not _eligible(p, d, copy_at)
            )
            size = int(p.small_mb * MB) + _open_part(p, copy_at) + full
            wanted.append(
                _Object(
                    f"previa-{copy_at:%Y%m%dT%H%M}",
                    "previa",
                    size + int(p.hot_manifest_kb * 1000),
                    copy_at,
                )
            )

        for obj in wanted:
            if used + obj.size > BUDGET:
                result.refused += 1
                if result.first_refusal is None:
                    result.first_refusal = upload_at
                continue
            current[obj.name] = obj
            used += obj.size
            count(upload_at, 4)  # two PutObject and two GetObject
            if obj.kind == "dia":
                uploaded_days.add(date.fromisoformat(obj.name[len("dia-") :]))

        hot = [(o.name, o.made) for o in current.values() if o.kind == "caliente"]
        for name in sorted({n for n, _ in hot} - keep_hot(hot, p)):
            delete(name, upload_at)
        for kind, keep in (("previa", p.previa_keep), ("globales", p.globals_keep)):
            names = sorted(o.name for o in current.values() if o.kind == kind)
            for name in names[: max(0, len(names) - keep)]:
                delete(name, upload_at)
        for name in sorted(o.name for o in current.values() if o.kind == "dia"):
            if _eligible(p, date.fromisoformat(name[len("dia-") :]), upload_at):
                delete(name, upload_at)
    return result


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def summary(result: Result) -> str:
    p = result.parameters
    lines = [
        f"Simulación de {p.days} días desde el {p.start}: copia caliente al cubo cada "
        f"{p.hot_upload_every_hours} h, retención {p.hourly_keep}/{p.daily_keep}/"
        f"{p.weekly_keep} (horarias/diarias/semanales), {p.previa_keep} previas (una cada "
        f"{p.previa_every_days} días), {p.globals_keep} globales, versiones anteriores "
        f"1 + {p.lifecycle_delay_days:g} días, "
        + (
            "ningún día elegible (sin E4c-3)"
            if p.eligible_after_days is None
            else f"días elegibles {p.eligible_after_days} días tras el sellado"
        )
        + ".",
        f"- Máximo del cubo antes de una subida: {result.peak / GB:.2f} GB (el {result.peak_day}).",
        "- Al máximo: "
        + ", ".join(f"{k} {v / GB:.2f} GB" for k, v in result.peak_breakdown.items())
        + ".",
    ]
    if result.first_refusal is None:
        lines.append("- Todo cabe en los 19 GB durante toda la simulación.")
    else:
        lines.append(
            f"- Primera subida que no cabe en 19 GB: {result.first_refusal:%Y-%m-%d %H:%M} UTC "
            f"(día {(result.first_refusal.date() - p.start).days + 1}); subidas rechazadas: "
            f"{result.refused}."
        )
    lines.append(
        f"- Máximo de peticiones en {REQUEST_WINDOW_DAYS} días seguidos: "
        f"{result.max_requests_window()} (tope {REQUEST_CAP})."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    defaults = Parameters()
    parser = argparse.ArgumentParser(description="simulación del presupuesto del cubo (E4c-2)")
    parser.add_argument("--dias", type=int, default=defaults.days)
    parser.add_argument(
        "--subida-caliente-cada",
        type=int,
        default=defaults.hot_upload_every_hours,
        metavar="HORAS",
    )
    parser.add_argument("--horarias", type=int, default=defaults.hourly_keep)
    parser.add_argument("--diarias", type=int, default=defaults.daily_keep)
    parser.add_argument("--semanales", type=int, default=defaults.weekly_keep)
    parser.add_argument("--previas", type=int, default=defaults.previa_keep)
    parser.add_argument("--previa-cada", type=int, default=defaults.previa_every_days)
    parser.add_argument("--retraso-ciclo-de-vida", type=float, default=2.0, metavar="DIAS")
    parser.add_argument("--elegible-tras", type=int, default=None, metavar="DIAS")
    parser.add_argument("--crecimiento", type=float, default=0.0, help="por 30 días, 0.1 = 10 %%")
    a = parser.parse_args(argv)
    p = Parameters(
        days=a.dias,
        hot_upload_every_hours=a.subida_caliente_cada,
        hourly_keep=a.horarias,
        daily_keep=a.diarias,
        weekly_keep=a.semanales,
        previa_keep=a.previas,
        previa_every_days=a.previa_cada,
        lifecycle_delay_days=a.retraso_ciclo_de_vida,
        eligible_after_days=a.elegible_tras,
        growth_per_30_days=a.crecimiento,
    )
    print(summary(simulate(p)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
