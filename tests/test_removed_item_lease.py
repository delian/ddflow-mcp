"""An item removed in one clone while another clone holds its lease is not silent (B71dedb76d2).

Bob removes T2 while alice, in another clone, holds a live lease and has a commit on
`ddflow/T2`. After alice pulls, every command used to say the wrong thing: `heartbeat`
renewed a removed item, `merge` said only "no such item", `cleanup` called the branch
LEAVE ALONE, `doctor` and `status` were silent. Her finished branch was unreachable by any
ddflow command and nobody was told. A lease is evidence of intent and is never stolen, so
the fix makes the stranding VISIBLE and names the git commands.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from helpers import git_raw as _git

from ddflow.infra import log as L
from tests.conftest import run_cli


@pytest.fixture(autouse=True)
def _no_version_stamp(monkeypatch):
    monkeypatch.setattr(L.EventLog, "stamp", False)


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(dest, "config", k, v)
    return dest


def _commit_log(repo: Path, msg: str) -> None:
    _git(repo, "add", ".ddflow/events")
    _git(repo, "commit", "-qm", msg, "--no-verify")


def _pull(repo: Path) -> None:
    _git(repo, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")


def _two_clones(tmp_path: Path, *, alice_claims: bool) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    a = _clone(remote, tmp_path / "a")
    (a / ".gitattributes").write_text(".ddflow/events/*.jsonl merge=union\n")
    _git(a, "add", ".gitattributes")
    _git(a, "commit", "-qm", "attrs", "--no-verify")
    for t in ("T1", "T2"):
        assert (
            run_cli(a, "task", "add", t, "--title", t, "--globs", f"src/{t}.py", agent="alice")[0]
            == 0
        )
    _commit_log(a, "tasks")
    _git(a, "push", "-q", "origin", "main")
    b = _clone(remote, tmp_path / "b")
    if alice_claims:
        assert run_cli(a, "claim", "T2", agent="alice")[0] == 0
        tree = a / ".ddflow" / "worktrees" / "T2"
        (tree / "x.md").write_text("alice's work\n")
        _git(tree, "add", "-A")
        _git(tree, "commit", "-qm", "alice work", "--no-verify")
    assert run_cli(b, "remove", "T2", "--reason", "obsolete", agent="bob")[0] == 0
    _commit_log(b, "bob removes T2")
    _git(b, "push", "-q", "origin", "main")
    if alice_claims:
        _commit_log(a, "alice log")
    _pull(a)
    if alice_claims:
        # Bob now sees alice's live lease on the item he removed, though the branch is
        # alice's alone: nothing is stranded in HIS clone.
        _git(a, "push", "-q", "origin", "main")
        _pull(b)
    return a, b


@pytest.fixture
def stranded(tmp_path: Path) -> Path:
    return _two_clones(tmp_path, alice_claims=True)[0]


def test_doctor_names_the_removed_item_holder_branch_and_remedy(stranded):
    code, out, err = run_cli(stranded, "doctor", agent="alice")
    text = out + err
    assert code == 1, text
    assert "PROBLEM" in text and "T2" in text and "alice" in text and "ddflow/T2" in text, text
    assert "ddflow release T2" in text, text
    assert "git branch -m ddflow/T2 keep/T2" in text and "git branch -D ddflow/T2" in text, text


def test_heartbeat_does_not_renew_a_removed_item(stranded):
    code, out, err = run_cli(stranded, "heartbeat", "T2", agent="alice")
    text = out + err
    assert code == 3, text
    assert "removed" in text and "ddflow/T2" in text, text


@pytest.mark.parametrize("verb", ["merge", "complete"])
def test_merge_and_complete_say_why_instead_of_no_such_item(stranded, verb):
    code, out, err = run_cli(stranded, verb, "T2", agent="alice")
    text = out + err
    assert code != 0, text
    assert "removed" in text and "ddflow/T2" in text and "alice" in text, text


def test_cleanup_lists_the_branch_instead_of_leaving_it_alone(stranded):
    _, out, err = run_cli(stranded, "cleanup", agent="alice")
    text = out + err
    assert "removed from the queue, lease still held by alice" in text, text
    assert "ddflow release T2" in text and "git branch -D ddflow/T2" in text, text
    assert "LEAVE ALONE" not in text, text


def test_release_still_works_and_clears_the_problem(stranded):
    assert run_cli(stranded, "release", "T2", agent="alice")[0] == 0
    _, out, err = run_cli(stranded, "doctor", agent="alice")
    assert "ddflow release T2" not in out + err, out + err


def test_a_removed_item_without_a_live_lease_is_no_problem(tmp_path):
    a, _b = _two_clones(tmp_path, alice_claims=False)
    _, out, err = run_cli(a, "doctor", agent="alice")
    assert "ddflow release T2" not in out + err and "keep/T2" not in out + err, out + err


def test_the_clone_that_removed_it_has_no_problem(tmp_path):
    """Bob sees alice's live lease on T2 but has no branch or tree for it: nothing is
    stranded in his clone (the pull brought her claim in, asserted first)."""
    _a, b = _two_clones(tmp_path, alice_claims=True)
    shard = (b / ".ddflow" / "events" / "alice.jsonl").read_text()
    assert "lease.acquired" in shard, shard
    _, out, err = run_cli(b, "doctor", agent="bob")
    assert "ddflow release T2" not in out + err, out + err
