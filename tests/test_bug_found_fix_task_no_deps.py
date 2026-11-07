"""B-bug-found-task-no-deps-tag: `bug found` files its fix task with tags `bugfix` and the
derived `wave:0`, but no `no-deps` tag, though r-task-phase-needs-wave says a task with no
prerequisite carries `no-deps` (so that none is a decision, not an omission). The filed fix
task has empty `needs`, so it must also carry `no-deps`.

The bug's own fix task keeps living under the standing bugs phase: `--item X` says where
the bug was FOUND, not which phase holds the fix, and putting the fix in X's phase would
hold that phase open (D-old-work-behind-core-docs-accepted, B1bfcaf6ebb).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.api import knowledge as K
from ddflow.infra.log import EventLog


def test_a_fix_task_filed_by_bug_found_carries_no_deps(repo):
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", "T1", {"parent": "P1", "title": "parser", "globs": ["src/p.py"]})
    out = K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    assert out.data["fix_task_filed"] is True, out.data
    st = state(repo)
    ft = st.items["fix-Bx"]
    assert ft.tags and "no-deps" in ft.tags, ft.tags
