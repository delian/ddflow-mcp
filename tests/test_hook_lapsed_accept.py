"""The commit hook stands on a lease the committer's work is bound to (bug B8aa03801a0).

A claim made under an explicit `--agent` name is not the identity the hook derives from
the tree, and after the lease lapsed (a long suite, no heartbeat) the commit stayed
refused until a heartbeat. The hook now resolves the holder by the tree the lease made,
and accepts a lapsed lease nobody has taken over. Another agent's LIVE lease still refuses.
"""

from __future__ import annotations

import subprocess
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_hook_lapsed_lease import _later, _setup

from ddflow.infra.log import EventLog
from ddflow.services import enforce as E
from ddflow.services import leases as L


def _lapse(monkeypatch, cfg) -> None:
    _later(monkeypatch, cfg.lease.ttl_s + cfg.lease.grace_s + 600)


def test_an_explicit_agent_claim_commits_from_its_own_worktree_under_block(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg, holder="fleet-named")
    cfg.enforce.commit_without_lease = "block"
    monkeypatch.chdir(tree)
    assert E.check_commit(repo, cfg, agent="derived-from-the-tree") == (0, "")


def test_an_expired_untaken_lease_does_not_refuse_its_tree_under_block(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg, holder="fleet-named")
    cfg.enforce.commit_without_lease = "block"
    _lapse(monkeypatch, cfg)
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="derived-from-the-tree")
    assert code == 0, msg
    assert "LAPSED" in msg and "heartbeat" in msg, msg


def test_an_expired_untaken_lease_does_not_refuse_its_holder_elsewhere(repo, cfg, monkeypatch):
    _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    _lapse(monkeypatch, cfg)
    monkeypatch.chdir(repo)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "other.py").write_text("y = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/other.py"], check=True)
    assert E.check_commit(repo, cfg, agent="owner")[0] == 0
    assert E.check_commit(repo, cfg, agent="stranger")[0] == 1


def test_the_knob_narrows_or_restores_the_refusal(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    _lapse(monkeypatch, cfg)
    monkeypatch.chdir(tree)
    cfg.enforce.lapsed_lease = "holder"
    assert E.check_commit(repo, cfg, agent="derived")[0] == 1
    assert E.check_commit(repo, cfg, agent="owner")[0] == 0
    cfg.enforce.lapsed_lease = "refuse"
    assert E.check_commit(repo, cfg, agent="owner")[0] == 1


def test_another_agents_live_lease_still_refuses(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    from conftest import run_cli

    assert run_cli(repo, "task", "add", "T2", "--globs", "src/new.py")[0] == 0
    later = time.time() + cfg.lease.ttl_s + cfg.lease.grace_s + 600
    _lapse(monkeypatch, cfg)
    monkeypatch.setattr(L, "time", types.SimpleNamespace(time=lambda: later))
    L.acquire(EventLog(repo, "other"), cfg, "T2", holder="other", globs=["src/new.py"])
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1 and "ANOTHER agent" in msg, msg
