"""The commit hook names a LAPSED lease that was the committer's, and says to heartbeat it.

Bug B3e050cb66a: on B-local-config-surfaces the hook warned "You hold: (no live lease)"
and prescribed `ddflow claim` for a commit made in the item's own tree by its own holder.
The lease had lapsed -- no clock-moving heartbeat for 46 minutes -- and nobody had taken it
over. `check_commit` looked only at live leases, so it could not say so, and the warning
was filed as an identity bug (B5fde61b8a9, refuted by R2440394451).

A lapsed lease is still not a pass: the outcome is unchanged. Only the message changes.
"""

from __future__ import annotations

import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra import worktree as W
from ddflow.infra.log import EventLog
from ddflow.services import enforce as E
from ddflow.services import leases as L


def _setup(repo: Path, cfg, holder: str = "owner", *, lease: bool = True) -> Path:
    """T1 claimed by ``holder`` with its own tree; ``src/new.py`` staged in that tree."""
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "task", "add", "T1", "--globs", "src/*")[0] == 0
    wt = W.create(repo, cfg, "T1")
    if lease:
        L.acquire(
            EventLog(repo, holder),
            cfg,
            "T1",
            holder=holder,
            worktree=W.store_path(repo, wt.path),
            branch=wt.branch,
            globs=["src/*"],
        )
    (wt.path / "src").mkdir(exist_ok=True)
    (wt.path / "src" / "new.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(wt.path), "add", "-A"], check=True)
    return wt.path


def _later(monkeypatch, seconds: float) -> None:
    """Move the hook's clock forward, past the lease's TTL and grace."""
    now = time.time() + seconds
    monkeypatch.setattr(E, "time", types.SimpleNamespace(time=lambda: now))


def test_a_lapsed_lease_bound_to_this_tree_is_named_with_the_heartbeat_remedy(
    repo, cfg, monkeypatch
):
    tree = _setup(repo, cfg)
    _later(monkeypatch, cfg.lease.ttl_s + cfg.lease.grace_s + 600)
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="derived-from-the-tree")
    assert code == 0  # warn policy: still allowed, still warned
    assert "lapsed" in msg, msg
    assert "ddflow --agent owner heartbeat T1" in msg, msg
    assert "(no live lease)" not in msg, msg
    assert "ANOTHER agent" not in msg, msg


def test_a_lapsed_lease_is_still_refused_under_block(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    _later(monkeypatch, cfg.lease.ttl_s + cfg.lease.grace_s + 600)
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="derived-from-the-tree")
    assert code == 1, msg
    assert "ddflow --agent owner heartbeat T1" in msg, msg


def test_my_own_lapsed_lease_from_another_tree_is_named(repo, cfg, monkeypatch):
    _setup(repo, cfg)
    _later(monkeypatch, cfg.lease.ttl_s + cfg.lease.grace_s + 600)
    monkeypatch.chdir(repo)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "other.py").write_text("y = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/other.py"], check=True)
    _code, msg = E.check_commit(repo, cfg, agent="owner")
    assert "ddflow --agent owner heartbeat T1" in msg, msg


def test_someone_elses_lapsed_lease_in_another_tree_is_not_offered(repo, cfg, monkeypatch):
    """Standing in the primary as a different agent: that lapsed lease is not yours to
    renew -- the hint would resurrect a claim its holder abandoned."""
    _setup(repo, cfg)
    _later(monkeypatch, cfg.lease.ttl_s + cfg.lease.grace_s + 600)
    monkeypatch.chdir(repo)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "other.py").write_text("y = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/other.py"], check=True)
    code, msg = E.check_commit(repo, cfg, agent="stranger")
    assert code == 0 and msg, "an unclaimed path must still warn"
    assert "heartbeat" not in msg, msg
    assert "lapsed" not in msg, msg
    assert "(no live lease)" in msg, msg


def test_a_genuinely_unclaimed_edit_still_warns_with_the_claim_remedy(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg, lease=False)
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="derived-from-the-tree")
    assert code == 0 and msg, "an unclaimed edit was waved through"
    assert "(no live lease)" in msg, msg
    assert "ddflow claim <ID>" in msg, msg
    assert "lapsed" not in msg and "heartbeat" not in msg, msg


def test_a_live_lease_held_by_the_declared_identity_covers_a_commit_elsewhere(
    repo, cfg, monkeypatch
):
    """The identity direction of B5fde61b8a9: the holder's declared name (DDFLOW_AGENT /
    --agent, resolved into cfg.agent.id by the hooks command) covers a commit made in a
    tree the lease is not bound to; any other name is warned that the path is taken."""
    _setup(repo, cfg)
    monkeypatch.chdir(repo)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "other.py").write_text("y = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/other.py"], check=True)
    assert E.check_commit(repo, cfg, agent="owner") == (0, "")
    code, msg = E.check_commit(repo, cfg, agent="stranger")
    assert code == 0 and "ANOTHER agent" in msg, msg


def test_the_hook_reads_ddflow_agent_from_the_environment(repo, cfg):
    """End to end through `ddflow hooks check-commit`, as git runs it."""
    _setup(repo, cfg)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "other.py").write_text("y = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/other.py"], check=True)
    root = str(Path(__file__).resolve().parents[1])

    def hook(agent: str) -> str:
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": root, "DDFLOW_AGENT": agent}
        p = subprocess.run(
            [sys.executable, "-m", "ddflow", "--repo", str(repo), "hooks", "check-commit"],
            cwd=repo,
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        return p.stdout + p.stderr

    assert "not covered" not in hook("owner")
    assert "ANOTHER agent" in hook("stranger")


def test_a_lapsed_lease_matched_by_tree_alone_offers_takeover_too(repo, cfg, monkeypatch):
    """Matched only by the tree, the committer may be someone sent to take the abandoned
    work over, not its holder: the heartbeat is offered to the holder by name, and the
    takeover alongside it -- never the bare presumption that this is the holder."""
    tree = _setup(repo, cfg)
    _later(monkeypatch, cfg.lease.ttl_s + cfg.lease.grace_s + 600)
    monkeypatch.chdir(tree)
    _code, msg = E.check_commit(repo, cfg, agent="stranger")
    assert "If you are owner" in msg, msg
    assert "ddflow --agent owner heartbeat T1" in msg, msg
    assert "ddflow recover --item T1" in msg, msg
    assert "ddflow release T1" in msg, msg
    assert "--force" not in msg, msg
    assert "Your lease" not in msg, msg


def test_the_offered_remedies_are_the_ones_that_work(repo, cfg, monkeypatch):
    """Each remedy the hook offers for a lapsed lease does what it says: the holder's
    heartbeat revives it; a bare claim by anyone else is refused (default reclaim
    policy), and release-then-claim -- the path offered -- takes it over with every
    claim check still applied."""
    _setup(repo, cfg)
    assert cfg.lease.reclaim_policy == "report"
    later = time.time() + cfg.lease.ttl_s + cfg.lease.grace_s + 600
    monkeypatch.setattr(L, "time", types.SimpleNamespace(time=lambda: later))

    def lease():
        return fold(EventLog(repo, "r").read_all(), strict=False).items["T1"].lease

    assert lease().expired(later, cfg.lease.grace_s)
    assert L.renew(EventLog(repo, "owner"), "T1"), "the holder's heartbeat was refused"
    assert lease().holder == "owner"
    assert not lease().expired(later, cfg.lease.grace_s), "the heartbeat did not revive it"

    # Lapse it again, and take it over the way the hook says.
    later += cfg.lease.ttl_s + cfg.lease.grace_s + 600
    stranger = EventLog(repo, "stranger")
    with pytest.raises(L.LeaseError, match="EXPIRED"):
        L.acquire(stranger, cfg, "T1", holder="stranger", globs=["src/*"])
    assert L.release(stranger, "T1", note="salvaged")
    L.acquire(stranger, cfg, "T1", holder="stranger", globs=["src/*"])
    assert lease().holder == "stranger"


def test_a_lapsed_lease_is_not_offered_for_paths_another_agent_now_holds(repo, cfg, monkeypatch):
    """Reviving the lapsed lease over a path someone else has since leased is the race
    the STOP text warns against, so only STOP is said for that path."""
    tree = _setup(repo, cfg)
    assert run_cli(repo, "task", "add", "T2", "--globs", "src/new.py")[0] == 0
    later = time.time() + cfg.lease.ttl_s + cfg.lease.grace_s + 600
    _later(monkeypatch, cfg.lease.ttl_s + cfg.lease.grace_s + 600)
    monkeypatch.setattr(L, "time", types.SimpleNamespace(time=lambda: later))
    L.acquire(EventLog(repo, "other"), cfg, "T2", holder="other", globs=["src/new.py"])
    monkeypatch.chdir(tree)
    _code, msg = E.check_commit(repo, cfg, agent="owner")
    assert "ANOTHER agent" in msg, msg
    assert "heartbeat" not in msg, msg


def test_a_lapsed_lease_straddling_another_agents_paths_is_not_offered(repo, cfg, monkeypatch):
    """Per lease, not per path: the heartbeat revives every glob of the lapsed lease, so
    one overlapping another agent's live lease anywhere is not offered -- even when the
    staged path itself is free."""
    tree = _setup(repo, cfg)
    assert run_cli(repo, "task", "add", "T2", "--globs", "src/taken.py")[0] == 0
    later = time.time() + cfg.lease.ttl_s + cfg.lease.grace_s + 600
    _later(monkeypatch, cfg.lease.ttl_s + cfg.lease.grace_s + 600)
    monkeypatch.setattr(L, "time", types.SimpleNamespace(time=lambda: later))
    L.acquire(EventLog(repo, "other"), cfg, "T2", holder="other", globs=["src/taken.py"])
    monkeypatch.chdir(tree)
    _code, msg = E.check_commit(repo, cfg, agent="owner")  # stages only src/new.py
    assert "src/new.py" in msg and "ANOTHER agent" not in msg, msg
    assert "heartbeat" not in msg, msg


def test_a_derived_identity_is_told_how_the_holder_would_be_covered(repo, cfg, monkeypatch):
    """B9aeb141b9a: an agent whose identity was declared only to its claim (an MCP
    `as_agent`) commits outside its item's tree; the hook, told nothing, derives a name
    from the tree and says ANOTHER agent holds the paths. It cannot know better -- it
    says how it named the committer and what the holder would do."""
    _setup(repo, cfg)
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    cfg.agent.id = ""
    monkeypatch.chdir(repo)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "other.py").write_text("y = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/other.py"], check=True)
    _code, msg = E.check_commit(repo, cfg)
    assert "ANOTHER agent" in msg, msg
    assert "Nothing declared who is committing" in msg, msg
    assert "DDFLOW_AGENT=owner git commit" in msg, msg
    assert "commit in " in msg and "T1" in msg, msg


def test_a_declared_identity_gets_no_derived_identity_hint(repo, cfg, monkeypatch):
    """A committer that said who it is, and is not the holder, is another agent: the
    STOP stands on its own."""
    _setup(repo, cfg)
    monkeypatch.chdir(repo)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "other.py").write_text("y = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/other.py"], check=True)
    _code, msg = E.check_commit(repo, cfg, agent="stranger")
    assert "ANOTHER agent" in msg, msg
    assert "Nothing declared" not in msg and "DDFLOW_AGENT=" not in msg, msg


def test_the_derived_identity_hint_reaches_the_real_hook(repo, cfg):
    """Through `ddflow hooks check-commit` as git runs it, with nothing declared: the
    command resolves the derived name INTO cfg.agent.id, so the hint must key on how
    the name was found, not on whether one is set."""
    _setup(repo, cfg)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "other.py").write_text("y = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/other.py"], check=True)
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "hooks", "check-commit"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    out = p.stdout + p.stderr
    assert "ANOTHER agent" in out, out
    assert "DDFLOW_AGENT=owner git commit" in out, out
