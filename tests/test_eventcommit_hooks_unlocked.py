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
    hook.write_text("#!/bin/sh\nsleep 1\n")
    hook.chmod(0o755)
    outs: dict[str, object] = {}

    def door(name: str) -> None:
        outs[name] = K.session_end(repo, name)

    first = threading.Thread(target=door, args=("s1",))
    first.start()
    time.sleep(0.4)  # the first door is inside its slow commit now
    second = threading.Thread(target=door, args=("s2",))
    second.start()
    first.join()
    second.join()
    for name, out in outs.items():
        note = out.data.get("events_note", "")
        assert "nothing to commit" not in note and "index.lock" not in note, (name, note)
    EC.commit_shards(repo, "last")  # whatever a door left for the next one
    assert EC.uncommitted_shards(repo) == []
