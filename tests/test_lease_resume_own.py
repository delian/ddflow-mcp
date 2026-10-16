"""The agent whose lease lapsed resumes it with a plain claim (DX-lease-lapse-reclaim)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_lease_reacquire_overlap import _lapsed_then_taken

from conftest import run_cli
from ddflow.api import lifecycle as LC
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def test_the_holder_resumes_its_lapsed_lease_without_force(repo):
    _lapsed_then_taken(repo)
    assert run_cli(repo, "--agent", "second", "release", "T2")[0] == 0
    out = LC.claim(repo, "T1", agent="first", no_worktree=True)
    assert out.exit == 0, out.reason
    assert out.data["globs"] == ["src/a.py"], out.data
    st = fold(EventLog(repo, "r").read_all())
    assert set(st.active_leases(time.time())) == {"T1"}


def test_the_holder_resuming_over_a_taken_file_is_refused_and_told_which(repo):
    _lapsed_then_taken(repo)
    out = LC.claim(repo, "T1", agent="first", no_worktree=True)
    assert out.exit == 3, out.reason
    assert "T2" in out.reason and "second" in out.reason and "src/a.py" in out.reason, out.reason
    st = fold(EventLog(repo, "r").read_all())
    assert set(st.active_leases(time.time())) == {"T2"}


def test_another_agent_still_cannot_take_a_lapsed_lease_without_force(repo):
    _lapsed_then_taken(repo)
    assert run_cli(repo, "--agent", "second", "release", "T2")[0] == 0
    out = LC.claim(repo, "T1", agent="third", no_worktree=True)
    assert out.exit == 3 and "EXPIRED" in out.reason, out.reason
