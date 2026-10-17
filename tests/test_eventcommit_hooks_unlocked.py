"""The event-log commit's hooks run without the append lock (merge-holds-event-lock-during-ci).

`merge` commits the event log under `[log].commit_events_at`, and the commit ran inside the
append lock: a project whose pre-commit takes minutes made every other ddflow command in
the project fail after 30 s with a "wedged agent" message.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

from ddflow.api import knowledge as K


def test_another_writer_can_take_the_lock_while_the_commit_hooks_run(repo, tmp_path):
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    answer = tmp_path / "lock-state"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        f"#!/bin/sh\n{sys.executable} - <<'PY'\n"
        "import fcntl, os\n"
        f"fd = os.open({str(repo / '.ddflow' / 'events.lock')!r}, os.O_CREAT | os.O_RDWR)\n"
        "try:\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        f"    open({str(answer)!r}, 'w').write('free')\n"
        "except BlockingIOError:\n"
        f"    open({str(answer)!r}, 'w').write('held')\n"
        "PY\n"
    )
    hook.chmod(0o755)
    out = K.session_end(repo, "s1")
    assert answer.read_text() == "free", (answer.read_text(), out)


def test_two_doors_committing_at_once_do_not_commit_each_others_stage(repo):
    """With the commit's hooks outside the append lock, a second door can stage while the
    first door's commit runs: the commits are serialised by a lock of their own, so neither
    commits the other's staged line nor dies on 'nothing to commit'."""
    import threading
    import time

    from ddflow.services import eventcommit as EC

    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    hook = repo / ".git" / "hooks" / "pre-commit"
    started = repo / "hook-started"
    hook.write_text(f"#!/bin/sh\ntouch {str(started)!r}\nsleep 1\n")
    hook.chmod(0o755)
    outs: dict[str, object] = {}

    def door(name: str) -> None:
        outs[name] = K.session_end(repo, name)

    first = threading.Thread(target=door, args=("s1",))
    first.start()
    deadline = time.monotonic() + 60  # the first door is inside its slow commit when this ends
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert started.exists()
    second = threading.Thread(target=door, args=("s2",))
    second.start()
    first.join()
    second.join()
    for name, out in outs.items():
        note = out.data.get("events_note", "")
        # each door committed for itself: no sweep after them is needed or run
        assert out.data.get("events_committed"), (name, out.data)
        assert "nothing to commit" not in note and "index.lock" not in note, (name, note)
    assert EC.uncommitted_shards(repo) == []
    shard = next((repo / ".ddflow" / "events").glob("*.jsonl")).relative_to(repo)
    committed = _git(repo, "show", f"HEAD:{shard}")
    assert '"s1"' in committed and '"s2"' in committed, committed
    # and the second door's own commit is the one that holds its line, not the first's
    first, second = (outs[n].data["events_committed"] for n in ("s1", "s2"))
    assert first != second
    held = _git(repo, "show", f"{second}:{shard}")
    assert '"s2"' in held


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
    with shard.open("a") as f:
        f.write(shard.read_text().splitlines()[-1] + "\n")
    blob = repo / ".ddflow" / "blobs" / "x.json"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text("{}")
    blob.chmod(0)
    try:
        sha, note = EC.commit_shards(repo, "mine")
    finally:
        blob.chmod(0o644)
    assert sha and "x.json" in note and "unreadable" in note, (sha, note)


def test_a_commit_that_lands_while_the_hooks_run_is_not_reverted(repo):
    """git takes the parent when the commit begins and will not move HEAD from elsewhere: the
    event-log commit is refused, the other commit stays, and the lines wait for the next door."""
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
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f"cd {str(repo)!r}\n"
        "echo v2 > other.txt\n"
        "env -u GIT_INDEX_FILE git add other.txt\n"
        "env -u GIT_INDEX_FILE git commit --no-verify -qm 'landed during the hooks' -- other.txt\n"
    )
    hook.chmod(0o755)
    sha, note = EC.commit_shards(repo, "mine")
    assert not sha and "not committed" in note, (sha, note)
    assert _git(repo, "show", "HEAD:other.txt") == "v2", "the commit that landed was reverted"
    assert EC.uncommitted_shards(repo), "the lines are still there for the next door"
