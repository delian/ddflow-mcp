"""The events the log-derived flow signals read are really written.

`merge_failure_rate` counts failed merge-gate outcomes; a failed merge used to write
nothing, so the rate could only ever read 0.0 once one merge had succeeded.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core import flowsignals as FS
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)


def test_a_conflicting_merge_is_a_failed_merge_gate_outcome(repo, monkeypatch):
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    run_cli(repo, "init")
    (repo / "c.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "edit c", "--globs", "c.txt")
    code, out, err = run_cli(repo, "--json", "claim", "T1", agent="alpha")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "c.txt").write_text("branch\n")
    _git(tree, "commit", "-qam", "branch edit")
    (repo / "c.txt").write_text("main\n")
    _git(repo, "commit", "-qam", "main edit")

    code, out, err = run_cli(repo, "merge", "T1", agent="alpha")
    assert code != 0

    events = EventLog(repo).read_all()
    merge = [e for e in events if e.kind.startswith("gate.") and e.data.get("gate") == "merge"]
    assert [e.kind for e in merge] == ["gate.failed"]
    rate = FS.merge_failure_rate(events, time.time() + 1)
    assert rate == 1.0
    st = fold(events, strict=False)
    assert FS.compute(events, st, Config.load(repo), time.time() + 1).merge_failure_rate == 1.0

    # The failure does not wedge the item: resolve, merge again, and the gate now passes.
    _git(tree, "merge", "-q", "main", "-X", "ours", "-m", "take main")
    code, out, err = run_cli(repo, "merge", "T1", agent="alpha")
    assert code == 0, out + err
    merge = [
        e.kind
        for e in EventLog(repo).read_all()
        if e.kind.startswith("gate.") and e.data.get("gate") == "merge"
    ]
    assert merge == ["gate.failed", "gate.passed"]


def test_a_refused_precondition_is_not_a_failed_merge(repo, monkeypatch, tmp_path):
    """The target branch is checked out in another worktree: git never judged the item's
    branch, so the merge gate must not read as failed (it would inflate the rate)."""
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "edit c", "--globs", "c.txt")
    code, out, err = run_cli(repo, "--json", "claim", "T1", agent="alpha")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "c.txt").write_text("branch\n")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", "branch edit")
    _git(repo, "checkout", "-q", "-b", "elsewhere")
    _git(repo, "worktree", "add", str(tmp_path / "other"), "main")

    code, out, err = run_cli(repo, "merge", "T1", agent="alpha")
    assert code != 0 and "checked out in the worktree" in err, out + err
    events = EventLog(repo).read_all()
    assert not [e for e in events if e.kind.startswith("gate.") and e.data.get("gate") == "merge"]
