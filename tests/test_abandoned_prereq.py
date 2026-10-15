"""B9eeecd134c: a `needs` edge to an abandoned (or removed) item blocks the dependent for
good. Doctor must report it with the command that drops it, and abandoning or removing
an item that open items still need must say so -- without refusing or changing the exit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import run_cli


def _seed(repo, needs="D1,D2"):
    for t in ("D1", "D2"):
        run_cli(repo, "task", "add", t, "--globs", f"{t}.py")
    run_cli(repo, "task", "add", "T1", "--globs", "t.py", "--needs", needs)


def test_doctor_reports_a_need_on_an_abandoned_item(repo):
    _seed(repo)
    run_cli(repo, "abandon", "D1", "--reason", "done as a decision")
    code, out, err = run_cli(repo, "doctor")
    text = out + err
    assert "T1 needs 'D1'" in text
    assert 'ddflow update T1 --needs "D2"' in text
    assert code != 0, "a stuck edge is a PROBLEM-class finding"


def test_doctor_reports_a_need_on_a_removed_item_and_ignores_a_done_one(repo):
    _seed(repo)
    run_cli(repo, "remove", "D1", "--force")
    run_cli(repo, "claim", "D2", agent="a")
    text = "".join(run_cli(repo, "doctor")[1:])
    assert "T1 needs 'D1'" in text and "removed" in text
    assert "T1 needs 'D2'" not in text


def test_abandon_warns_naming_dependents_and_still_succeeds(repo):
    _seed(repo)
    code, out, _ = run_cli(repo, "abandon", "D1", "--reason", "decision")
    assert code == 0
    assert "T1" in out and "ddflow update T1 --needs" in out
    code, out, _ = run_cli(repo, "--json", "abandon", "D2", "--reason", "decision")
    assert code == 0
    assert json.loads(out)["stranded"] == ["T1"]


def test_abandon_with_no_dependents_says_nothing_extra(repo):
    _seed(repo, needs="")
    code, out, _ = run_cli(repo, "abandon", "D1", "--reason", "x")
    assert code == 0 and "needs" not in out
