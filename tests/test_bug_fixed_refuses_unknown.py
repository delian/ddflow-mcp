"""`bug fixed` closes a bug that exists, with a regression test that exists.

Two ways the rule "a bug is not closed without a regression test" was satisfied on
paper only (bugs B883720d6e7 and B855e3cac54):

* an id that names no recorded bug was accepted. Passing the TASK id — natural, since
  `bug found --item` links one — printed "bug REC closed", folded a phantom closed bug,
  and left the real one open;
* a `--regression-test` naming a test that does not exist was accepted, and closed two
  real bugs pointing at nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api

OK, FAIL, REFUSED = 0, 1, 3

SUITE = """
import pytest


def test_boundary():
    pass


@pytest.mark.parametrize("n", [1, 2])
def test_each(n):
    pass


class TestGroup:
    def test_member(self):
        pass
"""


def _open_bugs(repo: Path) -> int:
    return json.loads(run_cli(repo, "--json", "status")[1])["open_bugs"]


def _suite(root: Path) -> None:
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_fix.py").write_text(SUITE)


def test_an_unknown_bug_id_is_refused_and_names_the_open_bugs_of_that_item(repo):
    run_cli(repo, "init")
    _suite(repo)
    found = api.bug_found(repo, summary="off by one", item="REC")
    real = found.data["id"]

    out = api.bug_fixed(repo, "REC", regression_test="tests/test_fix.py::test_boundary")
    assert out.exit == REFUSED, out
    assert real in out.reason, out.reason
    assert _open_bugs(repo) == 1, "the real bug is still open and nothing was closed"


def test_a_refused_unknown_id_folds_no_phantom_bug(repo):
    run_cli(repo, "init")
    _suite(repo)
    api.bug_fixed(repo, "NOPE", regression_test="tests/test_fix.py::test_boundary")
    from ddflow.api import _load

    _log, _cfg, st = _load(repo)
    assert "NOPE" not in st.bugs


def test_a_regression_test_file_that_does_not_exist_is_refused(repo):
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_nowhere.py::test_boundary")
    assert out.exit == FAIL, out
    assert "tests/test_nowhere.py::test_boundary" in out.reason
    assert _open_bugs(repo) == 1


def test_a_test_function_that_does_not_exist_is_refused(repo):
    """The case that happened: the file was right, the function name was not."""
    run_cli(repo, "init")
    _suite(repo)
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_fix.py::test_boundary_and_more")
    assert out.exit == FAIL, out
    assert "test_boundary_and_more" in out.reason
    assert _open_bugs(repo) == 1


def test_every_entry_of_a_list_is_checked(repo):
    run_cli(repo, "init")
    _suite(repo)
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    out = api.bug_fixed(
        repo, "B1", regression_test="tests/test_fix.py::test_boundary, tests/test_fix.py::test_gone"
    )
    assert out.exit == FAIL, out
    assert "tests/test_fix.py::test_gone" in out.reason
    assert "tests/test_fix.py::test_boundary" not in out.reason, "only the missing one is named"


def test_real_node_ids_close_the_bug(repo):
    run_cli(repo, "init")
    _suite(repo)
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    out = api.bug_fixed(
        repo,
        "B1",
        regression_test=(
            "tests/test_fix.py::test_boundary, tests/test_fix.py::test_each[2], "
            "tests/test_fix.py::TestGroup::test_member"
        ),
    )
    assert out.exit == OK, out
    assert _open_bugs(repo) == 0


def test_a_test_that_exists_only_on_the_fix_branch_is_found(repo):
    """Bugs are closed from the item's worktree, before the test reaches the base."""
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    tree = repo.parent / "fixtree"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "fix", str(tree)], check=True
    )
    _suite(tree)
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_fix.py::test_boundary")
    assert out.exit == OK, out


def test_a_non_pytest_reference_is_accepted_and_said_to_be_unchecked(repo):
    """ddflow is language-agnostic: a spec or a shell command cannot be resolved here, and
    refusing it would lock every non-Python project out of closing bugs."""
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    out = api.bug_fixed(repo, "B1", regression_test="spec/cart_spec.rb:42")
    assert out.exit == OK, out
    assert out.data["unchecked"] == ["spec/cart_spec.rb:42"]


def test_separators_inside_a_parametrize_id_are_part_of_the_id(repo):
    """`::` and `,` are legal inside `[...]`; splitting on them refused a real test
    (rubber-duck on 34f6c53, B-bfu-param-sep)."""
    run_cli(repo, "init")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_fix.py").write_text(
        "import pytest\n\n\n@pytest.mark.parametrize('v', ['a::b', '1,2'])\n"
        "def test_param(v):\n    pass\n"
    )
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    out = api.bug_fixed(
        repo,
        "B1",
        regression_test="tests/test_fix.py::test_param[a::b], tests/test_fix.py::test_param[1,2]",
    )
    assert out.exit == OK, out
    assert out.data["unchecked"] == [], "a bracketed comma split the entry in two"


def test_a_test_file_outside_the_repository_does_not_count(repo, tmp_path):
    """An absolute path made `tree / path` ignore the tree, so any file anywhere closed
    the bug (rubber-duck on 34f6c53, B-bfu-abs-path)."""
    run_cli(repo, "init")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "evil.py").write_text("def test_x():\n    pass\n")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    for ref in (f"{outside}/evil.py::test_x", "../elsewhere/evil.py::test_x"):
        out = api.bug_fixed(repo, "B1", regression_test=ref)
        assert out.exit == FAIL, (ref, out)
    assert _open_bugs(repo) == 1


def test_a_node_id_is_resolved_as_a_chain_not_as_names_anywhere(repo):
    """Each name used to be matched anywhere in the file, so a method passed for a
    module-level test and a function outside the class passed for Class::method
    (critic on 34f6c53, B-bfu-structure)."""
    run_cli(repo, "init")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text(
        "class TestA:\n    pass\n\n\nclass TestB:\n    def test_method(self):\n        pass\n\n\n"
        "def test_free():\n    pass\n\n\n"
        "if True:\n    def test_guarded():\n        pass\n"
    )
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    for bogus in ("tests/test_x.py::test_method", "tests/test_x.py::TestA::test_free"):
        out = api.bug_fixed(repo, "B1", regression_test=bogus)
        assert out.exit == FAIL, (bogus, out)
    out = api.bug_fixed(
        repo,
        "B1",
        regression_test=(
            "tests/test_x.py::TestB::test_method, tests/test_x.py::test_free, "
            "tests/test_x.py::test_guarded"
        ),
    )
    assert out.exit == OK, out


def test_a_file_this_python_cannot_parse_is_matched_by_name(repo):
    """Newer syntax than ddflow's interpreter is still a real suite for the project; the
    close must not be refused for it (the lesson from B22-unparsed)."""
    run_cli(repo, "init")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_new.py").write_text("match (:\n\ndef test_z():\n    pass\n")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    run_cli(repo, "bug", "found", "--id", "B2", "--summary", "y")
    # A separate open bug for the negative case: reusing the closed B1 would let the
    # assertion pass for a reason other than the missing name (critic on 5ced6c7).
    assert api.bug_fixed(repo, "B2", regression_test="tests/test_new.py::test_q").exit == FAIL
    assert api.bug_fixed(repo, "B1", regression_test="tests/test_new.py::test_z").exit == OK
