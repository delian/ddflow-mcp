"""Triage never upgrades a review gate to passed over a tree the review did not see (bug B835d22b138).

A round reviewed one tree; the author then changed the code and triaged that round's findings
`--refuted` / `--confirmed`. The settling triage recorded the gate `passed` with the OLD round's
evidence, so `complete` accepted work no reviewer had seen. The pass needs a round that ran on
the tree the item holds now.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_review_triage import _full_review, _setup, _state

import ddflow.api.review_settle as ST

OK = 0


def _tri(repo, n, verdict="refuted", probe="ran it: no such bug"):
    return ST.triage(repo, "T1", gate="critic", finding=n, verdict=verdict, probe=probe)


def test_refuting_findings_of_an_older_tree_does_not_pass_the_gate(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    (repo / "c.py").write_text("x = 2  # edited after the review\n")  # the tree moved on
    _tri(repo, 1)
    last = _tri(repo, 2)
    assert last.exit == OK
    assert _state(repo).gates["critic"].outcome == "failed", last.data["text"]
    assert not last.data.get("passed_on_refutation")
    assert "re-run" in last.data["text"] and "ddflow review" in last.data["text"]


def test_refuting_findings_of_the_current_tree_still_passes_and_names_it(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    _tri(repo, 1)
    last = _tri(repo, 2)
    gate = _state(repo).gates["critic"]
    assert gate.outcome == "passed" and last.data["passed_on_refutation"] is True
    from ddflow.services.gates import evidence as GE

    assert GE.recorded_content(gate.evidence.get("tree_sha", "")) == GE.source_tree(repo), (
        "a passed must name the tree it reviewed, and that is the tree the item holds"
    )


def test_a_round_that_names_no_tree_cannot_be_passed_by_triage(repo, tmp_path, monkeypatch):
    _setup(repo, tmp_path)
    _full_review(repo)
    from ddflow.api import review as RV

    monkeypatch.setattr(RV.GE, "recorded_content", lambda *_a, **_k: "")
    _tri(repo, 1)
    last = _tri(repo, 2)
    assert _state(repo).gates["critic"].outcome == "failed", last.data["text"]
    assert "names no tree" in last.data["text"]


def test_an_unreadable_current_tree_cannot_be_passed_by_triage(repo, tmp_path, monkeypatch):
    _setup(repo, tmp_path)
    _full_review(repo)
    from ddflow.api import review as RV

    monkeypatch.setattr(RV.GE, "source_tree", lambda *_a, **_k: "")
    _tri(repo, 1)
    last = _tri(repo, 2)
    gate = _state(repo).gates["critic"]
    assert gate.outcome == "failed", last.data["text"]
    assert "cannot be read" in last.data["text"] and "stays failed" in last.data["text"]


def test_a_round_on_a_clean_commit_is_compared_by_that_commits_tree(repo, tmp_path, monkeypatch):
    """A round recorded as `<head>+clean` names the commit's own tree (no `st:` content id)."""
    import subprocess
    from types import SimpleNamespace as NS

    from ddflow.api import review as RV

    _setup(repo, tmp_path)
    for cmd in (["add", "-A"], ["commit", "-qm", "work"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    rec = NS(evidence={"tree_sha": f"{head[:12]}+clean"})
    st = NS(items={"T1": NS(gates={"critic": rec})})
    monkeypatch.setattr(RV.triaging, "item_tree", lambda *_a, **_k: (repo, ""))
    assert RV.reviewed_other_tree(repo, None, st, "T1", "critic") == ""
    (repo / "a.py").write_text("x = 2\n")  # the tree moves on from that commit
    assert "holds other content" in RV.reviewed_other_tree(repo, None, st, "T1", "critic")


def test_a_spent_budget_on_a_stale_round_says_so_once_and_points_at_the_operator(repo, tmp_path):
    _setup(repo, tmp_path)
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace("max_rounds = 0", "max_rounds = 1"))
    _full_review(repo)
    (repo / "c.py").write_text("x = 2  # edited after the review\n")
    _tri(repo, 1)
    last = _tri(repo, 2)
    text = last.data["text"]
    assert _state(repo).gates["critic"].outcome == "failed"
    assert text.count("stays failed") == 1 and "--force --reason" in text, text


def _merged_scenario(repo, tmp_path, monkeypatch):
    """A merged item (worktree gone; `item_tree` answers None) whose `--commit` re-review
    recorded only the commit it covered (`reviewed_head`) and no `tree_sha`."""
    import subprocess
    from types import SimpleNamespace as NS

    from ddflow.api import review as RV

    _setup(repo, tmp_path)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "work"], check=True, capture_output=True
    )
    rev = ["git", "-C", str(repo), "rev-parse", "HEAD"]
    first = subprocess.run(rev, check=True, capture_output=True, text=True).stdout.strip()
    (repo / "a.py").write_text("x = 2  # one more\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "more"], check=True, capture_output=True
    )
    head = subprocess.run(rev, check=True, capture_output=True, text=True).stdout.strip()

    def make(reviewed_head, merged):
        rec = NS(evidence={"reviewed_head": reviewed_head, "review_kind": "full"})
        it = NS(
            id="T1",
            kind="task",
            worktree="",
            merged_sha=merged,
            landed_after="",
            landed_before="",
            branch="",
            pr=None,
            gates={"critic": rec},
        )
        return NS(items={"T1": it})

    monkeypatch.setattr(RV.triaging, "item_tree", lambda *_a, **_k: (None, ""))
    return first, head, make


def test_a_commit_review_after_merge_names_the_commit_it_reviewed(repo, tmp_path, monkeypatch):
    """Bug B-critic-refutation-pass-needs-tree-after-merge: the round has no worktree to
    name (`tree_sha`), but its `reviewed_head` is the merged commit the reviewer saw."""
    from ddflow.api import review as RV

    _first, head, make = _merged_scenario(repo, tmp_path, monkeypatch)
    assert RV.reviewed_other_tree(repo, None, make(head, head), "T1", "critic") == ""


def test_a_commit_review_of_an_older_commit_after_merge_is_still_stale(repo, tmp_path, monkeypatch):
    from ddflow.api import review as RV

    first, head, make = _merged_scenario(repo, tmp_path, monkeypatch)
    text = RV.reviewed_other_tree(repo, None, make(first, head), "T1", "critic")
    assert "holds other content" in text, text
