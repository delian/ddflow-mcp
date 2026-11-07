"""The event-log commit skips the hooks and goes through one serialized path (B-events-commit-races-shared-main).

`merge` commits the event log under `[log].commit_events_at`. The log holds no code, so its
commit skips the project's hooks (`git commit --no-verify`) -- they only delay it (a commit-msg
hook alone ran over 2 minutes) and let another lane's commit race in and sweep up the staged
shard (B-events-commit-races-shared-main). An earlier fix moved the hooks OUT of the append lock
(merge-holds-event-lock-during-ci: a project whose pre-commit takes minutes made every other
ddflow command fail after 30 s with a "wedged agent" message); skipping them removes the delay
wholesale, and a lock of its own serializes the doors.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

from ddflow.api import knowledge as K


def test_an_events_only_commit_skips_the_pre_commit_hook(repo, tmp_path):
    """`--no-verify` skips the pre-commit hook too (not only commit-msg), so nothing waits on
    a pre-commit suite for an events-only commit (bug B-events-commit-races-shared-main)."""
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    answer = tmp_path / "pre-commit-ran"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {str(answer)!r}\nexit 1\n")
    hook.chmod(0o755)
    out = K.session_end(repo, "s1")
    assert out.data.get("events_committed"), out.data
    assert not answer.exists(), "the pre-commit hook must not run for an events-only commit"


def test_an_events_only_commit_skips_the_hooks(repo, tmp_path):
    """B-events-commit-races-shared-main: an events-only commit ran the project hooks (the
    check-msg hook alone was still running at 124 s), letting another lane's commit race in
    and sweep up the shard staged in the shared index. The event log holds no code, so the
    commit skips the hooks (`git commit --no-verify`) and goes through the one serialized
    path (`ddflow-eventcommit.lock`)."""
    from ddflow.services import eventcommit as EC

    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text("#!/bin/sh\nexit 1\n")  # rejects every commit: the events-only commit skips it
    hook.chmod(0o755)
    out = K.session_end(repo, "s1")
    assert out.data.get("events_committed"), out.data
    assert not out.data.get("events_note"), out.data
    assert EC.uncommitted_shards(repo) == []


def test_two_doors_committing_at_once_do_not_commit_each_others_stage(repo):
    """Two doors started together serialize their event-log commits: the shard holds both
    lines, and neither door dies on 'cannot lock ref' or 'index.lock' (bug
    B-events-commit-races-shared-main swept one lane's line under another lane's message)."""
    import threading

    from ddflow.services import eventcommit as EC

    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    barrier = threading.Barrier(2)
    outs: dict[str, object] = {}

    def door(name: str) -> None:
        barrier.wait()
        outs[name] = K.session_end(repo, name)

    first = threading.Thread(target=door, args=("s1",))
    second = threading.Thread(target=door, args=("s2",))
    first.start()
    second.start()
    first.join()
    second.join()
    assert EC.uncommitted_shards(repo) == []
    shard = next((repo / ".ddflow" / "events").glob("*.jsonl")).relative_to(repo)
    committed = _git(repo, "show", f"HEAD:{shard}")
    assert '"s1"' in committed and '"s2"' in committed, committed
    for name, out in outs.items():
        note = out.data.get("events_note", "")
        assert "cannot lock ref" not in note and "index.lock" not in note, (name, note)


def test_the_commit_holds_exactly_the_bytes_that_were_checked(repo, monkeypatch):
    """A line appended after the snapshot (while the commit's hooks run) is not in that
    commit, whole or torn: it is left for the next door, uncommitted and intact."""
    from ddflow.services import eventcommit as EC

    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    K.session_end(repo, "s1")
    shard = next((repo / ".ddflow" / "events").glob("*.jsonl"))
    line = shard.read_text().splitlines()[-1]
    with shard.open("a") as f:
        f.write(line + "\n")  # the door's own whole line
    snapshot = shard.read_text()
    real = EC.W.git

    def append_during_commit(root, *args, **kw):
        if args and args[0] == "commit":
            with shard.open("a") as f:
                f.write(line[:20])  # a writer mid-line while the hooks run
        return real(root, *args, **kw)

    monkeypatch.setattr(EC.W, "git", append_during_commit)
    sha, note = EC.commit_shards(repo, "mine")
    assert sha, note
    assert _git(repo, "show", f"HEAD:{shard.relative_to(repo)}") == snapshot.strip()
    # the half line is still only in the working tree, and the shared index is not left staged
    assert shard.read_text() == snapshot + line[:20]
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_an_unreadable_blob_is_named_not_silently_dropped(repo):
    import os

    import pytest

    from ddflow.services import eventcommit as EC

    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    K.session_end(repo, "s1")
    shard = next((repo / ".ddflow" / "events").glob("*.jsonl"))
    digest = "ab" * 32
    with shard.open("a") as f:  # the blob is named, so it is this log's to commit
        f.write(json.dumps({"kind": "x", "evidence": {"k": {"$blob": f"sha256:{digest}"}}}) + "\n")
    blob = repo / ".ddflow" / "blobs" / digest[:2] / f"{digest}.json"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text("{}")
    blob.chmod(0)
    try:
        sha, note = EC.commit_shards(repo, "mine")
    finally:
        blob.chmod(0o644)
    assert sha and f"{digest}.json" in note and "unreadable" in note, (sha, note)


def test_a_commit_that_lands_during_the_commit_is_not_reverted(repo, monkeypatch):
    """git takes the parent when the commit begins and will not move HEAD from elsewhere: the
    event-log commit is refused, the other commit stays, and the lines wait for the next door.
    No hook runs now (--no-verify), so the landing commit is simulated before the event-log
    commit begins (bug B-events-commit-races-shared-main)."""
    from ddflow.services import eventcommit as EC

    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    K.session_end(repo, "s1")
    (repo / "other.txt").write_text("v1\n")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-qm", "other", "--no-verify")
    shard = next((repo / ".ddflow" / "events").glob("*.jsonl"))
    with shard.open("a") as f:
        f.write(shard.read_text().splitlines()[-1] + "\n")

    real = EC.W.git

    def land_during_commit(root, *args, **kw):
        if args and args[0] == "commit":
            (repo / "other.txt").write_text("v2\n")
            _git(repo, "add", "other.txt")
            _git(repo, "commit", "-qm", "landed", "--no-verify")
        return real(root, *args, **kw)

    monkeypatch.setattr(EC.W, "git", land_during_commit)
    sha, note = EC.commit_shards(repo, "mine")
    # the commit that landed is NOT reverted: ours is made again on top of it, so both stay
    assert sha and "made again on top of it" in note, (sha, note)
    assert _git(repo, "show", "HEAD:other.txt") == "v2", "the commit that landed was reverted"
