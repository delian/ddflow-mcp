"""A contested claim is shown by status, next and brief, and refuses merge (B2809f4213b).

Two clones claim one task offline as different agents. After they pull each other the fold
knows the claim is contested (B191), but only `show` and `doctor` said so: `status` listed
the task as plain 'In flight', `next` omitted it, `brief` said nothing, `merge` SUCCEEDED
for the losing claim, and after `resolve --keep` nobody told the loser.

Claims stay optimistic; the contest is surfaced and `ddflow resolve` settles it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import git_raw as _git

from ddflow.infra import log as L
from tests.conftest import run_cli

RESOLVE = "ddflow resolve T1 --keep"


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


@pytest.fixture
def clones(tmp_path: Path) -> tuple[Path, Path]:
    """alice (U1) and bob (U2) claim T1 offline; both then hold the other's events."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    a = _clone(remote, tmp_path / "a")
    (a / ".gitattributes").write_text(".ddflow/events/*.jsonl merge=union\n")
    _git(a, "add", ".gitattributes")
    _git(a, "commit", "-qm", "attrs", "--no-verify")
    assert (
        run_cli(a, "task", "add", "T1", "--title", "t", "--globs", "src/t.py", agent="seed")[0] == 0
    )
    _commit_log(a, "T1")
    _git(a, "push", "-q", "origin", "main")
    b = _clone(remote, tmp_path / "b")
    assert run_cli(b, "claim", "T1", agent="U2")[0] == 0
    # The loser's work: a commit on his own branch, which main does not have.
    wt = b / ".ddflow" / "worktrees" / "T1"
    (wt / "bob.txt").write_text("bob's work\n")
    _git(wt, "add", "bob.txt")
    _git(wt, "commit", "-qm", "bob's T1", "--no-verify")
    _commit_log(b, "b claim")
    _git(b, "push", "-q", "origin", "main")
    assert run_cli(a, "claim", "T1", agent="U1")[0] == 0
    _commit_log(a, "a claim")
    _pull(a)
    _git(a, "push", "-q", "origin", "main")
    _pull(b)
    return a, b


def _who(clones, which: str) -> tuple[Path, str]:
    return (clones[0], "U1") if which == "a" else (clones[1], "U2")


@pytest.mark.parametrize("which", ["a", "b"])
def test_status_lists_the_contest_not_plain_in_flight(clones, which):
    repo, agent = _who(clones, which)
    code, text, err = run_cli(repo, "status", agent=agent)
    assert code == 0, err
    assert "Contested" in text and "T1" in text, text
    assert f"{RESOLVE} <event-id|agent>" in text, text
    in_flight = text.split("In flight:")[1].split("\n\n")[0] if "In flight:" in text else ""
    assert "T1" not in in_flight, text
    code, out, err = run_cli(repo, "--json", "status", agent=agent)
    body = json.loads(out)
    assert [c["id"] for c in body["contested"]] == ["T1"], body
    assert "T1" not in [r["id"] for r in body["in_flight"]], body
    assert {c["holder"] for c in body["contested"][0]["claims"]} == {"U1", "U2"}, body


@pytest.mark.parametrize("which", ["a", "b"])
def test_next_and_brief_name_the_contest_and_its_command(clones, which):
    repo, agent = _who(clones, which)
    code, text, err = run_cli(repo, "next", agent=agent)
    assert "T1" in text and "contested" in text.lower(), text + err
    assert f"{RESOLVE} <event-id|agent>" in text, text
    code, text, err = run_cli(repo, "brief", agent=agent)
    assert code == 0, err
    assert "T1" in text and "contested" in text.lower(), text
    assert f"{RESOLVE} <event-id|agent>" in text, text


@pytest.mark.parametrize("which", ["a", "b"])
def test_merge_and_complete_are_refused_while_contested(clones, which):
    repo, agent = _who(clones, which)
    before = _git(repo, "rev-parse", "main")
    for verb in ("merge", "complete"):
        code, out, err = run_cli(repo, verb, "T1", agent=agent)
        assert code == 3, (verb, code, out, err)
        assert "contest" in (out + err).lower(), (verb, out, err)
    assert _git(repo, "rev-parse", "main") == before
    assert "item.completed" not in (repo / ".ddflow" / "events" / f"{agent}.jsonl").read_text()


def test_the_loser_is_told_after_resolve_and_the_winner_is_not(clones):
    a, b = clones
    code, out, err = run_cli(a, "resolve", "T1", "--keep", "U1", agent="U1")
    assert code == 0, out + err
    _commit_log(a, "resolved")
    _git(a, "push", "-q", "origin", "main")
    _pull(b)

    code, text, err = run_cli(b, "brief", agent="U2")
    assert code == 0, err
    assert "your claim on T1 was released: lost the contest to U1" in text, text
    assert "branch ddflow/T1 has 1 commit(s) not on main" in text, text
    assert "keep it with git or port it" in text, text

    code, text, err = run_cli(a, "brief", agent="U1")
    assert "was released" not in text and "lost the contest" not in text, text

    code, text, err = run_cli(b, "cleanup", agent="U2")
    assert "held by U1" not in text, text
    assert "your own" in text and "released" in text, text
