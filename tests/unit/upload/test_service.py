"""The upload cycle against an Object Storage in memory with the versioning and the
permissions of the policy (02 §13.1, point 4, E4c-2, points 3 and 7)."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from kbot.backup import local
from kbot.upload import retention, service
from kbot.upload import state as upload_state
from kbot.upload.budget import RequestBudget
from kbot.upload.store import CountedStore
from tests.unit.upload.support import (
    NAMESPACE,
    START,
    Clock,
    FakeObjectStorage,
    counter,
    hot_name,
    publish,
)


class Setup:
    def __init__(self, tmp_path: Path, used: dict[str, int] | None = None, **fake: object) -> None:
        self.clock = Clock()
        self.backups = tmp_path / "backups"
        self.globals_dir = self.backups / "globales"
        self.uploads = tmp_path / "subidas"
        for directory in (self.backups, self.globals_dir, self.uploads):
            directory.mkdir(parents=True, exist_ok=True)
        self.storage = FakeObjectStorage(clock=self.clock, **fake)  # type: ignore[arg-type]
        counter(self.uploads, used)
        self.budget = RequestBudget(self.uploads / "peticiones.json", clock=self.clock)

    def uploader(self, hot_every_hours: int = 1) -> service.Uploader:
        return service.Uploader(
            CountedStore(self.storage, self.budget),
            backups=self.backups,
            globals_dir=self.globals_dir,
            directory=self.uploads,
            clock=self.clock,
            hot_every_hours=hot_every_hours,
        )

    def run(self, hot_every_hours: int = 1) -> service.Report:
        return self.uploader(hot_every_hours).run()

    def state(self) -> dict[str, object]:
        loaded = upload_state.load(self.uploads / upload_state.STATE)
        assert loaded is not None
        return loaded


def test_a_cycle_uploads_reads_again_and_verifies_each_copy(tmp_path: Path) -> None:
    s = Setup(tmp_path)
    day = publish(s.backups, "dia-2026-11-27", b"day")
    hot = publish(s.backups, hot_name(START - timedelta(minutes=15)), b"hot")
    globals_copy = publish(s.globals_dir, "globales-20261129T034000Z", b"roles")
    report = s.run()
    assert report.errors == []
    assert report.uploaded == [day.path.name, hot.path.name, globals_copy.path.name]
    assert s.storage.current_names() == sorted(
        name for copy in report.uploaded for name in service.objects_of(copy)
    )
    assert s.storage.bytes_of("dia/dia-2026-11-27/volcado.age") == b"day"
    state = s.state()
    entry = state["verificadas"]["dia-2026-11-27"]  # type: ignore[index]
    assert entry["sha256"] == day.manifest["volcado"]["sha256_cifrado"]
    assert entry["sha256_manifiesto"] == local.sha256_file(day.path / local.MANIFEST)
    assert entry["particiones"] == upload_state.partitions_digest(day.manifest["particiones"])
    assert entry["visto"] is None  # not in this cycle's listing (E4C2-02)
    listing = state["listado"]
    assert listing["ok"] is True and listing["terminado"]  # type: ignore[index]
    assert state["ultima_subida_completa"] == START.isoformat()
    assert state["ultima_caliente_fuera"] == hot.path.name
    # Each object once, in one PutObject, and read once again (E4C2-01).
    assert sorted(s.storage.sent("PutObject")) == s.storage.current_names()
    assert sorted(s.storage.sent("GetObject")) == s.storage.current_names()
    # The next listing sees them.
    s.clock.advance(hours=1)
    assert s.run().uploaded == []
    entry = s.state()["verificadas"]["dia-2026-11-27"]  # type: ignore[index]
    assert entry["visto"] == s.state()["listado"]["id"]  # type: ignore[index]


def test_a_dump_above_128_mib_goes_in_one_put_and_never_in_parts(tmp_path: Path) -> None:
    """E4C2-01: the multipart calls need OBJECT_OVERWRITE, which the policy does not give,
    even for a new name; one PutObject takes up to 50 GiB."""
    s = Setup(tmp_path)
    big = bytes(129 * 1024 * 1024)
    publish(s.backups, "dia-2026-11-27", big)
    assert s.run().errors == []
    assert s.storage.sent("PutObject").count("dia/dia-2026-11-27/volcado.age") == 1
    for call in ("CreateMultipartUpload", "UploadPart", "CommitMultipartUpload"):
        assert s.storage.sent(call) == []
    with pytest.raises(Exception, match="OBJECT_OVERWRITE"):
        s.storage.create_multipart_upload("dia/x/volcado.age")
    assert s.storage.bytes_of("dia/dia-2026-11-27/volcado.age") == big


def test_a_name_in_use_is_never_overwritten(tmp_path: Path) -> None:
    s = Setup(tmp_path)
    copy = publish(s.backups, "dia-2026-11-27", b"day")
    s.storage.tamper("dia/dia-2026-11-27/volcado.age", b"someone else")
    report = s.run()
    assert report.errors == ["dia-2026-11-27: the copy in the bucket differs from this one"]
    assert s.storage.bytes_of("dia/dia-2026-11-27/volcado.age") == b"someone else"
    assert "dia/dia-2026-11-27/volcado.age" not in s.storage.sent("PutObject")
    assert "dia-2026-11-27" not in s.state()["verificadas"]  # type: ignore[operator]
    assert copy.path.is_dir()


def test_a_lost_answer_and_its_412_are_settled_by_reading_again(tmp_path: Path) -> None:
    s = Setup(tmp_path, lose_response={"dia/dia-2026-11-27/volcado.age"})
    publish(s.backups, "dia-2026-11-27", b"day")
    assert s.run().errors == []
    assert s.storage.sent("PutObject").count("dia/dia-2026-11-27/volcado.age") == 2
    assert "dia-2026-11-27" in s.state()["verificadas"]  # type: ignore[operator]


def test_a_copy_changed_in_the_bucket_is_not_verified(tmp_path: Path) -> None:
    s = Setup(tmp_path, corrupt={"caliente/" + hot_name(START) + "/volcado.age"})
    publish(s.backups, hot_name(START), b"hot")
    report = s.run()
    assert report.errors == [f"{hot_name(START)}: the copy in the bucket differs from this one"]
    assert s.state()["verificadas"] == {}
    assert s.state()["ultima_subida_completa"] is None


def test_a_sealed_day_made_again_keeps_the_copy_in_the_bucket_with_the_same_data(
    tmp_path: Path,
) -> None:
    """A sealed-day copy made again locally has another encryption under the same name. The
    copy in the bucket stays: it is verified by its own manifest, which vouches for its
    dump and has the same partitions, rows and fingerprints."""
    s = Setup(tmp_path)
    first = publish(s.backups, "dia-2026-11-27", b"first")
    assert s.run().errors == []
    in_bucket = s.state()["verificadas"]["dia-2026-11-27"]  # type: ignore[index]
    shutil.rmtree(first.path)
    second = publish(s.backups, "dia-2026-11-27", b"second")
    state = s.state()
    state["verificadas"] = {}  # the state was lost
    (s.uploads / upload_state.STATE).write_text(json.dumps(state), encoding="utf-8")
    s.clock.advance(hours=1)
    assert s.run().errors == []
    again = s.state()["verificadas"]["dia-2026-11-27"]  # type: ignore[index]
    assert again["sha256"] == in_bucket["sha256"] != second.manifest["volcado"]["sha256_cifrado"]
    assert again["particiones"] == upload_state.partitions_digest(second.manifest["particiones"])
    assert s.storage.bytes_of("dia/dia-2026-11-27/volcado.age") == b"first"
    assert s.storage.sent("PutObject").count("dia/dia-2026-11-27/volcado.age") == 1


def test_nothing_is_uploaded_above_19_gb_and_the_error_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = Setup(tmp_path)
    publish(s.backups, "dia-2026-11-27", b"day")
    s.storage.tamper("previa/previa-20261101T120000Z/volcado.age", b"x" * 100)
    monkeypatch.setattr(service, "BUDGET_BYTES", 100)
    report = s.run()
    assert report.errors == ["dia-2026-11-27: it does not fit in the 19 GB of the budget"]
    assert s.storage.sent("PutObject") == []
    assert s.state()["ultima_subida_completa"] is None
    assert s.state()["ocupado"] == 100


def test_above_40000_requests_only_the_sealed_day_copies_go(tmp_path: Path) -> None:
    s = Setup(tmp_path, used={(START.date() - timedelta(days=1)).isoformat(): 40_000})
    publish(s.backups, "dia-2026-11-27", b"day")
    publish(s.backups, hot_name(START), b"hot")
    report = s.run()
    assert report.uploaded == ["dia-2026-11-27"]
    assert report.errors == [
        "more than 40000 requests in 32 days: only the sealed-day copies are uploaded"
    ]
    assert s.state()["ultima_subida_completa"] is None


def test_without_versioning_nothing_is_uploaded(tmp_path: Path) -> None:
    s = Setup(tmp_path, versioning=False)
    publish(s.backups, "dia-2026-11-27", b"day")
    report = s.run()
    assert report.errors == ["the versioning of the bucket is not on: nothing is uploaded"]
    assert s.storage.sent("ListObjectVersions") == [] and s.storage.sent("PutObject") == []


def test_the_listing_attempt_is_written_before_its_request(tmp_path: Path) -> None:
    """E4C2-02: an attempt that fails leaves the state saying so, and authorises nothing."""
    s = Setup(tmp_path)
    from kbot.upload.store import Transient

    s.storage.fail["ListObjectVersions"] = [Transient("timeout")] * 3
    report = s.run()
    assert report.errors and "Transient" in report.errors[0]
    listing = s.state()["listado"]
    assert listing["ok"] is False and listing["terminado"] is None  # type: ignore[index]
    assert upload_state.usable_listing(s.state(), START) is None


def test_the_hot_copies_follow_the_cadence(tmp_path: Path) -> None:
    """Every 6 hours: the owner's decision of 26 Nov 2026 (an amendment of D25)."""
    assert service.HOT_UPLOAD_HOURS == 6
    s = Setup(tmp_path)
    for hour in (0, 5, 6, 7):
        publish(s.backups, hot_name(START.replace(hour=hour, minute=40)), b"h%d" % hour)
    s.clock.now = START.replace(hour=7)
    assert s.run(hot_every_hours=6).uploaded == [hot_name(START.replace(hour=6, minute=40))]
    s.clock.advance(hours=1)
    assert s.run(hot_every_hours=6).uploaded == []  # 00:40 is older than what is out


def test_the_retention_of_d25_over_three_days_of_hourly_cycles(tmp_path: Path) -> None:
    s = Setup(tmp_path)
    first = datetime(2026, 11, 30, 0, 40, tzinfo=UTC)  # a Monday
    made = []
    for hour in range(72):
        moment = first + timedelta(hours=hour)
        made.append((hot_name(moment), moment))
        publish(s.backups, hot_name(moment), b"%d" % hour)
        for old in local.hot_to_delete(sorted(p.name for p in s.backups.glob("caliente-*"))):
            for item in (s.backups / old).iterdir():
                item.unlink()
            (s.backups / old).rmdir()
        s.clock.now = moment + timedelta(minutes=15)
        assert s.run().errors == []
    kept = {n for n in s.storage.current_names() if n.startswith("caliente/")}
    expected = retention.keep_hot(made)
    assert kept == {name for copy in expected for name in service.objects_of(copy)}
    assert expected == {made[0][0], made[24][0], made[48][0], made[-3][0], made[-2][0], made[-1][0]}
    # Every other one left with DeleteObject: a delete marker, its data kept as a version.
    deleted = [n for n in s.storage.sent("DeleteObject") if n.endswith(local.DUMP)]
    assert len(deleted) == 72 - len(expected)
    assert all(s.storage.objects[n][-2].data is not None for n in deleted)


def test_the_retention_keeps_the_first_of_each_day_and_week_over_30_days() -> None:
    first = datetime(2026, 11, 2, 0, 40, tzinfo=UTC)  # a Monday
    copies = [
        (hot_name(first + timedelta(hours=h)), first + timedelta(hours=h)) for h in range(720)
    ]
    keep = retention.keep_hot(copies)
    days = [copies[24 * d][0] for d in range(23, 30)]  # the first of the last 7 days
    weeks = [copies[24 * 21][0], copies[24 * 28][0]]  # the Mondays of the last 2 weeks
    assert keep == set(days) | set(weeks) | {c for c, _ in copies[-3:]}
    assert len(keep) == 11  # the Monday of the last week is daily and weekly at once


def test_a_sealed_day_copy_goes_only_when_the_copies_that_rely_on_it_can_do_without_it() -> None:
    """The acceptance case of the residual of R-E4-01: a retained hot copy H fixes S and
    G1. With G2 eligible and G1 not, S stays until H expires; with G1 verified, it goes."""
    day = date(2026, 11, 27)
    h = retention.Dependency(copy="caliente-H", day_copy="dia-2026-11-27", generation="G1")
    may_go = retention.day_copy_may_go
    assert not may_go("dia-2026-11-27", day, [h], {"caliente-H"}, set(), {"G1"})
    assert not may_go("dia-2026-11-27", day, [h], {"caliente-H"}, {day}, {"G2"})
    assert may_go("dia-2026-11-27", day, [h], {"caliente-H"}, {day}, {"G1"})
    assert may_go("dia-2026-11-27", day, [h], set(), {day}, {"G2"})  # H expired
    no_generation = retention.Dependency("caliente-H", "dia-2026-11-27", None)
    assert not may_go("dia-2026-11-27", day, [no_generation], {"caliente-H"}, {day}, {"G1"})


def test_the_service_applies_the_residual_with_an_index_it_rebuilds(tmp_path: Path) -> None:
    """The same case through the service: H left the day out relying on S and fixing G1.
    The day becomes eligible with G2 verified outside, and S stays; the index of the
    dependencies is lost and rebuilt from the manifest of H in the bucket; once G1 is
    verified outside, S leaves the bucket."""
    s = Setup(tmp_path)
    day_copy = publish(s.backups, "dia-2026-11-27", b"day")
    dependency = {
        "dia": "2026-11-27",
        "copia_de_dia": {"copia": day_copy.path.name},
        "generacion": "G1",
    }
    h = hot_name(START)
    publish(s.backups, h, b"H", dias_excluidos=[dependency])
    assert s.run().errors == []
    assert "dia/dia-2026-11-27/volcado.age" in s.storage.current_names()
    index = json.loads((s.uploads / upload_state.DEPENDENCIES).read_text(encoding="utf-8"))
    assert index == {h: [{"copy": h, "day_copy": "dia-2026-11-27", "generation": "G1"}]}

    def cycle(hours: int, verified_outside: str) -> None:
        moment = START + timedelta(hours=hours)
        knowledge = {
            "dias_elegibles": ["2026-11-27"],
            "generaciones_verificadas_fuera": [verified_outside],
        }
        publish(s.backups, hot_name(moment), b"H%d" % hours, archivo=knowledge)
        s.clock.now = moment
        assert s.run().errors == []

    cycle(1, "G2")  # eligible through another generation: H still needs S
    assert "dia/dia-2026-11-27/volcado.age" in s.storage.current_names()
    (s.uploads / upload_state.DEPENDENCIES).unlink()
    cycle(2, "G1")
    assert f"caliente/{h}/{local.MANIFEST}" in s.storage.sent("GetObject")[-3:]
    assert "dia/dia-2026-11-27/volcado.age" not in s.storage.current_names()
    assert "dia-2026-11-27" not in s.state()["verificadas"]  # type: ignore[operator]
    cycle(3, "G1")  # the local copy of an eligible day is never uploaded again
    assert s.storage.sent("PutObject").count("dia/dia-2026-11-27/volcado.age") == 1


def test_an_object_the_owner_deletes_is_uploaded_again_and_never_verified_meanwhile(
    tmp_path: Path,
) -> None:
    s = Setup(tmp_path)
    publish(s.backups, "dia-2026-11-27", b"day")
    s.run()
    s.storage.remove("dia/dia-2026-11-27/volcado.age")
    s.clock.advance(hours=1)
    report = s.run()
    assert report.uploaded == ["dia-2026-11-27"]
    entry = s.state()["verificadas"]["dia-2026-11-27"]  # type: ignore[index]
    assert entry["visto"] is None  # not seen by this listing: nothing relies on it yet
    assert s.state()["espacio"] == NAMESPACE
