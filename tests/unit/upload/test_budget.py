"""The cap of requests and the durable reservation before each one (02 §13.1, point 4,
E4c-2, points 5 and 7; E4C2-03 and its residual)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from kbot.upload import durable
from kbot.upload.budget import CAP, WINDOW_DAYS, BudgetExhausted, CounterUnreadable, RequestBudget
from kbot.upload.store import CountedStore, Transient
from tests.unit.upload.support import START, Clock, FakeObjectStorage, RecordingOps, counter

TODAY = START.date()


def used(days_ago: int, count: int) -> dict[str, int]:
    return {(TODAY - timedelta(days=days_ago)).isoformat(): count}


def setup(
    tmp_path: Path, already: dict[str, int], fail_at: str | None = None, page_size: int = 1000
) -> tuple[FakeObjectStorage, CountedStore, list[tuple[str, str]]]:
    log: list[tuple[str, str]] = []
    clock = Clock()
    storage = FakeObjectStorage(clock=clock, log=log, page_size=page_size)
    path = counter(tmp_path, already)
    budget = RequestBudget(path, clock=clock, ops=RecordingOps(log, fail_at))
    return storage, CountedStore(storage, budget), log


def test_each_request_goes_out_only_after_its_reservation_is_durable(tmp_path: Path) -> None:
    """Residual of E4C2-03: temporary, fsync of the file, rename, fsync of the directory,
    and only then the request."""
    _, store, log = setup(tmp_path, {})
    assert store.namespace() == "frxyz"
    assert log == [
        ("write", ".peticiones.json.tmp"),
        ("fsync_file", ".peticiones.json.tmp"),
        ("rename", "peticiones.json"),
        ("fsync_directory", tmp_path.name),
        ("GetNamespace", ""),
    ]
    stored = json.loads((tmp_path / "peticiones.json").read_text(encoding="utf-8"))
    assert stored["dias"] == {TODAY.isoformat(): 1}


@pytest.mark.parametrize("step", ["write", "fsync_file", "rename", "fsync_directory"])
def test_a_failed_step_of_the_reservation_sends_nothing(tmp_path: Path, step: str) -> None:
    storage, store, log = setup(tmp_path, {}, fail_at=step)
    with pytest.raises(durable.PersistenceError, match=f"{step} failed"):
        store.namespace()
    assert storage.sent("GetNamespace") == []
    assert log[-1][0] == step  # nothing after the step that failed


@pytest.mark.parametrize("remaining", [0, 1, 2, 3])
def test_a_listing_of_two_pages_with_a_retry_never_passes_the_cap(
    tmp_path: Path, remaining: int
) -> None:
    """E4C2-03: with 0, 1, 2 or 3 requests left, a listing of two pages whose first page
    fails once goes as far as the budget allows, and no request beyond it is ever sent."""
    storage, store, _ = setup(tmp_path, used(3, CAP - remaining), page_size=1)
    for name in ("a", "b"):
        storage.objects[name] = []
        storage._add(name, b"x")
    storage.fail["ListObjectVersions"] = [Transient("timeout")]
    if remaining >= 3:
        assert [v.name for v in store.list_all("frxyz")] == ["a", "b"]
    else:
        with pytest.raises(BudgetExhausted):
            store.list_all("frxyz")
    assert len(storage.sent("ListObjectVersions")) == min(remaining, 3)
    assert store.budget.used() == CAP - remaining + min(remaining, 3) <= CAP


def test_a_new_process_reads_the_counter_and_goes_on_from_it(tmp_path: Path) -> None:
    storage, store, log = setup(tmp_path, used(0, CAP - 2))
    store.namespace()
    clock = Clock()
    restarted = CountedStore(
        storage, RequestBudget(tmp_path / "peticiones.json", clock=clock, ops=RecordingOps(log))
    )
    assert restarted.budget.remaining() == 1
    restarted.namespace()
    with pytest.raises(BudgetExhausted):
        restarted.namespace()
    assert storage.sent("GetNamespace") == ["", ""]


def test_a_reservation_counts_even_when_its_request_fails(tmp_path: Path) -> None:
    storage, store, _ = setup(tmp_path, {})
    storage.fail["GetNamespace"] = [Transient("reset")] * 3
    with pytest.raises(Transient):
        store.namespace()
    assert len(storage.sent("GetNamespace")) == 3  # the first attempt and two retries
    assert store.budget.used() == 3


def test_the_window_is_the_last_32_utc_days(tmp_path: Path) -> None:
    old = used(WINDOW_DAYS, 30_000) | used(WINDOW_DAYS - 1, 10_000) | used(0, 5_000)
    _, store, _ = setup(tmp_path, old)
    assert store.budget.used() == 15_000
    assert store.budget.remaining() == CAP - 15_000
    store.namespace()
    kept = json.loads((tmp_path / "peticiones.json").read_text(encoding="utf-8"))["dias"]
    assert set(kept) == {
        (TODAY - timedelta(days=WINDOW_DAYS - 1)).isoformat(),
        TODAY.isoformat(),
    }


@pytest.mark.parametrize("content", [None, "{", '{"formato": "otro", "dias": {}}', "[]"])
def test_without_a_readable_counter_nothing_is_sent(tmp_path: Path, content: str | None) -> None:
    storage = FakeObjectStorage()
    path = tmp_path / "peticiones.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    store = CountedStore(storage, RequestBudget(path, clock=Clock()))
    with pytest.raises(CounterUnreadable):
        store.namespace()
    assert storage.log == []


def test_the_saving_threshold_and_the_coverage_of_a_whole_operation(tmp_path: Path) -> None:
    _, store, _ = setup(tmp_path, used(1, 40_000))
    assert store.budget.saving()
    _, store, _ = setup(tmp_path, used(1, CAP - 3))
    assert not store.budget.covers(4) and store.budget.covers(3)
