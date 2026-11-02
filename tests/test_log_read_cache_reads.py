"""B86: the incremental read of the append-only log.

The cached read must return exactly what an uncached one does, and notice every kind of
change to a shard: append, new shard, torn tail, rewrite, divergent checkout, delete,
unreadable, vanish, early-prefix change, truncation. Why the log is not compacted instead:
see tests/test_log_read_cache_ceiling.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import subprocess

import pytest
from log_cache_support import _cold_cache, _ids, _no_version_stamp, _uncached  # noqa: F401

from ddflow.infra.log import EventLog, clear_parse_cache

# -- B86: the incremental read ------------------------------------------------------


def test_a_cached_read_returns_exactly_what_an_uncached_one_does(repo):
    log, plain = EventLog(repo, "a1"), _uncached(repo)
    for i in range(20):
        log.append("task.added", f"T{i}", {"title": f"task {i}", "kind": "task"})
    assert _ids(log.read_all()) == _ids(plain.read_all())


def test_an_event_appended_after_the_cache_warmed_is_still_read(repo):
    """The bug this design could plausibly have: a warm cache serving a stale answer."""
    log = EventLog(repo, "a1")
    log.append("task.added", "T1", {"title": "first", "kind": "task"})
    assert {e.subject for e in log.read_all()} == {"T1"}  # warms the cache

    log.append("task.added", "T2", {"title": "second", "kind": "task"})
    assert {e.subject for e in log.read_all()} == {"T1", "T2"}, (
        "the append landed after the cache warmed and was not picked up"
    )


def test_a_second_agents_new_shard_is_picked_up(repo):
    """A new shard changes the KEY set, not any cached shard's size."""
    a, b = EventLog(repo, "a1"), EventLog(repo, "a2")
    a.append("task.added", "T1", {"title": "mine", "kind": "task"})
    reader = EventLog(repo, "a1")
    assert {e.subject for e in reader.read_all()} == {"T1"}
    b.append("task.added", "T2", {"title": "theirs", "kind": "task"})
    assert {e.subject for e in reader.read_all()} == {"T1", "T2"}


def test_a_torn_tail_is_reported_once_and_read_once_it_is_completed(repo):
    """The torn-tail resume class, which is how this design fails if it is wrong.

    A crash mid-append leaves a fragment with no newline. Marking it consumed would
    skip the event FOREVER once the writer finished the line — silently, because the
    fold has no way to know a line was dropped.
    """
    log = EventLog(repo, "a1")
    log.append("task.added", "T1", {"title": "whole", "kind": "task"})
    log.read_all()  # warm

    head = b'{"kind":"task.added","subject":"TORN","data":{"title":"t","kind":"task"}'
    with log.shard.open("ab") as fh:
        fh.write(head)
    events = log.read_all()
    assert log.skipped_lines == 1, "a torn fragment must be reported, not hidden"
    assert "TORN" not in {e.subject for e in events}

    tail = b',"lamport":999,"ts":"2026-09-27T00:00:00Z","agent":"a1"}\n'
    with log.shard.open("ab") as fh:
        fh.write(tail)
    events = log.read_all()
    assert "TORN" in {e.subject for e in events}, (
        "the completed line was marked consumed while torn and is now lost forever"
    )
    assert log.skipped_lines == 0


def test_a_shard_rewritten_in_place_is_re_parsed(repo):
    """The defect the first version of this shipped, found by the rubber-duck reviewer.

    The cache used to validate on `st_ino`, documented as "a `git merge` writes a temp
    file and renames, so the inode changes". That is false — probed directly:

        $ git checkout -q other && stat -c %i .ddflow/events/a1.jsonl -> 218500670
        $ git checkout -q main  && stat -c %i .ddflow/events/a1.jsonl -> 218500670

    `open(path, "w")` likewise truncates and KEEPS the inode. So any rewrite that did not
    shrink the file defeated both checks: the prefix came from the cache, the tail read
    started mid-line in the new content, and the result mixed events that were gone with
    a bogus torn-append report.
    """
    log = EventLog(repo, "a1")
    log.append("task.added", "OLD1", {"title": "x", "kind": "task"})
    log.append("task.added", "OLD2", {"title": "y", "kind": "task"})
    assert {e.subject for e in log.read_all()} == {"OLD1", "OLD2"}  # warm

    # Rewritten IN PLACE, same inode, LONGER than before -- so neither an inode check
    # nor a shrink check can see it.
    fresh = _uncached(repo)
    other = EventLog(repo.parent / "scratch", "a1")
    lines = []
    for n in ("NEW1", "NEW2", "NEW3"):
        lines.append(
            json.dumps(
                {
                    "kind": "task.added",
                    "subject": n,
                    "data": {"title": n, "kind": "task"},
                    "agent": "a1",
                    "lamport": 1,
                    "ts": "2026-09-27T00:00:00Z",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    del other
    before_ino = log.shard.stat().st_ino
    with log.shard.open("w") as fh:  # truncate + write: the inode does NOT change
        fh.write("\n".join(lines) + "\n")
    assert log.shard.stat().st_ino == before_ino, "the fixture replaced the file; retry"
    assert log.shard.stat().st_size > 0

    got = {e.subject for e in log.read_all()}
    assert got == {"NEW1", "NEW2", "NEW3"}, f"stale events served from the cache: {got}"
    assert got == {e.subject for e in fresh.read_all()}
    assert log.skipped_lines == 0, "a mid-line tail read invented a torn append"


def test_a_git_checkout_between_DIVERGENT_branches_is_re_parsed(repo):
    """The real-world trigger, with git doing the writing — and it has to DIVERGE.

    A long-lived MCP server hits this the moment the operator switches branches.
    `Store.rebuild` would then persist the wrong events into SQLite stamped with the
    CORRECT `head()` fingerprint, so a fresh process would not rebuild them away: a
    transient cache bug becomes on-disk corruption.

    Two earlier versions of this test were VACUOUS, each for its own reason, and both are
    why the assertions below are so specific:

      1. Warming on the LONGER branch and checking out the shorter one tripped the shrink
         check that existed for another purpose.
      2. Warming on a branch the other had merely APPENDED to made one shard a literal
         prefix of the other — so reading the tail from the cached offset gives the right
         answer even with no validity check at all.

    So the branches must diverge from a shared base: the cached prefix then disagrees with
    the file's actual bytes at that offset, which is the condition a digest catches and a
    stat cannot.
    """
    sh = ["git", "-C", str(repo)]
    ident = ["-c", "user.email=t@t", "-c", "user.name=t"]

    def commit(msg):
        subprocess.run([*sh, "add", "-A"], check=True, capture_output=True)
        subprocess.run([*sh, *ident, "commit", "-qm", msg], check=True, capture_output=True)

    log = EventLog(repo, "a1")
    log.append("task.added", "BASE", {"title": "shared history", "kind": "task"})
    commit("base")

    subprocess.run([*sh, "checkout", "-q", "-b", "short"], check=True, capture_output=True)
    log.append("task.added", "SHORT1", {"title": "only on short", "kind": "task"})
    commit("short")

    subprocess.run([*sh, "checkout", "-q", "main"], check=True, capture_output=True)
    subprocess.run([*sh, "checkout", "-q", "-b", "long"], check=True, capture_output=True)
    for n in ("LONG1", "LONG2", "LONG3"):
        log.append("task.added", n, {"title": "only on long", "kind": "task"})
    commit("long")

    subprocess.run([*sh, "checkout", "-q", "short"], check=True, capture_output=True)
    small = log.shard.stat()
    assert {e.subject for e in log.read_all()} == {"BASE", "SHORT1"}  # warms the cache

    subprocess.run([*sh, "checkout", "-q", "long"], check=True, capture_output=True)
    big = log.shard.stat()
    assert big.st_size > small.st_size, "fixture: the shard must GROW or a shrink check hides it"
    # Git REPLACES the file on checkout. The inode number usually comes back unchanged only
    # because the filesystem hands the just-freed number straight back -- the reuse that
    # fooled the old inode-keyed cache, and what lets this test tell a digest from a stat.
    # A concurrent process (a parallel test worker) can take that number first; this used
    # to FAIL the precondition under `pytest -n auto` (bug Befd838f4da). The re-parse below
    # must be right either way: a new inode costs one run its discriminating power, not
    # its correctness check.
    # The branches genuinely DIVERGE: the cached offset now lands mid-line, which is the
    # condition a digest catches and no stat can. Asserted, not assumed -- if `long` ever
    # became an append to `short`, this test would silently go back to proving nothing.
    assert b"SHORT1" not in log.shard.read_bytes(), "the branches did not diverge"
    assert log.shard.read_bytes()[small.st_size - 1 : small.st_size] != b"\n", (
        "the cached offset happens to land on a line boundary, so the stale prefix would "
        "parse cleanly and the defect would not show"
    )

    got = {e.subject for e in log.read_all()}
    assert got == {"BASE", "LONG1", "LONG2", "LONG3"}, (
        f"a warm cache served the branch we left: {got}"
    )
    assert got == {e.subject for e in _uncached(repo).read_all()}
    assert log.skipped_lines == 0, "a mid-line tail read invented a torn append"


def test_a_shard_deleted_and_recreated_on_the_same_path_is_re_parsed(repo):
    """`rm -rf .ddflow && ddflow init` under a running server. ext4 hands the freed inode
    straight back, so the recreated file can land on the same inode AND the same path."""
    log = EventLog(repo, "a1")
    log.append("task.added", "OLD", {"title": "x", "kind": "task"})
    assert {e.subject for e in log.read_all()} == {"OLD"}  # warm

    log.shard.unlink()
    assert log.read_all() == [], "a deleted shard's events are still being served"

    log.append("task.added", "NEW", {"title": "y", "kind": "task"})
    assert {e.subject for e in log.read_all()} == {"NEW"}


def test_an_unreadable_shard_raises_rather_than_vanishing(repo):
    """A shard that is THERE and cannot be read must not be silently dropped.

    The pre-cache code caught `FileNotFoundError` only. Widening that to `OSError` — which
    the first version of this change did — turned a loud failure into a whole agent's
    events disappearing from the queue while `read_all` reported success and
    `skipped_lines` stayed 0, so nothing downstream could tell. Found by the cross-family
    critic. A file that has genuinely VANISHED is still benign and still skipped.
    """
    import os

    mine, theirs = EventLog(repo, "a1"), EventLog(repo, "a2")
    mine.append("task.added", "MINE", {"title": "x", "kind": "task"})
    theirs.append("task.added", "THEIRS", {"title": "y", "kind": "task"})
    clear_parse_cache()
    assert {e.subject for e in mine.read_all()} == {"MINE", "THEIRS"}

    os.chmod(theirs.shard, 0o000)
    clear_parse_cache()
    try:
        if os.geteuid() == 0:
            pytest.skip("running as root: the permission bit does not bite")
        with pytest.raises(PermissionError):
            mine.read_all()
    finally:
        os.chmod(theirs.shard, 0o644)

    # A vanished shard, by contrast, is benign and skipped.
    theirs.shard.unlink()
    clear_parse_cache()
    assert {e.subject for e in mine.read_all()} == {"MINE"}


def test_a_vanished_shards_cache_entry_is_evicted(repo):
    """Otherwise the entry lives for the process lifetime: nothing visits a path that
    `shards()` no longer returns, so `_read_shard` never sees the deletion."""
    from ddflow.infra import log as L

    log = EventLog(repo, "a1")
    log.append("task.added", "T1", {"title": "x", "kind": "task"})
    log.read_all()
    shard = log.shard
    assert shard in L._PARSE_CACHE

    shard.unlink()
    log.read_all()
    assert shard not in L._PARSE_CACHE, "a deleted shard is still held in the cache"


def test_a_change_early_in_a_long_prefix_is_detected(repo):
    """The digest must cover the WHOLE consumed prefix, not a trailing window of it.

    A window is the cheap version of this check and it is unsound: a divergence entirely
    before the window, with identical bytes after it, is invisible. Mutation N2b — a
    512-byte trailing window — passed every other test here, because their fixtures are
    smaller than 512 bytes. This one is not.
    """
    log = EventLog(repo, "a1")
    for i in range(40):  # comfortably past any plausible window size
        log.append(
            "task.added", f"T{i:03d}", {"title": "padding to exceed a window", "kind": "task"}
        )
    assert len(log.read_all()) == 40  # warms the cache
    raw = log.shard.read_bytes()
    assert len(raw) > 4096, "fixture too small to distinguish a window from the whole prefix"

    # Change the FIRST event only, same byte length, so every later byte is identical.
    assert raw.count(b'"T000"') == 1
    rewritten = raw.replace(b'"T000"', b'"T999"', 1)
    assert len(rewritten) == len(raw), "the edit changed the length; a size check would see it"
    assert rewritten[-512:] == raw[-512:], "the edit reached the tail; it must not"
    with log.shard.open("wb") as fh:  # in place: same inode, same size
        fh.write(rewritten)

    subjects = {e.subject for e in log.read_all()}
    assert "T999" in subjects and "T000" not in subjects, (
        "a change early in the prefix was not detected — the validity check is only "
        f"looking at part of it. Got: {sorted(subjects)[:3]}..."
    )


def test_a_truncated_shard_is_re_parsed(repo):
    log = EventLog(repo, "a1")
    for i in range(5):
        log.append("task.added", f"T{i}", {"title": f"t{i}", "kind": "task"})
    assert len(log.read_all()) == 5
    lines = log.shard.read_text().splitlines()
    log.shard.write_text("\n".join(lines[:2]) + "\n")
    assert len(log.read_all()) == 2, "a truncated shard kept serving the events it lost"
