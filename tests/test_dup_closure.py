"""Closing a duplicate bug (D-no-duplicates, 3): nothing closes automatically.

`bug fixed X` lists the open bugs linked to X as duplicates, so the fixer closes each
explicitly; `bug duplicate D --of X` closes D only once X is fixed and has its regression
test, and records the link and X's test with the closure.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import knowledge as K
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, REFUSED = 0, 3

SUITE = "def test_guard():\n    pass\n"


def _init(repo: Path) -> None:
    run_cli(repo, "init")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_probe.py").write_text(SUITE)
    for bid in ("B1", "B2", "B3"):
        run_cli(repo, "bug", "found", "--id", bid, "--summary", f"report {bid}", "--no-task")
    for dup in ("B2", "B3"):
        assert run_cli(repo, "link", dup, "--duplicate-of", "B1")[0] == 0


def _fix(repo: Path, bug: str = "B1"):
    return run_cli(
        repo, "bug", "fixed", bug,
        "--regression-test", "tests/test_probe.py::test_guard",
        "--skip-regression-verify", "--verify-reason", "fixture bug has no pre-fix source",
    )  # fmt: skip


def _state(repo: Path):
    return fold(EventLog(repo).read_all())


def test_bug_fixed_lists_its_open_duplicates(repo):
    _init(repo)
    code, out, err = _fix(repo)
    assert code == OK, err
    assert "B2" in out and "B3" in out
    assert "bug duplicate" in out
    assert _state(repo).bugs["B2"].open, "listing must not close the duplicate"


def test_bug_fixed_api_returns_the_open_duplicates(repo):
    _init(repo)
    out = K.bug_fixed(
        repo, "B1", regression_test="tests/test_probe.py::test_guard",
        verify_regression=False, verify_reason="fixture",
    )  # fmt: skip
    assert out.ok
    assert sorted(out.data["open_duplicates"]) == ["B2", "B3"]


def test_duplicate_refused_while_the_original_is_open(repo):
    _init(repo)
    code, _out, err = run_cli(repo, "bug", "duplicate", "B2", "--of", "B1")
    assert code == REFUSED
    assert "B1" in err and "fixed" in err
    assert _state(repo).bugs["B2"].open


def test_duplicate_closes_once_the_original_is_fixed(repo):
    _init(repo)
    assert _fix(repo)[0] == OK
    code, out, err = run_cli(repo, "bug", "duplicate", "B2", "--of", "B1")
    assert code == OK, err
    st = _state(repo)
    assert not st.bugs["B2"].open and st.bugs["B2"].fixed_at == ""
    assert "B1" in st.bugs["B2"].invalid_reason
    assert "tests/test_probe.py::test_guard" in st.bugs["B2"].invalid_reason
    assert st.bugs["B3"].open, "an unnamed duplicate must stay open"
    assert "B1" in st.links["B2"].linked("duplicate_of")


def test_duplicate_refused_when_the_original_was_fixed_without_a_test(repo):
    _init(repo)
    EventLog(repo).append("bug.fixed", "B1", {"regression_test": "", "regression_tests": []})
    assert not _state(repo).bugs["B1"].open
    code, _out, err = run_cli(repo, "bug", "duplicate", "B2", "--of", "B1")
    assert code == REFUSED
    assert "test" in err


def test_duplicate_refused_for_unknown_or_self_or_closed(repo):
    _init(repo)
    assert _fix(repo)[0] == OK
    assert run_cli(repo, "bug", "duplicate", "B2", "--of", "NOPE")[0] == REFUSED
    assert run_cli(repo, "bug", "duplicate", "B1", "--of", "B1")[0] == REFUSED
    assert run_cli(repo, "bug", "duplicate", "B2", "--of", "B1")[0] == OK
    assert run_cli(repo, "bug", "duplicate", "B2", "--of", "B1")[0] == REFUSED
