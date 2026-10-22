"""A regression test added for a triaged finding does not void the pass (bugs
B-review-pass-on-refutation-invalidated-by-test-only-commit, B4ab42dbedb).

Answering a refuted finding with a test changed the item's changed set, so the settled round
'held other content now' and cost another round (at the cap, the operator). Paths matching
`[review].triage_ignore_globs` (tests, by default) that moved since the round are not new work;
any other moved path still blocks the pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_review_triage import _state
from test_review_triage_scoped_tree import _round, _settle


def test_a_test_added_after_the_round_does_not_block_the_pass(repo, tmp_path):
    wt = _round(repo, tmp_path)
    (wt / "tests").mkdir()
    (wt / "tests" / "test_regress.py").write_text("def test_x():\n    assert True\n")
    last = _settle(repo)
    assert _state(repo).gates["critic"].outcome == "passed", last.data["text"]


def test_a_test_plus_a_source_edit_still_blocks_and_names_only_the_source(repo, tmp_path):
    wt = _round(repo, tmp_path)
    (wt / "tests").mkdir()
    (wt / "tests" / "test_regress.py").write_text("def test_x():\n    assert True\n")
    (wt / "c.py").write_text("x = 2  # edited after the review\n")
    last = _settle(repo)
    assert _state(repo).gates["critic"].outcome == "failed", last.data["text"]
    assert "c.py" in last.data["text"] and "test_regress.py" not in last.data["text"]
