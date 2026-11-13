"""A fix task inherits its source item's globs only where the bug text points at them (B76f42d8f1f).

The source item's whole glob list used to be copied onto the fix task, so a bug found while
working on `claim.py` that is about `merge.py` got `claim.py` and the rest, and the claim had
to replace them with `--globs`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.api import knowledge as K
from ddflow.infra.log import EventLog
from ddflow.services.bug_globs import fix_task_globs, inherit_globs

SRC = ["ddflow/api/lifecycle/claim.py", "ddflow/services/bug_globs.py", "tests/test_claim.py"]


def test_text_naming_no_file_inherits_everything():
    assert inherit_globs(SRC, "the parser drops a line") == SRC


def test_text_naming_a_source_file_keeps_only_that_one():
    assert inherit_globs(SRC, "claim.py refuses a lease") == ["ddflow/api/lifecycle/claim.py"]


def test_text_naming_only_other_files_inherits_nothing():
    assert inherit_globs(SRC, "merge timed out; README.md not uptodate") == []


def test_a_wildcard_source_glob_matches_a_named_file():
    assert inherit_globs(["ddflow/services/merge*.py"], "see ddflow/services/merge_x.py") == [
        "ddflow/services/merge*.py"
    ]


def test_bug_found_does_not_copy_unrelated_globs(repo):
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": SRC})
    log.append("item.completed", "T1", {"sha": "abc"})
    K.bug_found(repo, summary="merge timed out; README.md stale", item="T1", id="Bm", agent="a")
    # The bug's OWN named file is its scope (B45b80286d2); the source item's files are not copied.
    assert state(repo).items["fix-Bm"].globs == ["README.md"]


def test_slash_words_in_prose_are_not_file_names():
    for text in (
        "the read/write path races",
        "and/or 24/7 client/server",
        "w/o a lease",
        "the split is 50/50.",
        "runs 24/7.",
        "either/or.",
    ):
        assert inherit_globs(SRC, text) == SRC, text


def test_a_named_path_does_not_match_the_same_basename_elsewhere():
    assert inherit_globs(["ddflow/services/config.py"], "scripts/config.py is slow") == []
    assert inherit_globs(["ddflow/services/config.py"], "config.py is slow") == [
        "ddflow/services/config.py"
    ]


def test_a_bare_named_file_matches_a_wildcard_basename_glob():
    assert inherit_globs(["ddflow/services/*.py"], "config.py is slow") == ["ddflow/services/*.py"]


def _tracked(repo: Path, rel: str, text: str = "") -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", rel], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed", "--no-verify"], check=True)


def test_a_tool_bug_naming_a_test_file_gets_that_test_file(repo):
    """DX-fixtask-edit-existing-test-glob: a ddflow-scoped bug inherits no source files,
    but the test file its summary names is where the regression test goes, so the fix
    task's globs carry it and the edit commits without `ddflow update --globs`."""
    _tracked(repo, "tests/test_claim.py")
    kept, _dropped = fix_task_globs(
        repo,
        "",
        ["ddflow/api/lifecycle/claim.py"],
        "the brief lists claim.py; the regression test goes in tests/test_claim.py",
        tool_bug=True,
        named_scope_at=20,
    )
    assert "tests/test_claim.py" in kept
    assert "ddflow/api/lifecycle/claim.py" not in kept


def test_a_named_test_file_is_kept_in_the_found_scope_too(repo):
    """The found-scope path (a non-tool bug naming few files) already holds the named
    test file, so the regression-test edit is covered there too (from `_named_files`,
    not only from `named_test_globs`)."""
    _tracked(repo, "ddflow/api/lifecycle/claim.py")
    _tracked(repo, "tests/test_claim.py")
    kept, _dropped = fix_task_globs(
        repo,
        "",
        ["ddflow/api/lifecycle/claim.py"],
        "the brief lists claim.py; the regression test goes in tests/test_claim.py",
        tool_bug=False,
        named_scope_at=20,
    )
    assert "tests/test_claim.py" in kept
    assert "ddflow/api/lifecycle/claim.py" in kept
