"""[flow].claims = "remote": a claim also takes refs/ddflow/claims/<id> on the remote (B192).

Two clones that cannot see each other's log can both claim one item locally. With the
remote lock the second is refused while online, naming the holder; releasing frees it; a
lapsed claim is replaceable; an unreachable remote refuses (never a silent local claim).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from conftest import run_cli

import ddflow.api.lifecycle as A
from ddflow.config import Config
from ddflow.infra import claimref as CR


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def clones(repo: Path, tmp_path: Path):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text('[flow]\nclaims = "remote"\n')
    run_cli(repo, "task", "add", "T1", "--title", "one", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--title", "two", "--globs", "b.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "queue")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "HEAD")
    other = tmp_path / "clone-b"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    # The same queue as a second machine would have it (event shards are per-clone here).
    run_cli(other, "init")
    (other / ".ddflow" / "config.toml").write_text('[flow]\nclaims = "remote"\n')
    run_cli(other, "task", "add", "T1", "--title", "one", "--globs", "a.py")
    run_cli(other, "task", "add", "T2", "--title", "two", "--globs", "b.py")
    return repo, other, bare


def _refs(bare):
    return _git(bare, "for-each-ref", "refs/ddflow/claims/").splitlines()


def test_the_second_clone_is_refused_and_told_who_holds_it(clones):
    a, b, bare = clones
    assert A.claim(a, "T1", no_worktree=True, agent="agent-a").ok
    assert len(_refs(bare)) == 1
    out = A.claim(b, "T1", no_worktree=True, agent="agent-b")
    assert out.exit == 3 and "agent-a" in out.reason, out.reason


def test_release_frees_the_remote_claim(clones):
    a, b, bare = clones
    assert A.claim(a, "T1", no_worktree=True, agent="agent-a").ok
    assert A.release(a, "T1", agent="agent-a").ok
    assert _refs(bare) == []
    assert A.claim(b, "T1", no_worktree=True, agent="agent-b").ok


def test_a_lapsed_remote_claim_is_replaced_by_compare_and_swap(clones):
    a, b, _bare = clones
    cfg = Config.load(a)
    assert CR.take(a, cfg.flow.remote, "T1", "ghost", time.time() - 10).ok
    assert A.claim(b, "T1", no_worktree=True, agent="agent-b").ok


def test_an_unreachable_remote_refuses_instead_of_claiming_locally(clones):
    a, _b, bare = clones
    _git(a, "remote", "set-url", "origin", str(bare) + "-gone")
    out = A.claim(a, "T1", no_worktree=True, agent="agent-a")
    assert not out.ok and "remote" in out.reason, out.reason
    assert run_cli(a, "--json", "show", "T1")[1].count('"holder"') == 0


def test_local_is_the_default_and_touches_no_remote(repo, tmp_path):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    assert A.claim(repo, "T1", no_worktree=True, agent="agent-a").ok


def test_ref_names_are_valid_and_distinct():
    assert CR.ref_name("B-x.1") != CR.ref_name("B-x_1")
    assert " " not in CR.ref_name("a b") and ".." not in CR.ref_name("a..b")


def test_a_heartbeat_extends_the_remote_claim_so_it_is_not_stolen(clones):
    a, b, _bare = clones
    remote = Config.load(a).flow.remote
    assert CR.take(a, remote, "T1", "agent-a", time.time() + 1).ok
    assert CR.renew(a, remote, "T1", "agent-a", time.time() + 3600).ok
    time.sleep(2)
    got = CR.take(b, remote, "T1", "agent-b", time.time() + 60)
    assert got.status == "held" and got.holder == "agent-a"


def test_a_stalled_holder_releasing_does_not_delete_its_successors_claim(clones):
    a, b, bare = clones
    remote = Config.load(a).flow.remote
    assert CR.take(a, remote, "T1", "ghost", time.time() - 10).ok  # lapsed
    assert CR.take(b, remote, "T1", "agent-b", time.time() + 600).ok  # b replaced it
    assert CR.drop(a, remote, "T1", "ghost").status == "held"  # the ghost's late release
    assert len(_refs(bare)) == 1
    assert CR.drop(b, remote, "T1", "agent-b").ok and _refs(bare) == []


def test_ref_names_are_injective_for_non_ascii_ids():
    assert CR.ref_name("€") != CR.ref_name(" ac")
    assert CR.ref_name("Ā") != CR.ref_name("\x100")


def test_an_unreadable_existing_claim_is_unavailable_not_lapsed(clones, monkeypatch):
    a, b, bare = clones
    remote = Config.load(a).flow.remote
    assert CR.take(a, remote, "T1", "agent-a", time.time() + 600).ok
    monkeypatch.setattr(CR, "_read", lambda *args: None)
    assert CR.take(b, remote, "T1", "agent-b", time.time() + 600).status == "unavailable"
    assert len(_refs(bare)) == 1


def test_a_claim_the_local_checks_refuse_gives_the_remote_ref_back(clones):
    a, _b, bare = clones
    run_cli(a, "task", "add", "T3", "--title", "clash", "--globs", "a.py")  # overlaps T1
    assert A.claim(a, "T1", no_worktree=True, agent="agent-a").ok
    out = A.claim(a, "T3", no_worktree=True, agent="agent-a2")
    assert out.exit == 3, out.reason
    names = [line.split()[-1] for line in _refs(bare)]
    assert names == [CR.ref_name("T1")], names
