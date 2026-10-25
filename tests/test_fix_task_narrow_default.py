"""B45b80286d2: a fix task defaults to the bug's OWN found scope, not a project-wide glob.

A bug found on an item whose globs are project-wide (`ddflow/**` + `tests/**`) and whose
text names the specific files involved used to inherit the WIDE globs: a wildcard matches
any named file, so the fix task kept `ddflow/**` and `tests/**` whole, two narrow sibling
bug fixes excluded each other and the wave serialized. The default is now the bug's found
scope: the files its text names that exist in the tree, plus the tests pinning them. A bug
naming MANY files (past `[bugs].named_scope_at`) keeps the inherited globs; `--globs`
always wins.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.api import knowledge as K
from ddflow.infra.log import EventLog

WIDE = ["ddflow/**", "tests/**"]


def _seed(repo: Path, files: list[str], wide: list[str] = WIDE) -> EventLog:
    run_cli(repo, "init")
    for f in files:
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text("")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": wide})
    log.append("item.completed", "T1", {"sha": "abc"})
    return log


def _fix(repo: Path, **kw) -> list[str]:
    out = K.bug_found(repo, item="T1", agent="a", **kw)
    assert out.exit == 0, out.reason
    return state(repo).items[out.data["fix_task"]].globs


def test_a_bug_naming_one_narrow_file_gets_that_file_not_the_wide_item_globs(repo):
    _seed(repo, ["ddflow/services/merge.py", "tests/test_merge.py"])
    assert _fix(repo, summary="merge timed out on ddflow/services/merge.py", id="Bn") == [
        "ddflow/services/merge.py",
        "tests/test_merge.py",
    ]


def test_the_tests_pinning_the_named_files_join_them(repo):
    _seed(repo, ["ddflow/services/claim.py", "tests/test_claim.py", "tests/test_other.py"])
    globs = _fix(repo, summary="claim.py refuses a lease it should give", id="Bt")
    assert globs == ["ddflow/services/claim.py", "tests/test_claim.py"]


def test_a_bug_naming_many_files_keeps_the_inherited_globs(repo):
    _seed(repo, [f"notes/f{n}.py" for n in range(25)], wide=["notes/**"])
    many = " ".join(f"syncs notes/f{n}.py" for n in range(25))
    assert _fix(repo, summary=many, id="Bm") == ["notes/**"]


def test_a_bug_named_file_outside_the_item_globs_is_still_the_bugs_own_scope(repo):
    """The item working on claim.py no longer swallows a narrow merge.py bug (its own file)."""
    _seed(repo, ["ddflow/services/claim.py", "ddflow/services/merge.py", "tests/test_merge.py"])
    globs = _fix(repo, summary="merge.py timed out under claim.py", id="Bx")
    assert globs == [
        "ddflow/services/merge.py",
        "ddflow/services/claim.py",
        "tests/test_merge.py",
    ]


def test_a_bug_naming_a_test_file_keeps_that_test_file(repo):
    _seed(repo, ["tests/test_merge.py"])
    assert _fix(repo, summary="test_merge.py is flaky", id="Tt") == ["tests/test_merge.py"]


def test_explicit_globs_still_win_over_the_found_scope(repo):
    _seed(repo, ["ddflow/services/merge.py", "tests/test_merge.py"])
    out = K.bug_found(
        repo, summary="merge timed out on ddflow/services/merge.py", item="T1", id="Be",
        globs="ddflow/new_module.py", agent="a",
    )  # fmt: skip
    assert out.exit == 0, out.reason
    assert state(repo).items["fix-Be"].globs == ["ddflow/new_module.py"]
