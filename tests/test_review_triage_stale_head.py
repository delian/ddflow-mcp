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
    assert gate.evidence.get("tree_sha"), "a passed must name the tree it reviewed"
