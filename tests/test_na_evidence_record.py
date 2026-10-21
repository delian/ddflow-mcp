"""fix-na-evidence-record: the evidence that excused a required gate (B-na-required-gate) is
written onto `item.completed` as `not_applicable`, and shown in `ddflow show`."""

from __future__ import annotations

import subprocess

from conftest import pass_pipeline, run_cli

from ddflow.core.model import fold
from ddflow.infra import worktree as W
from ddflow.infra.log import EventLog


def _setup(repo, change: str):
    """unit_tests (REQUIRED) applies only to src/**; T1 commits a change to ``change``."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", change)
    assert run_cli(repo, "config", "--set", "gate.unit_tests.applies_when", '["src/**"]')[0] == 0
    code, out, err = run_cli(repo, "claim", "T1", agent="a1")
    assert code == 0, out + err
    wt = fold(EventLog(repo).read_all()).items["T1"].worktree
    tree = W.load_path(repo, wt)
    (tree / change).parent.mkdir(parents=True, exist_ok=True)
    (tree / change).write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tree), "add", change], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "change"], check=True)


def _completed(repo):
    return [e for e in EventLog(repo).read_all() if e.kind == "item.completed"][-1]


def test_a_diff_excused_required_gate_is_recorded_on_the_completion(repo):
    _setup(repo, "lib/x.py")
    pass_pipeline(repo, "T1", omit=("unit_tests",))
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-5", agent="a1")
    assert code == 0, out + err
    na = _completed(repo).data.get("not_applicable")
    assert isinstance(na, dict) and set(na) == {"unit_tests"}, na
    assert "src/**" in na["unit_tests"] and "lib/x.py" in na["unit_tests"], na
    shown = run_cli(repo, "show", "T1")[1]
    assert "not applicable on the change: unit_tests" in shown, shown
    # an older reader folds the event exactly as before
    assert fold(EventLog(repo).read_all()).items["T1"].state == "done"


def test_a_completion_with_nothing_excused_has_no_key(repo):
    _setup(repo, "src/a.py")
    pass_pipeline(repo, "T1")
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-5", agent="a1")
    assert code == 0, out + err
    assert "not_applicable" not in _completed(repo).data
    assert "not applicable on the change" not in run_cli(repo, "show", "T1")[1]


def test_the_reasons_are_redacted_leaf_by_leaf_in_the_committed_log():
    from ddflow.core import redact as R

    red = R.Redactor("log", secret_patterns=[r"sk-[A-Za-z0-9]{16,}"], home="/home/zedd")
    out = R.redact_event_data(
        "item.completed",
        {"not_applicable": {"unit_tests": "diff docs/sk-abcdefghijklmnop0123 misses src/**"}},
        red,
    )
    assert "sk-abcdefghijklmnop0123" not in str(out), out
    assert set(out["not_applicable"]) == {"unit_tests"}
