"""When main moves after a full run, keep the result and test only the delta (SV-revalidate-delta)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lanes_fixture import T0, Log

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.services import phase_suite as PS
from ddflow.services import revalidate as RV
from ddflow.services.testselect import Selected, Selection


def git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    cmd = [
        "git",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-C",
        str(repo),
        *args,
    ]
    return subprocess.run(cmd, check=True, capture_output=True, text=True, env=env).stdout.strip()


def repo_with_tests(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "r"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "docs").mkdir()
    (repo / ".ddflow" / "events").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "a.py").write_text("def f():\n    return 1\n")
    (repo / "pkg" / "b.py").write_text("def g():\n    return 2\n")
    (repo / "tests" / "test_a.py").write_text(
        "from pkg import a\n\n\ndef test_f():\n    assert a.f() == 1\n"
    )
    (repo / "docs" / "n.md").write_text("n\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo, git(repo, "rev-parse", "HEAD")


def commit(repo: Path, rel: str, text: str) -> str:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"edit {rel}")
    return git(repo, "rev-parse", "HEAD")


def assess(repo: Path, a: str, b: str, **kw) -> RV.Verdict:
    return RV.assess(repo, a, b, Config(), **kw)


def test_an_unrelated_docs_or_events_commit_leaves_the_result_standing(tmp_path: Path):
    repo, a = repo_with_tests(tmp_path)
    commit(repo, "docs/more.md", "more\n")
    b = commit(repo, ".ddflow/events/e.jsonl", "{}\n")
    v = assess(repo, a, b)
    assert v.kind == "stands" and v.outcome == "passed"
    assert v.note == "unchanged by delta"


def test_a_code_file_no_test_reaches_leaves_the_result_standing(tmp_path: Path):
    repo, a = repo_with_tests(tmp_path)
    b = commit(repo, "pkg/b.py", "def g():\n    return 3\n")
    v = assess(repo, a, b)
    assert v.kind == "stands" and v.tests == []


def test_a_delta_that_reaches_a_test_runs_only_those_tests(tmp_path: Path):
    repo, a = repo_with_tests(tmp_path)
    b = commit(repo, "pkg/a.py", "def f():\n    return 1  # edit\n    \n")
    v = assess(repo, a, b)
    assert v.kind in ("selected", "stands")  # a comment-only edit may stand; a logic edit selects
    b = commit(repo, "pkg/a.py", "def f():\n    return 2\n")
    v = assess(repo, a, b)
    assert v.kind == "selected" and v.tests == ["tests/test_a.py"]
    assert "full on" in v.note and "selected on delta" in v.note


def test_a_hub_in_the_delta_needs_the_full_run(tmp_path: Path):
    def hub(tree, base, **kw):
        return Selection(
            base=base, changed=["pkg/core.py"], full="pkg/core.py is imported by 40 modules"
        )

    repo, a = repo_with_tests(tmp_path)
    b = commit(repo, "pkg/core.py", "x = 1\n")
    v = assess(repo, a, b, selector=hub)
    assert v.kind == "full" and "imported by 40" in v.why and v.outcome == ""


def test_selected_tests_that_were_cut_or_unparsed_make_the_combination_partial(tmp_path: Path):
    def cut(tree, base, **kw):
        t = Selected("tests/test_a.py", "direct", 1)
        return Selection(
            base=base, changed=["pkg/a.py"], tests=[t], cut=[Selected("tests/test_z.py", "far", 3)]
        )

    repo, a = repo_with_tests(tmp_path)
    b = commit(repo, "pkg/a.py", "def f():\n    return 9\n")
    v = assess(repo, a, b, selector=cut)
    assert v.kind == "selected" and v.outcome == "partial"  # a fallback is never a silent pass

    def clean(tree, base, **kw):
        t = Selected("tests/test_a.py", "direct", 1)
        return Selection(base=base, changed=["pkg/a.py"], tests=[t])

    assert assess(repo, a, b, selector=clean).outcome == "passed"


def test_git_unable_to_say_what_changed_is_unavailable_never_standing(tmp_path: Path):
    repo, a = repo_with_tests(tmp_path)
    v = assess(repo, a, "0" * 40)
    assert v.kind == "unavailable" and v.outcome == ""
    v = assess(repo, "", a)
    assert v.kind == "unavailable"


def test_the_same_sha_stands_trivially(tmp_path: Path):
    repo, a = repo_with_tests(tmp_path)
    assert assess(repo, a, a).kind == "stands"


def test_a_selector_that_cannot_answer_is_unavailable(tmp_path: Path):
    repo, a = repo_with_tests(tmp_path)
    b = commit(repo, "pkg/a.py", "def f():\n    return 7\n")
    v = assess(repo, a, b, selector=lambda tree, base, **kw: None)
    assert v.kind == "unavailable"


def _phase(passed_sha: str | None):
    log = Log()
    log.add("phase.added", "P1", "lead", T0, title="p", body="")
    log.add("task.added", "T1", "lead", T0 + 1, title="t", body="", parent="P1")
    log.add("worktree.merged", "T1", "a", T0 + 100, sha="x", kind="task")
    ev = {"note": "ci run --full"}
    if passed_sha:
        ev["sha"] = passed_sha
    log.add("cadence.ran", "integration_tests", "a", T0 + 50, result="0", evidence=ev)
    return list(log.events), fold(list(log.events), strict=False)


def test_a_pass_older_than_the_merge_still_closes_the_phase_when_the_delta_stands():
    events, st = _phase("aaa")
    assert PS.missing(events, st, "P1")  # no revalidation: refused as before
    assert PS.missing(events, st, "P1", stands=lambda sha: "unchanged by delta") == ""


def test_a_delta_that_does_not_stand_keeps_the_phase_open():
    events, st = _phase("aaa")
    assert PS.missing(events, st, "P1", stands=lambda sha: "") != ""


def test_a_pass_that_recorded_no_sha_cannot_be_revalidated():
    events, st = _phase(None)
    called = []
    assert PS.missing(events, st, "P1", stands=lambda sha: called.append(sha) or "x") != ""
    assert called == []


class _Log:
    def __init__(self):
        self.rows = []

    def append(self, kind, subject, data):
        self.rows.append((kind, subject, data))


def test_the_complete_hook_accepts_a_standing_pass_and_records_the_revalidation(tmp_path: Path):
    from ddflow.api.lifecycle.complete import checks

    repo, a = repo_with_tests(tmp_path)
    b = commit(repo, "docs/x.md", "x\n")
    log = _Log()
    note = checks._stands(repo, Config(), log)(a)
    assert note == "unchanged by delta"
    ((kind, subject, data),) = log.rows
    assert (kind, subject) == ("cadence.ran", "integration_tests")
    assert data["evidence"]["sha"] == b and data["evidence"]["from"] == a


def test_the_complete_hook_refuses_when_the_delta_reaches_a_test(tmp_path: Path):
    from ddflow.api.lifecycle.complete import checks

    repo, a = repo_with_tests(tmp_path)
    commit(repo, "pkg/a.py", "def f():\n    return 5\n")
    log = _Log()
    assert checks._stands(repo, Config(), log)(a) == ""
    assert log.rows == []  # nothing recorded: no pass was kept


def test_a_rename_reaches_the_tests_that_import_the_old_path(tmp_path: Path):
    repo, a = repo_with_tests(tmp_path)
    git(repo, "mv", "pkg/a.py", "pkg/renamed.py")
    git(repo, "commit", "-q", "-m", "rename")
    b = git(repo, "rev-parse", "HEAD")
    v = assess(repo, a, b)
    assert "pkg/a.py" in v.delta and "pkg/renamed.py" in v.delta
    assert v.kind in ("selected", "full")  # never 'stands': test_a imports the old module


def test_a_document_a_test_reads_is_not_dropped_from_the_delta(tmp_path: Path):
    repo, a = repo_with_tests(tmp_path)
    (repo / "tests" / "test_doc.py").write_text(
        "from pathlib import Path\n\n\ndef test_n():\n    assert Path('docs/n.md').read_text()\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "test reading a doc")
    a = git(repo, "rev-parse", "HEAD")
    b = commit(repo, "docs/n.md", "changed\n")
    seen = {}

    def spy(tree, base, **kw):
        seen["changed"] = kw["changed"]
        return Selection(base=base, changed=kw["changed"])

    assess(repo, a, b, selector=spy)
    assert seen["changed"] == ["docs/n.md"]  # the selector, not a glob, decides what a doc reaches
