"""B166: a cold read (a one-shot CLI call) does not re-parse the whole log.

The snapshot is a CACHE in `.ddflow/local/` and these tests are about what makes it safe
to have: every way it can be wrong must end in "ignore it and parse the log", and the
result must be byte-for-byte what the unoptimised path returns.
"""

from __future__ import annotations

import hashlib
import json
import marshal

import pytest

from ddflow.config import LogConfig
from ddflow.core.events import Event
from ddflow.infra import log as L
from ddflow.infra.log import EventLog, clear_parse_cache
from tests.test_log_merged_order import _ev, _put
from tests.test_log_merged_order import _reference as _reference_ids

CFG = LogConfig()
N = 1600  # events per shard; the rewrite floor is 1000 events


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(EventLog, "stamp", False)
    monkeypatch.setattr(L, "SNAPSHOT_MIN_EVENTS", 10)
    monkeypatch.delenv(L.SNAPSHOT_ENV, raising=False)
    clear_parse_cache()
    yield
    clear_parse_cache()


def _log(repo, cfg=CFG) -> EventLog:
    return EventLog(repo, "x", log_cfg=cfg)


def _snap(log: EventLog):
    return log._snapshot_path()


def _build(repo, n=N):
    """Two shards of n events, read once (which writes the snapshot), then 'a new process'."""
    log = _log(repo)
    _put(log, "a", [_ev("a", i) for i in range(1, n + 1)])
    _put(log, "b", [_ev("b", i) for i in range(1, n + 1)])
    log.read_all()
    assert _snap(log).exists()
    clear_parse_cache()
    return log


def _check(repo, log: EventLog | None = None) -> EventLog:
    """A cold read, compared with the independent reference: the whole EVENTS, not just
    their ids. `log.parsed` is how many lines the READ parsed (the reference's own parsing
    is not counted)."""
    log = log or _log(repo)
    before = len(_PARSES)
    got = log.read_all()
    log.parsed = len(_PARSES) - before
    want, want_skipped = _reference(log)
    assert got == want
    assert log.skipped_lines == want_skipped
    return log


_PARSES: list[int] = []


def _reference(log: EventLog):
    """(events, skipped) by parsing every shard line independently of the code under test."""
    ids, skipped = _reference_ids(log)
    by_id = {}
    for path in sorted(log.dir.glob("*.jsonl")):
        for line in path.read_bytes().decode().splitlines():
            try:
                e = Event.from_json(line)
            except ValueError:
                continue
            by_id.setdefault(e.id, e)
    return [by_id[i] for i in ids], skipped


@pytest.fixture
def parses(monkeypatch):
    _PARSES.clear()
    real = Event.from_json

    def counting(line):
        _PARSES.append(1)
        return real(line)

    monkeypatch.setattr(Event, "from_json", staticmethod(counting))
    return _PARSES


def test_a_big_log_writes_a_git_ignored_snapshot(repo):
    log = _build(repo)
    assert (_snap(log).parent / ".gitignore").read_text() == "*\n"


def test_a_small_log_writes_none(repo, monkeypatch):
    monkeypatch.setattr(L, "SNAPSHOT_MIN_EVENTS", 5000)
    log = _log(repo)
    _put(log, "a", [_ev("a", i) for i in range(1, 2000)])
    log.read_all()
    assert not _snap(log).exists()


def test_a_cold_read_from_the_snapshot_parses_nothing_and_matches(repo, parses):
    _build(repo)
    assert _check(repo).parsed == 0, "lines were parsed despite a valid snapshot"


def test_only_the_appended_tail_is_parsed(repo, parses):
    log = _build(repo)
    _put(log, "a", [_ev("a", N + i) for i in range(1, 8)])
    _put(log, "c", [_ev("c", 3)])  # a shard that appeared after the snapshot
    _put(log, "b", [_ev("a", 5)])  # a duplicate of a's event, merged in from elsewhere
    assert _check(repo).parsed == 9


def test_out_of_order_tails_and_duplicates_match_the_reference(repo):
    log = _build(repo)
    _put(log, "a", [_ev("a", 2, "late")])  # a Lamport value far below the high-water mark
    _put(log, "b", [_ev("a", 2, "late")])  # and the same event again in another shard
    _check(repo)
    # and a big tail forces the order to be rebuilt rather than patched
    _put(log, "a", [_ev("a", 10 + i, "bulk") for i in range(200)])
    _check(repo)


@pytest.mark.parametrize(
    "damage",
    ["flip", "truncate", "garbage", "empty", "version", "fields", "format", "size", "parser"],
)
def test_a_damaged_snapshot_is_ignored_not_trusted(repo, parses, damage):
    log = _build(repo)
    path = _snap(log)
    raw = path.read_bytes()
    head, _, payload = raw.partition(b"\n")
    meta = json.loads(head)
    if damage == "flip":
        mid = len(payload) // 2
        raw = head + b"\n" + payload[:mid] + bytes([payload[mid] ^ 1]) + payload[mid + 1 :]
    elif damage == "truncate":
        raw = raw[: len(raw) // 2]
    elif damage == "garbage":
        raw = b"not a snapshot\n\x00\x01"
    elif damage == "empty":
        raw = b""
    else:
        meta[damage] = [] if damage == "fields" else 0 if damage == "size" else "other"
        raw = json.dumps(meta).encode() + b"\n" + payload
    path.write_bytes(raw)
    assert _check(repo).parsed == 2 * N, "a damaged snapshot must fall back to a full parse"


def test_a_shard_changed_in_place_is_reparsed_the_others_are_not(repo, parses):
    log = _build(repo)
    # a branch switch rewrites shard a with different events of the same length
    a = log.dir / "a.jsonl"
    a.write_bytes(
        b"".join((_ev("a", i, "other").to_json() + "\n").encode() for i in range(1, N + 1))
    )
    assert _check(repo).parsed == N  # a reparsed; b came from the snapshot


def test_a_shorter_or_deleted_shard_is_never_served_from_the_snapshot(repo):
    log = _build(repo)
    a = log.dir / "a.jsonl"
    lines = a.read_bytes().splitlines(keepends=True)
    a.write_bytes(b"".join(lines[: N // 2]))  # truncated: the snapshot claims more
    _check(repo)
    clear_parse_cache()
    (log.dir / "b.jsonl").unlink()
    _check(repo)


def test_a_torn_tail_is_reported_and_never_snapshotted_as_consumed(repo):
    log = _log(repo)
    _put(log, "a", [_ev("a", i) for i in range(1, N + 1)])
    full = _ev("a", N + 1).to_json().encode()
    with (log.dir / "a.jsonl").open("ab") as fh:
        fh.write(full[:-8])
    log.read_all()
    assert log.skipped_lines == 1
    clear_parse_cache()
    fresh = _check(repo)  # cold read from the snapshot: still one torn line, not lost
    assert fresh.skipped_lines == 1
    with (log.dir / "a.jsonl").open("ab") as fh:
        fh.write(full[-8:] + b"\n")
    clear_parse_cache()
    assert len(_check(repo).read_all()) == N + 1


def test_verify_never_uses_the_snapshot(repo):
    log = _build(repo)
    # Poison the snapshot with VALID checksums: an event whose body no longer matches its id.
    path = _snap(log)
    head, _, payload = path.read_bytes().partition(b"\n")
    body = marshal.loads(payload)
    name = "a.jsonl"
    tuples = body[name][3]
    forged = list(tuples[3])
    forged[2] = {"title": "forged", "kind": "task"}
    tuples[3] = tuple(forged)
    payload = marshal.dumps(body)
    meta = json.loads(head)
    meta["size"], meta["sha256"] = len(payload), hashlib.sha256(payload).hexdigest()
    path.write_bytes(json.dumps(meta).encode() + b"\n" + payload)
    clear_parse_cache()
    reader = _log(repo)
    assert [e for e in reader.read_all() if e.data.get("title") == "forged"], "poison not loaded"
    # verify() recomputes from the bytes: the forged copy is invisible, and a REAL edit of
    # the file is caught even though a snapshot exists.
    assert reader.verify() == []
    a = log.dir / "a.jsonl"
    raw = a.read_bytes()
    a.write_bytes(raw.replace(b'"title":"t"', b'"title":"u"', 1))
    clear_parse_cache()
    assert any("content does not match" in p for p in _log(repo).verify())


def test_off_means_no_snapshot_is_written_or_read(repo, monkeypatch, parses):
    log = _build(repo)
    for how in ("reuse_parsed", "env"):
        clear_parse_cache()
        cfg = LogConfig(reuse_parsed=False) if how == "reuse_parsed" else CFG
        if how == "env":
            monkeypatch.setenv(L.SNAPSHOT_ENV, "0")
        assert _check(repo, _log(repo, cfg)).parsed == 2 * N
    _snap(log).unlink()
    _log(repo).read_all()  # env is still off
    assert not _snap(log).exists()


def test_the_snapshot_is_rewritten_once_the_tail_is_large_and_not_before(repo):
    log = _build(repo)
    before = _snap(log).read_bytes()
    _put(log, "a", [_ev("a", N + i) for i in range(1, 50)])
    _check(repo)
    assert _snap(log).read_bytes() == before, "49 new events do not justify a rewrite"
    clear_parse_cache()
    _put(log, "a", [_ev("a", N + 100 + i) for i in range(1500)])
    _check(repo)
    assert _snap(log).read_bytes() != before
    clear_parse_cache()
    reader = _check(repo)
    assert len(reader.read_all()) == 2 * N + 49 + 1500


def test_an_unreadable_snapshot_location_does_not_break_the_read(repo):
    log = _log(repo)
    local = log.dir.parent / "local"
    local.mkdir(parents=True, exist_ok=True)
    (local / L.SNAPSHOT_FILE).mkdir()  # a directory where the file should be
    _put(log, "a", [_ev("a", i) for i in range(1, N + 1)])
    assert len(log.read_all()) == N
    clear_parse_cache()
    _check(repo)


def test_a_snapshot_is_per_clone_state_and_two_clones_agree(repo, tmp_path):
    """Merges across clones: identical logs give identical output with and without it."""
    log = _build(repo)
    other = tmp_path / "clone"
    other.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", str(other)], check=True)
    (other / ".ddflow").mkdir()
    (other / ".ddflow" / "events").mkdir()
    for f in log.dir.glob("*.jsonl"):
        (other / ".ddflow" / "events" / f.name).write_bytes(f.read_bytes())
    plain = EventLog(other, "x", log_cfg=LogConfig(reuse_parsed=False))
    clear_parse_cache()
    snap = _log(repo)
    assert [e.id for e in snap.read_all()] == [e.id for e in plain.read_all()]


@pytest.mark.parametrize("tamper", ["repeat", "drop", "reorder", "empty", "range", "stale_shard"])
def test_a_bad_saved_order_is_rejected_even_with_valid_checksums(repo, tamper):
    """The order is not covered by the shard hashes, so it is checked on adoption."""
    log = _build(repo)
    path = _snap(log)
    head, _, payload = path.read_bytes().partition(b"\n")
    body = marshal.loads(payload)
    names, order = body["\0order"]
    order = list(order)
    if tamper == "repeat":
        order[5] = order[4]
    elif tamper == "drop":
        order.pop(7)
    elif tamper == "reorder":
        order[3], order[4] = order[4], order[3]
    elif tamper == "empty":
        order = []
    elif tamper == "range":
        order[0] = 10**9
    elif tamper == "stale_shard":
        names = [(n, c + 1) for n, c in names]
    body["\0order"] = (names, order)
    payload = marshal.dumps(body)
    meta = json.loads(head)
    meta["size"], meta["sha256"] = len(payload), hashlib.sha256(payload).hexdigest()
    path.write_bytes(json.dumps(meta).encode() + b"\n" + payload)
    _check(repo)


def test_a_read_only_log_never_writes_a_snapshot_into_the_repo_it_reads(repo):
    """`external.sync` and exports read SOMEONE ELSE's repository: no files there."""
    log = _log(repo)
    _put(log, "a", [_ev("a", i) for i in range(1, N + 1)])
    reader = EventLog(repo, "x", log_cfg=CFG, cache_writes=False)
    assert len(reader.read_all()) == N
    assert not _snap(log).exists() and not _snap(log).parent.exists()


def test_a_valid_saved_order_is_adopted_so_the_cold_read_does_not_sort(repo, monkeypatch):
    """Adoption is the feature; the rejection tests alone would stay green if it never
    happened. (`_sorted_unique` is the from-scratch sort+dedupe.)"""
    _build(repo)
    sorts = []
    real = L._sorted_unique
    monkeypatch.setattr(L, "_sorted_unique", lambda ev: sorts.append(1) or real(ev))
    _check(repo)
    assert not sorts, "a valid snapshot order was not adopted"
    # control: with the order poisoned the same read must fall back to sorting
    clear_parse_cache()
    path = _snap(_log(repo))
    head, _, payload = path.read_bytes().partition(b"\n")
    body = marshal.loads(payload)
    body["\0order"] = (body["\0order"][0], [])
    payload = marshal.dumps(body)
    meta = json.loads(head)
    meta["size"], meta["sha256"] = len(payload), hashlib.sha256(payload).hexdigest()
    path.write_bytes(json.dumps(meta).encode() + b"\n" + payload)
    _check(repo)
    assert sorts, "the control did not fall back to a sort"
