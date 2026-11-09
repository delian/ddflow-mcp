"""B9eeecd134c: a `needs` edge to a REMOVED item blocks the dependent for good. Doctor
must report it with the command that drops it, and abandoning or removing an item that open
items still need must say so -- without refusing or changing the exit.

An ABANDONED item is settled, not "gone": it satisfies its dependents (B740b7f8cf4), so a
need on it does NOT block the dependent and doctor does NOT report it as a stuck edge. Only
a removed need is "gone".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import run_cli


def _seed(repo, needs="D1,D2"):
    for t in ("D1", "D2"):
        run_cli(repo, "task", "add", t, "--title", "task item", "--globs", f"{t}.py")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "t.py", "--needs", needs)


def test_doctor_does_not_report_a_need_on_an_abandoned_item(repo):
    """An abandoned need is settled (satisfies its dependent), so doctor must NOT report it as
    a stuck edge (B740b7f8cf4); a removed need is (see the removed test below)."""
    _seed(repo)
    run_cli(repo, "abandon", "D1", "--reason", "done as a decision")
    _code, out, err = run_cli(repo, "doctor")
    text = out + err
    assert "T1 needs 'D1'" not in text, f"abandoned is settled, not stuck: {text}"


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


def test_two_abandoned_needs_leave_no_stuck_edge_and_drop_only_the_abandoned_edge(repo):
    """After D1 is abandoned (settled) and D2 is then abandoned, doctor reports no stuck edge,
    and abandoning D2 offers to drop only the D2 edge -- D1 is settled and stays."""
    _seed(repo, needs="D1,D2")
    run_cli(repo, "abandon", "D1", "--reason", "x")
    _, out, _ = run_cli(repo, "--json", "abandon", "D2", "--reason", "x")
    assert json.loads(out)["drop_edges"] == ['ddflow update T1 --needs "D1"']
    text = "".join(run_cli(repo, "doctor")[1:])
    assert "T1 needs 'D1'" not in text
    assert "T1 needs 'D2'" not in text


def test_a_dependent_whose_only_need_is_abandoned_is_still_warned(repo):
    _seed(repo, needs="D1")
    _, out, _ = run_cli(repo, "abandon", "D1", "--reason", "x")
    assert "T1" in out and "ddflow update T1 --needs" in out
