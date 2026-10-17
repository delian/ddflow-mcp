"""`ddflow chore commit` (decision D-chore-class): files in `[chore].globs` are committed,
merged and COMPLETED in one command with no reviewer, on a person's approval of the list.

Before it, a chore took ten commands and then could not complete ("reviewer independence
not satisfied: no reviewer ran"), so it was abandoned instead.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    return repo


def _state(repo):
    return fold(EventLog(repo, "probe").read_all(), strict=False)


def _approve(repo):
    code, _out, err = run_cli(repo, "chore", "approve")
    assert code == 0, err


def _chore_items(repo):
    return [i for i in _state(repo).items.values() if "chore" in i.tags]


def test_a_chore_lands_and_completes_with_no_reviewer(proj):
    _approve(proj)
    (proj / ".mailmap").write_text("A <a@x> <b@x>\n")
    code, out, err = run_cli(proj, "--json", "chore", "commit", ".mailmap", "-m", "mailmap")
    assert code == 0, (out, err)
    body = json.loads(out)
    item = _state(proj).items[body["item"]]
    assert item.state == "done"
    assert "merge" not in body["skipped_gates"] and "critic" in body["skipped_gates"]
    assert _git(proj, "show", "HEAD:.mailmap") == "A <a@x> <b@x>\n"
    assert _git(proj, "status", "--porcelain", "--", ".mailmap") == "", "primary copy left behind"
    skipped = item.gates["critic"]
    assert skipped.outcome == "skipped" and "chore:" in skipped.reason
    assert "standing approval" in skipped.reason


def test_a_modified_tracked_chore_file_is_restored_in_the_primary(proj):
    (proj / "DDFLOW.md").write_text("one\n")
    _git(proj, "add", "DDFLOW.md")
    _git(proj, "commit", "-qm", "doc")
    _approve(proj)
    (proj / "DDFLOW.md").write_text("two\n")
    code, out, err = run_cli(proj, "chore", "commit", "DDFLOW.md", "-m", "edit doc")
    assert code == 0, (out, err)
    assert (proj / "DDFLOW.md").read_text() == "two\n"
    assert _git(proj, "status", "--porcelain", "--", "DDFLOW.md") == ""


def test_code_is_refused_and_leaves_no_task(proj):
    _approve(proj)
    (proj / "app.py").write_text("print(1)\n")
    (proj / ".mailmap").write_text("A <a@x> <b@x>\n")
    code, out, err = run_cli(proj, "chore", "commit", ".mailmap", "app.py", "-m", "mixed")
    assert code == 3 and "app.py" in out + err
    assert _chore_items(proj) == [], "a refused chore must not create a task"
    assert (proj / "app.py").exists() and (proj / ".mailmap").exists()


def test_the_list_must_be_approved_by_a_person(proj):
    (proj / ".mailmap").write_text("A <a@x> <b@x>\n")
    code, out, err = run_cli(proj, "chore", "commit", ".mailmap", "-m", "mailmap")
    assert code == 3 and "chore approve" in out + err
    assert _chore_items(proj) == []


def test_an_agent_identity_cannot_grant_the_approval(proj):
    code, out, err = run_cli(proj, "--agent", "bot", "chore", "approve")
    assert code == 3 and "agent identity" in out + err
    assert "chore:class" not in _state(proj).approvals


def test_editing_the_list_withdraws_the_approval(proj):
    _approve(proj)
    code, _o, err = run_cli(proj, "config", "--set", "chore.globs", '["**"]')
    assert code == 0, err
    (proj / "app.py").write_text("print(1)\n")
    code, out, err = run_cli(proj, "--agent", "bot", "chore", "commit", "app.py", "-m", "code")
    assert code == 3 and "changed since it was approved" in out + err


def test_the_chore_tag_alone_does_not_excuse_a_reviewer(proj):
    """Regression: the exemption is judged on the paths that changed, not on the tag."""
    _approve(proj)
    (proj / "app.py").write_text("print(1)\n")
    run_cli(proj, "task", "add", "T1", "--title", "sneaky", "--globs", "app.py", "--tags", "chore")
    run_cli(proj, "claim", "T1")
    wt = proj / ".ddflow" / "worktrees" / "T1"
    (wt / "app.py").write_text("print(1)\n")
    _git(wt, "add", "app.py")
    _git(wt, "commit", "-qm", "code")
    run_cli(proj, "merge", "T1", "--model", "claude-x")
    code, _out, err = run_cli(proj, "complete", "T1", "--model", "claude-x")
    assert code != 0 and "reviewer independence" in err


def test_a_staged_new_chore_file_is_cleared_from_the_primary(proj):
    """A new file already `git add`-ed is in the index but not in HEAD: it must still go,
    or the merge refuses over the local copy."""
    _approve(proj)
    (proj / ".mailmap").write_text("A <a@x> <b@x>\n")
    _git(proj, "add", ".mailmap")
    code, out, err = run_cli(proj, "chore", "commit", ".mailmap", "-m", "staged")
    assert code == 0, (out, err)
    assert _git(proj, "show", "HEAD:.mailmap") == "A <a@x> <b@x>\n"
    assert _git(proj, "status", "--porcelain", "--", ".mailmap") == ""


def test_only_a_task_can_be_a_chore(proj):
    from ddflow.config import Config
    from ddflow.services import chore as CO

    _approve(proj)
    st = _state(proj)
    run_cli(proj, "task", "add", "T1", "--title", "t", "--globs", ".mailmap", "--tags", "chore")
    it = _state(proj).items["T1"]
    cfg = Config.load(proj)
    assert CO.exemption(_state(proj), cfg, it, [".mailmap"])[0] is not None
    it.kind = "phase"
    assert CO.exemption(st, cfg, it, [".mailmap"]) == (None, "")


def test_editing_the_skipped_gates_withdraws_the_approval_too(proj):
    _approve(proj)
    code, _o, err = run_cli(proj, "config", "--set", "chore.skip_gates", '["merge"]')
    assert code == 0, err
    (proj / ".mailmap").write_text("A <a@x> <b@x>\n")
    code, out, err = run_cli(proj, "--agent", "bot", "chore", "commit", ".mailmap", "-m", "m")
    assert code == 3 and "changed since it was approved" in out + err


def test_a_non_ascii_path_and_a_deleted_directory_are_landed(proj):
    (proj / "docs" / "ddflow" / "decisions").mkdir(parents=True)
    (proj / "docs" / "ddflow" / "decisions" / "gone.md").write_text("x\n")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "decision")
    _approve(proj)
    (proj / "docs" / "ddflow" / "decisions" / "é.md").write_text("y\n")
    code, out, err = run_cli(proj, "chore", "commit", "docs/ddflow/decisions/é.md", "-m", "utf8")
    assert code == 0, (out, err)
    assert _git(proj, "show", "HEAD:docs/ddflow/decisions/é.md") == "y\n"
    (proj / "docs" / "ddflow" / "decisions" / "gone.md").unlink()
    code, out, err = run_cli(proj, "chore", "commit", "docs/ddflow/decisions/gone.md", "-m", "rm")
    assert code == 0, (out, err)


def test_a_tagged_task_outside_the_list_gets_a_warning_not_a_second_blocker(proj):
    from ddflow.config import Config
    from ddflow.services import chore as CO

    run_cli(proj, "task", "add", "T1", "--title", "t", "--globs", "app.py", "--tags", "chore")
    cfg = Config.load(proj)
    excused, note, warnings = CO.judge(_state(proj), cfg, _state(proj).items["T1"], ["app.py"])
    assert excused is None and note == "" and "not a chore" in warnings[0]


def test_a_rename_into_the_list_is_refused_before_a_task_exists(proj):
    """The source of a staged rename is outside the list and not named: refuse, rather
    than fail in the merge with the work half done."""
    (proj / "foo.py").write_text("x = 1\n")
    _git(proj, "add", "foo.py")
    _git(proj, "commit", "-qm", "code")
    _approve(proj)
    (proj / "docs" / "ddflow" / "decisions").mkdir(parents=True)
    _git(proj, "mv", "foo.py", "docs/ddflow/decisions/foo.py")
    code, out, err = run_cli(proj, "chore", "commit", "docs/ddflow/decisions/foo.py", "-m", "mv")
    assert code == 3 and "foo.py" in out + err
    assert _chore_items(proj) == []
