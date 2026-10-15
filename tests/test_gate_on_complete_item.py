"""A gate is not recorded on a finished item, nor from another item's tree (B8b2dafcbf3).

A subagent ran `ddflow review <id> --gate rubber_duck` from a directory that was not the
item's worktree; the review ran against that tree and recorded a FAILED rubber_duck on an
item completed earlier. Two holes: `review` did not check whose tree it stood in, and no
gate verb asked whether the item was still open.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import finish, run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _gate_events(repo: Path, item: str, gate: str) -> int:
    n = 0
    for f in (repo / ".ddflow" / "events").glob("*.jsonl"):
        for ln in f.read_text().splitlines():
            ev = json.loads(ln) if ln.strip() else {}
            if ev.get("subject") == item and gate in json.dumps(ev.get("payload", {})):
                n += 1
    return n


def _from(repo: Path, cwd: Path, *argv: str) -> tuple[int, str, str]:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", *argv],
        capture_output=True, text=True, env=env, cwd=cwd, timeout=300,
    )  # fmt: skip
    return p.returncode, p.stdout, p.stderr


def _finished(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "one", "--globs", "a/*")
    run_cli(repo, "claim", "T1", "--no-worktree")
    assert finish(repo, "T1")[0] == OK


def test_gate_record_on_a_completed_item_is_refused(repo):
    _finished(repo)
    code, _out, err = run_cli(
        repo, "gate", "record", "T1", "rubber_duck", "--outcome", "failed", "--evidence", "x"
    )
    assert code == REFUSED, err
    assert "complete" in err and "--force" in err, err


def test_gate_skip_on_a_completed_item_is_refused(repo):
    _finished(repo)
    code, _out, err = run_cli(repo, "gate", "skip", "T1", "bug_hunt", "--reason", "late")
    assert code == REFUSED, err


def test_gate_run_on_a_completed_item_is_refused(repo):
    _finished(repo)
    code, _out, err = run_cli(repo, "gate", "run", "T1", "ci")
    assert code == REFUSED, err


def test_force_records_on_a_completed_item_and_logs_why(repo):
    _finished(repo)
    code, _out, err = run_cli(
        repo, "gate", "record", "T1", "rubber_duck", "--outcome", "failed",
        "--evidence", "x", "--force", "--reason", "late finding",
    )  # fmt: skip
    assert code in (OK, FAIL), err
    assert any(
        "late finding" in f.read_text() for f in (repo / ".ddflow" / "events").glob("*.jsonl")
    )


def test_review_from_another_items_tree_is_refused(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--title", "a", "--globs", "a/*")
    run_cli(repo, "task", "add", "B", "--title", "b", "--globs", "b/*")
    run_cli(repo, "claim", "A")
    code, out, err = run_cli(repo, "claim", "B")
    assert code == OK, out + err
    trees = {i: json.loads(run_cli(repo, "--json", "show", i)[1])["worktree"] for i in ("A", "B")}
    tree_a = (
        (repo / trees["A"]).resolve() if not Path(trees["A"]).is_absolute() else Path(trees["A"])
    )
    tree_b = (
        (repo / trees["B"]).resolve() if not Path(trees["B"]).is_absolute() else Path(trees["B"])
    )
    before = _gate_events(repo, "A", "rubber_duck")
    code, _out, err = _from(repo, tree_b, "review", "A", "--gate", "rubber_duck")
    assert code == REFUSED, err
    assert str(tree_a) in err, err
    assert _gate_events(repo, "A", "rubber_duck") == before, "nothing may be recorded"
    # --force waives the finished-item rule only, never the tree check
    run_cli(repo, "task", "add", "C", "--title", "c", "--globs", "c/*")
    run_cli(repo, "claim", "C", "--no-worktree")
    assert finish(repo, "C")[0] == OK
    before = _gate_events(repo, "C", "rubber_duck")
    code, _out, err = _from(
        repo, tree_b, "review", "C", "--gate", "rubber_duck", "--force", "--reason", "x"
    )
    assert code == REFUSED, err
    assert "B's worktree" in err, err
    assert _gate_events(repo, "C", "rubber_duck") == before
