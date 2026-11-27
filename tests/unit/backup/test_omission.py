"""Which sealed days the hot copy leaves out (R-E4-01; 02 §13.1, point 4, E4c-2, point 4;
E4C2-02): only those whose copy is verified in the bucket and was seen by a listing that
is the last attempt, finished well, is less than an hour old and authorised no earlier hot
copy. What needs no database; the dump itself is tested in tests/db/test_backup.py."""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from kbot.backup import local
from kbot.upload import state as upload_state
from tests.unit.upload.support import hot_name, partitions, publish

DAY = date(2026, 11, 27)
COPY = "dia-2026-11-27"


def at(hour: int, minute: int) -> datetime:
    return datetime(2026, 12, 1, hour, minute, tzinfo=UTC)


def listing(identifier: str, started: datetime, ok: bool = True) -> dict[str, Any]:
    return {
        "id": identifier,
        "empezado": started.isoformat(),
        "terminado": started.isoformat() if ok else None,
        "ok": ok,
    }


def verified(seen: str | None, digest: str | None = None) -> dict[str, Any]:
    return {
        "clase": "dia",
        "objeto": f"dia/{COPY}/volcado.age",
        "sha256": "1" * 64,
        "objeto_manifiesto": f"dia/{COPY}/manifiesto.json",
        "sha256_manifiesto": "2" * 64,
        "verificada": at(9, 56).isoformat(),
        "visto": seen,
        "particiones": digest or upload_state.partitions_digest(partitions("2026-11-27")),
    }


def state(last: dict[str, Any], copies: dict[str, Any]) -> dict[str, Any]:
    return upload_state.empty() | {"listado": last, "verificadas": copies}


def hot_copy_used(directory: Path, moment: datetime, listing_id: str | None) -> None:
    """What kbot-backup leaves after a hot copy: published, with its listing."""
    publish(directory, hot_name(moment), b"hot", listado=listing_id)


def test_a_verified_day_seen_by_a_fresh_listing_is_left_out_with_its_identity(
    tmp_path: Path,
) -> None:
    publish(tmp_path, COPY, b"day")
    found = local.omissions(
        tmp_path, state(listing("L1", at(10, 55)), {COPY: verified("L1")}), at(11, 40)
    )
    assert found == (
        "L1",
        {
            DAY: {
                "copia": COPY,
                "objeto": f"dia/{COPY}/volcado.age",
                "sha256": "1" * 64,
                "objeto_manifiesto": f"dia/{COPY}/manifiesto.json",
                "sha256_manifiesto": "2" * 64,
                "verificada": at(9, 56).isoformat(),
            }
        },
    )


def test_the_case_of_the_review_and_its_variants(tmp_path: Path) -> None:
    """E4C2-02: a good listing at 10:55 and the copy deleted at 10:55:01. The copy of 11:40
    leaves the day out with that listing; the listing of 11:55 fails, does not happen, does
    not end, or sees the deletion: in every case the copy of 12:40 carries the day."""
    publish(tmp_path, COPY, b"day")
    good = state(listing("L1", at(10, 55)), {COPY: verified("L1")})
    listing_id, omitted = local.claim_omissions(tmp_path, good, at(11, 40))
    assert listing_id == "L1" and set(omitted) == {DAY}
    hot_copy_used(tmp_path, at(11, 40), listing_id)
    variants = {
        "failed": state(listing("L2", at(11, 55), ok=False), {COPY: verified("L1")}),
        "never made": good,
        "not ended": state(
            {"id": "L2", "empezado": at(11, 55).isoformat(), "terminado": None, "ok": False},
            {COPY: verified("L1")},
        ),
        "saw the deletion": state(listing("L2", at(11, 55)), {}),
    }
    for name, after in variants.items():
        listing_id, omitted = local.omissions(tmp_path, after, at(12, 40))
        assert omitted == {}, name
    # Each of the three reasons alone is enough.
    assert local.omissions(tmp_path, good, at(11, 50)) == (None, {})  # already used
    fresh_but_failed = state(listing("L2", at(12, 30), ok=False), {COPY: verified("L1")})
    assert local.omissions(tmp_path, fresh_but_failed, at(12, 40)) == (None, {})
    unused_but_old = state(listing("L9", at(10, 55)), {COPY: verified("L9")})
    assert local.omissions(tmp_path, unused_but_old, at(11, 55)) == (None, {})


def test_a_day_not_seen_by_the_last_listing_or_with_other_data_is_carried(tmp_path: Path) -> None:
    publish(tmp_path, COPY, b"day")
    last = listing("L2", at(11, 55))
    assert local.omissions(tmp_path, state(last, {COPY: verified("L1")}), at(12, 40)) == ("L2", {})
    assert local.omissions(tmp_path, state(last, {COPY: verified(None)}), at(12, 40)) == ("L2", {})
    other = verified("L2", digest="f" * 64)  # the bucket holds other partitions
    assert local.omissions(tmp_path, state(last, {COPY: other}), at(12, 40)) == ("L2", {})


def test_without_a_usable_state_nothing_is_left_out(tmp_path: Path) -> None:
    publish(tmp_path, COPY, b"day")
    assert local.omissions(tmp_path, None, at(12, 40)) == (None, {})
    assert local.omissions(tmp_path, upload_state.empty(), at(12, 40)) == (None, {})
    broken = state({"id": "L1", "ok": True}, {COPY: verified("L1")})  # no start
    assert local.omissions(tmp_path, broken, at(12, 40)) == (None, {})
    future = state(listing("L1", at(13, 55)), {COPY: verified("L1")})
    assert local.omissions(tmp_path, future, at(12, 40)) == (None, {})
    # The state file kbot-upload writes, read as kbot-backup reads it.
    path = tmp_path / "estado.json"
    path.write_text("{not json", encoding="utf-8")
    assert upload_state.load(path) is None


def test_a_day_without_its_local_copy_is_never_left_out(tmp_path: Path) -> None:
    """The manifest takes the rows and fingerprints of the day from its local copy."""
    found = local.omissions(
        tmp_path, state(listing("L1", at(10, 55)), {COPY: verified("L1")}), at(11, 40)
    )
    assert found == ("L1", {})
    assert timedelta(hours=1) == upload_state.FRESH


def test_a_listing_authorises_one_hot_copy_even_with_full_copies_restarts_and_retention(
    tmp_path: Path,
) -> None:
    """E4C2-04: with a listing L still fresh, the first copy relies on it and leaves the day
    out; the second carries everything; the third, after a restart of the process and the
    local retention of the first, must not rely on L again. Only the first may."""
    publish(tmp_path, COPY, b"day")
    good = state(listing("L", at(10, 55)), {COPY: verified("L")})
    first = local.claim_omissions(tmp_path, good, at(11, 0))
    assert first[0] == "L" and set(first[1]) == {DAY}
    hot_copy_used(tmp_path, at(11, 0), "L")
    assert local.claim_omissions(tmp_path, good, at(11, 10)) == (None, {})
    hot_copy_used(tmp_path, at(11, 10), None)
    shutil.rmtree(tmp_path / hot_name(at(11, 0)))  # the local retention, and a new process
    for minute in (20, 30, 50):
        assert local.claim_omissions(tmp_path, good, at(11, minute)) == (None, {})
    assert local.consumed_listing(tmp_path) == (True, "L")
    # A newer listing authorises one copy again.
    newer = state(listing("M", at(11, 55)), {COPY: verified("M")})
    assert set(local.claim_omissions(tmp_path, newer, at(12, 0))[1]) == {DAY}
    assert local.claim_omissions(tmp_path, newer, at(12, 5)) == (None, {})


def test_an_unreadable_record_of_the_consumed_listing_authorises_nothing(tmp_path: Path) -> None:
    publish(tmp_path, COPY, b"day")
    (tmp_path / local.CONSUMED_LISTING).write_text("{roto", encoding="utf-8")
    good = state(listing("L", at(10, 55)), {COPY: verified("L")})
    assert local.consumed_listing(tmp_path) == (False, None)
    assert local.claim_omissions(tmp_path, good, at(11, 0)) == (None, {})
