"""The retention of the bucket (D25; 02 §13.1, point 4, E4c-2, points 3.5 and 3.6).

Only verified copies count. Of the hot copies stay the newest hourly ones, the first of
each of the last UTC days and the first of each of the last ISO weeks (from Monday, UTC);
a copy in several classes stays once. Of the copies taken before an update and of the
global objects, the last two.

A sealed-day copy *S* leaves the bucket only under the residual of R-E4-01: its day must
be ``elegible_para_exclusion_backup``, and every retained copy whose manifest left the day
out relying on *S* must be restorable without it, with the identities of its own
manifest: the generation that manifest fixes for the day must be ``verificado_fuera``.
Otherwise *S* stays until that copy leaves the retention.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

HOURLY = 3
DAILY = 7
WEEKLY = 2
PREVIA = 2
GLOBALS = 2


@dataclass(frozen=True)
class Dependency:
    """A retained copy left out a day relying on the copy of that day in the bucket."""

    copy: str  # the copy that depends
    day_copy: str  # the sealed-day copy it relies on
    generation: str | None  # the generation its manifest fixes for the day


def copy_time(name: str, prefix: str) -> datetime:
    """The moment in the name of a copy: ``<prefix>YYYYMMDDTHHMMSSZ``."""
    return datetime.strptime(name[len(prefix) :], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def keep_hot(
    copies: Sequence[tuple[str, datetime]],
    hourly: int = HOURLY,
    daily: int = DAILY,
    weekly: int = WEEKLY,
) -> set[str]:
    """The hot copies that stay, of the verified ones given with their moments."""
    ordered = sorted(copies, key=lambda item: (item[1], item[0]))
    keep = {name for name, _ in ordered[-hourly:]} if hourly else set()
    first_of_day: dict[date, str] = {}
    first_of_week: dict[tuple[int, int], str] = {}
    for name, made in ordered:
        first_of_day.setdefault(made.astimezone(UTC).date(), name)
        iso = made.astimezone(UTC).isocalendar()
        first_of_week.setdefault((iso.year, iso.week), name)
    if daily:
        keep.update(first_of_day[day] for day in sorted(first_of_day)[-daily:])
    if weekly:
        keep.update(first_of_week[week] for week in sorted(first_of_week)[-weekly:])
    return keep


def keep_last(names: Iterable[str], count: int) -> set[str]:
    """The last ``count`` copies of a class whose names sort by time."""
    ordered = sorted(names)
    return set(ordered[-count:]) if count else set()


def day_copy_may_go(
    day_copy: str,
    day: date,
    dependencies: Iterable[Dependency],
    retained: set[str],
    eligible_days: set[date],
    verified_generations: set[str],
) -> bool:
    """Whether the sealed-day copy of ``day`` may leave the bucket (residual of R-E4-01)."""
    if day not in eligible_days:
        return False
    for dependency in dependencies:
        if dependency.day_copy != day_copy or dependency.copy not in retained:
            continue
        if dependency.generation is None or dependency.generation not in verified_generations:
            return False
    return True


def dependencies_of(manifest: Mapping[str, object]) -> list[Dependency]:
    """The sealed-day copies a hot copy relies on, from its manifest."""
    found = []
    for item in manifest.get("dias_excluidos", []) or []:  # type: ignore[union-attr]
        if not isinstance(item, dict):
            raise ValueError("an omitted day that is not an object")
        if item.get("copia_de_dia") is None:
            continue  # left out by its generation: no sealed-day copy is needed
        generation = item.get("generacion")
        found.append(
            Dependency(
                copy=str(manifest["nombre"]),
                day_copy=str(item["copia_de_dia"]["copia"]),
                generation=None if generation is None else str(generation),
            )
        )
    return found
