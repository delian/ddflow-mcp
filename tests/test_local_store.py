"""LocalStore (B-uni-local-worker.3-store): documents, rings, the coalescing queue and
Retention, on real files with a fake clock."""

from __future__ import annotations

import json
import multiprocessing
import os
import threading
from pathlib import Path

import pytest

from ddflow.infra import fsio
from ddflow.infra.localstore import LocalStore, Retention, StoreUnreadable


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def store(tmp_path):
    clock = Clock()
    s = LocalStore(tmp_path / "local", clock=clock)
    s.fake = clock
    return s


# -- documents -----------------------------------------------------------------------


def test_a_missing_document_reads_as_the_default(store):
    assert store.read("x.json") is None
    assert store.read("x.json", default={"a": 1}) == {"a": 1}


def test_a_document_round_trips_and_the_directory_ignores_itself(store):
    store.write("x.json", {"a": [1, 2]})
    assert store.read("x.json") == {"a": [1, 2]}
    assert (store.root / ".gitignore").read_text().endswith("*\n")
    raw = json.loads(store.path("x.json").read_text())
    assert raw["schema"] == 1 and raw["data"] == {"a": [1, 2]} and raw["ddflow"]


@pytest.mark.parametrize("name", ["", "../x", "a/b", ".hidden"])
def test_a_store_name_is_a_plain_file_name(store, name):
    with pytest.raises(ValueError, match="plain store name"):
        store.path(name)


def test_a_file_with_no_envelope_is_a_schema_0_document_read_whole(store):
    store.root.mkdir(parents=True)
    store.path("seen.json").write_text('{"v": "0.1.3"}')
    assert store.read("seen.json") == {"v": "0.1.3"}


@pytest.mark.parametrize("text", ["", "{", "[1]", '{"schema": "1"}', '{"schema": true}'])
def test_an_unreadable_document_is_refused_not_read_as_empty(store, text):
    store.root.mkdir(parents=True)
    store.path("q.json").write_text(text)
    with pytest.raises(StoreUnreadable) as exc:
        store.read("q.json", default={})
    assert exc.value.path == store.path("q.json")


def test_a_newer_schema_is_refused_on_read_and_on_write_and_left_alone(store):
    store.root.mkdir(parents=True)
    newer = {"schema": 3, "ddflow": "9.9.9", "fmt": 7, "data": {"k": 1}}
    store.path("n.json").write_text(json.dumps(newer))
    before = store.path("n.json").read_bytes()
    with pytest.raises(fsio.NewerContent, match=r"upgrade ddflow to >= 9\.9\.9"):
        store.read("n.json", schema=2)
    with pytest.raises(fsio.NewerContent):
        store.write("n.json", {"k": 2}, schema=2)
    with pytest.raises(fsio.NewerContent):
        store.update("n.json", lambda d: d, schema=2)
    assert store.path("n.json").read_bytes() == before


def test_an_update_keeps_the_envelope_keys_it_does_not_know(store):
    store.root.mkdir(parents=True)
    doc = {"schema": 1, "ddflow": "0.0.1", "fmt": 1, "data": 1, "future": {"x": True}}
    store.path("k.json").write_text(json.dumps(doc))
    assert store.update("k.json", lambda n: n + 1) == 2
    raw = json.loads(store.path("k.json").read_text())
    assert raw["data"] == 2 and raw["future"] == {"x": True}


def test_a_crash_during_the_swap_leaves_the_old_document(store, monkeypatch):
    store.write("c.json", {"v": "old"})

    def boom(src, dst):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.write("c.json", {"v": "new"})
    monkeypatch.undo()
    assert store.read("c.json") == {"v": "old"}
    assert not [p for p in store.root.iterdir() if p.name.endswith(".tmp")]


def test_a_second_holder_of_the_lock_times_out(store):
    with store.lock("l.json"):
        with pytest.raises(fsio.LockTimeout):
            store.lock("l.json", timeout_s=0).__enter__()


def _bump(root: str, n: int) -> None:
    s = LocalStore(root)
    for _ in range(n):
        s.update("count.json", lambda c: c + 1, default=0)


def test_concurrent_updates_from_threads_and_processes_all_land(store):
    threads = [threading.Thread(target=_bump, args=(str(store.root), 10)) for _ in range(4)]
    procs = [
        multiprocessing.get_context("spawn").Process(target=_bump, args=(str(store.root), 10))
        for _ in range(3)
    ]
    for t in (*threads, *procs):
        t.start()
    for t in (*threads, *procs):
        t.join()
    assert store.read("count.json") == 70


# -- rings ---------------------------------------------------------------------------


def test_a_ring_keeps_the_last_n_records_oldest_first(store):
    for i in range(7):
        store.ring_append("r.json", i, capacity=3)
    assert store.ring("r.json") == [4, 5, 6]


def test_a_ring_below_capacity_keeps_everything_and_a_missing_ring_is_empty(store):
    assert store.ring("none.json") == []
    store.ring_append("r.json", "a", capacity=5)
    assert store.ring_append("r.json", "b", capacity=5) == ["a", "b"]


def test_a_ring_needs_room_for_one_record(store):
    with pytest.raises(ValueError, match="at least one"):
        store.ring_append("r.json", 1, capacity=0)


# -- the coalescing queue ------------------------------------------------------------


def test_a_second_put_of_a_key_coalesces_keeping_the_first_time_and_counting(store):
    assert store.queue_put("q.json", "a", {"v": 1}) == 1
    store.fake.t += 5
    assert store.queue_put("q.json", "a", {"v": 2}) == 2
    pending = store.queue_pending("q.json")
    assert list(pending) == ["a"]
    assert pending["a"]["payload"] == {"v": 2}
    assert pending["a"]["first_at"] == 1000.0 and pending["a"]["last_at"] == 1005.0


def test_take_returns_due_entries_oldest_first_and_removes_them(store):
    store.queue_put("q.json", "b", 1)
    store.fake.t += 1
    store.queue_put("q.json", "a", 2)
    got = store.queue_take("q.json")
    assert [k for k, _ in got] == ["b", "a"]
    assert store.queue_pending("q.json") == {}
    assert store.queue_take("q.json") == []


def test_take_respects_the_limit_and_leaves_the_rest_queued(store):
    for i, k in enumerate("abc"):
        store.fake.t += 1
        store.queue_put("q.json", k, i)
    assert [k for k, _ in store.queue_take("q.json", limit=2)] == ["a", "b"]
    assert list(store.queue_pending("q.json")) == ["c"]


def test_a_key_is_due_only_after_it_has_been_quiet_for_its_debounce(store):
    store.queue_put("q.json", "k", 1, debounce_s=10)
    store.fake.t += 9
    store.queue_put("q.json", "k", 2, debounce_s=10)  # a new put restarts the quiet period
    store.fake.t += 9
    assert store.queue_take("q.json") == []
    store.fake.t += 1
    [(key, entry)] = store.queue_take("q.json")
    assert key == "k" and entry["payload"] == 2 and entry["count"] == 2


def test_a_crash_while_taking_leaves_the_entries_queued(store, monkeypatch):
    store.queue_put("q.json", "k", 1)
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        store.queue_take("q.json")
    monkeypatch.undo()
    assert list(store.queue_pending("q.json")) == ["k"]


# -- retention -----------------------------------------------------------------------


def _files(d: Path, ages: dict[str, tuple[float, int]], now: float) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for name, (age, size) in ages.items():
        p = d / name
        p.write_bytes(b"x" * size)
        os.utime(p, (now - age, now - age))


def _names(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir() if not p.name.startswith("."))


def test_retention_keeps_the_newest_n(tmp_path):
    _files(tmp_path, {"a": (30, 1), "b": (20, 1), "c": (10, 1)}, 1000)
    gone = Retention(keep_n=2).sweep(tmp_path, now=1000)
    assert [p.name for p in gone] == ["a"] and _names(tmp_path) == ["b", "c"]


def test_retention_drops_what_is_older_than_the_age(tmp_path):
    _files(tmp_path, {"a": (500, 1), "b": (100, 1)}, 1000)
    Retention(max_age_s=200).sweep(tmp_path, now=1000)
    assert _names(tmp_path) == ["b"]


def test_retention_trims_oldest_first_to_the_byte_budget(tmp_path):
    _files(tmp_path, {"a": (30, 40), "b": (20, 40), "c": (10, 40)}, 1000)
    Retention(max_bytes=90).sweep(tmp_path, now=1000)
    assert _names(tmp_path) == ["b", "c"]


def test_retention_with_no_limits_removes_nothing_and_spares_dotfiles(tmp_path):
    _files(tmp_path, {"a": (10**6, 1), ".gitignore": (10**6, 1), ".x.lock": (10**6, 1)}, 1000)
    assert Retention().sweep(tmp_path, now=1000) == []
    Retention(keep_n=0, max_age_s=0, max_bytes=0).sweep(tmp_path, now=1000)
    assert sorted(p.name for p in tmp_path.iterdir()) == [".gitignore", ".x.lock"]


def test_retention_of_a_missing_directory_is_a_no_op(tmp_path):
    assert Retention(keep_n=1).sweep(tmp_path / "nope", now=0) == []


def test_the_store_sweeps_a_subdirectory_by_its_clock(store):
    _files(store.root / "reports", {"old": (500, 1), "new": (1, 1)}, store.fake.t)
    store.sweep("reports", Retention(max_age_s=100))
    assert _names(store.root / "reports") == ["new"]
    store.write("d.json", 1)
    store.remove("d.json")
    store.remove("d.json")
    assert store.read("d.json") is None


@pytest.mark.parametrize("subdir", ["..", "/etc", "a/b", ""])
def test_a_sweep_cannot_leave_the_store(store, subdir):
    with pytest.raises(ValueError, match="plain store name"):
        store.sweep(subdir, Retention(keep_n=0))


@pytest.mark.parametrize("fmt", ["null", '"abc"', "true", "[1]"])
def test_a_newer_document_with_an_odd_fmt_is_still_a_newer_refusal(store, fmt):
    store.root.mkdir(parents=True)
    store.path("n.json").write_text('{"schema": 9, "ddflow": "9.9.9", "fmt": ' + fmt + "}")
    with pytest.raises(fsio.NewerContent, match=r"9\.9\.9"):
        store.read("n.json")


def test_remove_keeps_the_lock_file_so_exclusion_survives(store):
    store.write("d.json", 1)
    lock = fsio.lock_path_for(store.path("d.json"))
    inode = lock.stat().st_ino
    store.remove("d.json")
    assert store.read("d.json") is None and lock.stat().st_ino == inode


def _clock_probing_the_lock(store, name):
    """A clock that records whether the store's lock is held at the moment it is read."""
    held = []

    def clock():
        try:
            with fsio.file_lock(fsio.lock_path_for(store.path(name)), timeout_s=0):
                held.append(False)  # we could take it: nobody holds it
        except fsio.LockTimeout:
            held.append(True)
        return 1000.0

    return clock, held


def test_a_put_reads_the_clock_while_it_holds_the_lock(store):
    store.queue_put("q.json", "k", 1)  # creates the lock file
    store.clock, held = _clock_probing_the_lock(store, "q.json")
    store.queue_put("q.json", "k", 2)
    assert held and all(held)


def test_a_take_reads_the_clock_while_it_holds_the_lock(store):
    store.queue_put("q.json", "k", 1)
    store.clock, held = _clock_probing_the_lock(store, "q.json")
    store.queue_take("q.json")
    assert held and all(held)
