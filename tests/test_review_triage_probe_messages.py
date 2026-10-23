"""The refusal of a triage probe says what failed to resolve and what is accepted (bugs
B-triage-error-truncates-test-name, B3c2f2fea11).

A probe naming a missing test was refused with the bare words ('probe names test
'test_review'...') and no word on where it looked or what form is accepted; a probe that
mentioned a test FILE by its stem (no .py) read as a missing test.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_review_triage import _full_review, _setup, _triage


def _refusal(repo, probe: str) -> str:
    out = _triage(repo, 1, "refuted", probe)
    assert out.exit != 0, probe
    return out.reason + out.data.get("text", "")


def _project(repo, tmp_path) -> None:
    _setup(repo, tmp_path)
    _full_review(repo)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_real.py").write_text("def test_real():\n    pass\n")
    (repo / "tests" / "test_stemmed.py").write_text("def test_inside():\n    pass\n")


def test_a_missing_node_names_the_whole_id_and_the_accepted_forms(repo, tmp_path):
    _project(repo, tmp_path)
    why = _refusal(repo, "tests/test_real.py::TestB::test_no_such_thing_at_all")
    assert "tests/test_real.py::TestB::test_no_such_thing_at_all" in why, why
    assert "path::node id" in why and "command that was run" in why, why


def test_a_bare_missing_test_name_says_where_it_looked(repo, tmp_path):
    _project(repo, tmp_path)
    why = _refusal(repo, "see test_no_such_test_exists for it")
    assert "'test_no_such_test_exists'" in why and "tests/" in why, why
    assert "path::node id" in why, why


def test_a_test_file_stem_is_answered_with_the_file_and_the_node_form(repo, tmp_path):
    _project(repo, tmp_path)
    why = _refusal(repo, "covered by test_stemmed")  # the stem of tests/test_stemmed.py
    assert "tests/test_stemmed.py::<test name>" in why, why


def test_a_missing_node_says_it_looked_in_every_worktree(repo, tmp_path):
    _project(repo, tmp_path)
    why = _refusal(repo, "tests/test_real.py::test_not_there")
    assert "tests/test_real.py::test_not_there" in why, why  # the whole id, not its leaf
    assert "tests/ of every worktree" in why, why


def test_a_node_in_a_file_that_does_not_exist_is_named_whole(repo, tmp_path):
    """The reported shape: `tests/x.py::name`, with no tests/x.py at all."""
    _project(repo, tmp_path)
    why = _refusal(repo, "tests/x.py::name")
    assert "probe names tests/x.py::name," in why, why
