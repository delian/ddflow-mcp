"""DX-gate-run-after-merge: `merge` removes the worktree, so a command gate run on a merged
item had no tree. It used to record UNAVAILABLE over the pre-merge outcome with a reason
about "no worktree"; now it keeps a recorded outcome and names the merge in the reason."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _merged(repo: Path, *, passed_before: bool = False) -> None:
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + '\n[gate.unit_tests]\ncommand = "true"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "cfg")
    run_cli(repo, "task", "add", "T1", "--title", "t")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "t.txt").write_text("x\n")
    _git(tree, "add", ".")
    _git(tree, "commit", "-qm", "work")
    if passed_before:
        run_cli(
            repo, "gate", "record", "T1", "unit_tests", "--outcome", "passed", "--evidence", "e"
        )
    assert run_cli(repo, "merge", "T1")[0] == 0


def _gate(repo: Path, name: str):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates.get(name)


def test_a_gate_run_after_merge_keeps_the_pre_merge_pass(repo):
    _merged(repo, passed_before=True)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
    text = out + err
    assert code == 2, text  # nothing ran: the exit says so
    assert "merged" in text and "recorded" in text and "passed" in text, text
    assert _gate(repo, "unit_tests").outcome == "passed"


def test_a_gate_run_after_merge_with_no_pass_says_why(repo):
    _merged(repo)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
    text = out + err
    assert code == 2, text
    assert "merged" in text and "before `ddflow merge T1`" in text, text
    assert "claimed without a worktree" not in text
    rec = _gate(repo, "unit_tests")
    assert rec.outcome == "unavailable" and "merged" in (rec.reason or ""), rec


def test_a_forced_gate_run_after_merge_does_not_answer_with_the_stale_pass(repo):
    _merged(repo, passed_before=True)
    run_cli(repo, "gate", "run", "T1", "unit_tests", "--force", "--reason", "fresh please")
    assert _gate(repo, "unit_tests").outcome == "unavailable"
