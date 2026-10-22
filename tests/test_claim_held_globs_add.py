"""`claim <held item> --globs X` adds X to the lease and the item's globs (Bf22edc42d5).

It used to REPLACE both, so the next commit was refused as uncovered.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _seed(repo: Path) -> None:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t1", "globs": ["src/a.py"]})


def test_reclaiming_a_held_item_with_globs_adds_them(repo):
    _seed(repo)
    assert LC.claim(repo, "T1", agent="me", no_worktree=True).exit == 0
    out = LC.claim(repo, "T1", agent="me", globs="h/b.py", no_worktree=True)
    assert out.exit == 0, out.reason
    assert sorted(out.data["globs"]) == ["h/b.py", "src/a.py"]
    it = fold(EventLog(repo, "r").read_all()).items["T1"]
    assert sorted(it.globs) == ["h/b.py", "src/a.py"]
    assert sorted(it.lease.globs) == ["h/b.py", "src/a.py"]


def test_a_first_claim_with_globs_still_names_the_files(repo):
    _seed(repo)
    out = LC.claim(repo, "T1", agent="me", globs="h/b.py", no_worktree=True)
    assert out.exit == 0, out.reason
    assert out.data["globs"] == ["h/b.py"]


def test_a_held_lease_with_no_globs_falls_back_to_the_items_own(repo):
    """The fallback reads the ITEM's globs (a list on the item), never another lease's."""
    _seed(repo)
    EventLog(repo, "me").append(
        "lease.acquired", "T1", {"holder": "me", "at": time.time(), "ttl_s": 1800, "globs": []}
    )
    out = LC.claim(repo, "T1", agent="me", globs="h/b.py", no_worktree=True)
    assert out.exit == 0, out.reason
    assert sorted(out.data["globs"]) == ["h/b.py", "src/a.py"]
