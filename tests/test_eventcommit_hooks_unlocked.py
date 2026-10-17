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
