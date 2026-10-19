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
from helpers import make_event as _ev

from ddflow.core.model import fold
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
    # Even with a branch of the same name here (a claim bob lost, say): it is not alice's.
    _git(b, "branch", "ddflow/T2")
    _, out, err = run_cli(b, "doctor", agent="bob")
    assert "ddflow release T2" not in out + err, out + err


def _removed(*, lease_at: float):
    evs = [
        _ev("task.added", "T", 1, title="t"),
        _ev(
            "lease.acquired",
            "T",
            2,
            "alice",
            holder="alice",
            at=lease_at,
            ttl_s=100,
            branch="ddflow/T",
        ),
        _ev("task.removed", "T", 3, "bob", reason="obsolete"),
    ]
    return fold(evs)


def test_require_names_the_holder_only_while_the_lease_is_live():
    import time

    from ddflow.api.lifecycle._common import _require

    live = _require(_removed(lease_at=time.time()), "T", "worktree.merged", 0)
    assert live.exit == 3 and "alice" in live.reason and "ddflow/T" in live.reason
    lapsed = _require(_removed(lease_at=1000.0), "T", "worktree.merged", 0)
    assert "no such item" in lapsed.reason and "alice" not in lapsed.reason


def test_cleanup_lists_a_stranded_branch_whose_worktree_is_gone(stranded):
    tree = stranded / ".ddflow" / "worktrees" / "T2"
    _git(stranded, "worktree", "remove", "--force", str(tree))
    _, out, err = run_cli(stranded, "cleanup", agent="alice")
    text = out + err
    assert "lease still held by alice" in text and "git branch -D ddflow/T2" in text, text
    assert "LEAVE ALONE" not in text, text
    assert run_cli(stranded, "doctor", agent="alice")[0] == 1


def test_require_judges_liveness_by_the_configured_grace():
    """A lease past its TTL but inside the repo's `[lease].grace_s` is still held."""
    import time

    from ddflow.api.lifecycle._common import _require

    st = _removed(lease_at=time.time() - 150)  # ttl 100: lapsed by 50s
    assert "no such item" in _require(st, "T", "worktree.merged", 0).reason
    assert "alice" in _require(st, "T", "worktree.merged", 500).reason


@pytest.fixture
def remover(tmp_path: Path) -> Path:
    """Bob's clone: he removed T2, and alice's live lease on it arrived by pull."""
    return _two_clones(tmp_path, alice_claims=True)[1]


@pytest.mark.parametrize("verb", ["merge", "complete", "heartbeat"])
def test_the_remover_clone_is_not_told_a_remedy_it_would_refuse(remover, verb):
    code, out, err = run_cli(remover, verb, "T2", agent="bob")
    text = out + err
    assert code != 0 and "alice" in text and "another clone" in text, text
    assert "ddflow release T2" not in text and "git branch -D" not in text, text
    assert run_cli(remover, "release", "T2", agent="bob")[0] != 0, "the premise: release refuses"


def test_status_lists_the_stranded_item_in_both_clones(tmp_path):
    import json

    a, b = _two_clones(tmp_path, alice_claims=True)
    for clone, agent, release in ((a, "alice", True), (b, "bob", False)):
        _, out, _ = run_cli(clone, "--json", "status", agent=agent)
        rows = json.loads(out)["stranded"]
        assert [r["item"] for r in rows] == ["T2"] and rows[0]["holder"] == "alice", rows
        assert ("ddflow release T2" in rows[0]["text"]) is release, rows
        _, text, err = run_cli(clone, "status", agent=agent)
        assert "Removed, lease still held" in text + err and "T2" in text + err, text


def test_status_has_no_stranded_list_without_one(tmp_path):
    import json

    a, _b = _two_clones(tmp_path, alice_claims=False)
    _, out, _ = run_cli(a, "--json", "status", agent="alice")
    assert "stranded" not in json.loads(out)
