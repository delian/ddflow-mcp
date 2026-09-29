"""A lease claimed under the pre-B190 derived id is still this clone's after the upgrade.

B190 gave derived ids a per-clone suffix: `{host}-{tree}` became `{host}-{tree}-{hex6}`.
A lease claimed before the upgrade kept the bare id as its holder, and every holder
comparison afterwards -- renew, release, complete, merge, the gate lease-keeper, the
scheduler -- saw someone else's lease: heartbeat said "no lease held", complete refused,
and the agent's own item was offered to it as blocked by a stranger.

The fix re-homes such a lease to the suffixed id, once, on the record. Only a lease the
bare id acquired BEFORE this clone had a suffix qualifies: after that moment this clone
derives the suffixed id, so a bare-id claim made later is another clone's.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.core.model import fold
from ddflow.infra import log as L


def _lease(repo: Path, item: str):
    return fold(L.EventLog(repo, "reader").read_all(), strict=False).items[item].lease


def _bare(repo: Path) -> str:
    return f"{socket.gethostname().split('.')[0]}-{repo.name}"


def _derived(repo: Path) -> str:
    L._AGENT_ID_CACHE.clear()
    return L.default_agent_id(repo)


def _claimed_before_the_upgrade(repo: Path, monkeypatch) -> str:
    """T1 leased by the bare id while this clone had no suffix; the suffix comes after."""
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    monkeypatch.chdir(repo)
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")[0] == 0
    (repo / L.CLONE_ID_FILE).unlink(missing_ok=True)
    bare = _bare(repo)
    out = api.claim(repo, "T1", no_worktree=True, agent=bare)
    assert out.exit == 0, out.reason
    assert _lease(repo, "T1").holder == bare
    time.sleep(0.01)  # the suffix is created strictly after the claim
    return bare


def test_heartbeat_after_the_upgrade_renews_the_pre_upgrade_lease(repo, monkeypatch):
    bare = _claimed_before_the_upgrade(repo, monkeypatch)
    me = _derived(repo)
    assert me != bare and me.startswith(bare + "-"), me
    out = api.heartbeat(repo, "T1")
    assert out.exit == 0, out.reason
    assert _lease(repo, "T1").holder == me


def test_the_re_homed_lease_keeps_what_the_claim_recorded(repo, monkeypatch):
    _claimed_before_the_upgrade(repo, monkeypatch)
    before = _lease(repo, "T1")
    api.heartbeat(repo, "T1")
    after = _lease(repo, "T1")
    assert (after.worktree, after.branch, after.globs, after.resources) == (
        before.worktree,
        before.branch,
        before.globs,
        before.resources,
    )
    assert "re-homed" in after.note and _bare(repo) in after.note, after.note
    assert not fold(L.EventLog(repo, "r").read_all(), strict=False).items["T1"].lease_contest


def test_re_claiming_your_own_item_after_the_upgrade_is_not_refused(repo, monkeypatch):
    """Release is deliberately open to anyone (an operator frees a crashed agent's
    lease), so it cannot show the defect; a claim, which refuses someone else's live
    lease, can."""
    _claimed_before_the_upgrade(repo, monkeypatch)
    me = _derived(repo)
    out = api.claim(repo, "T1", no_worktree=True)
    assert out.exit == 0, out.reason
    assert _lease(repo, "T1").holder == me


def test_a_bare_id_lease_taken_AFTER_this_clone_had_a_suffix_is_not_taken(repo, monkeypatch):
    """That claim was made by another clone still on the bare id -- the collision B190
    exists to end. Adopting it would hand one agent's lease to another."""
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    monkeypatch.chdir(repo)
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")[0] == 0
    me = _derived(repo)  # the suffix exists from here on
    past = time.time() - 60
    os.utime(repo / L.CLONE_ID_FILE, (past, past))
    bare = _bare(repo)
    assert api.claim(repo, "T1", no_worktree=True, agent=bare).exit == 0
    api.heartbeat(repo, "T1")
    assert _lease(repo, "T1").holder == bare
    assert me != bare


def test_an_explicit_identity_adopts_nothing(repo, monkeypatch):
    """Only the DERIVED id has a bare predecessor; a declared name is not an upgrade."""
    _claimed_before_the_upgrade(repo, monkeypatch)
    api.heartbeat(repo, "T1", agent="reviewer-2")
    monkeypatch.setenv("DDFLOW_AGENT", "worker-a")
    api.heartbeat(repo, "T1")
    assert _lease(repo, "T1").holder == _bare(repo)


def test_re_homing_happens_once(repo, monkeypatch):
    _claimed_before_the_upgrade(repo, monkeypatch)
    _derived(repo)
    api.heartbeat(repo, "T1")
    n = len(L.EventLog(repo, "r").read_all())
    api.heartbeat(repo, "T1")
    api.status(repo)
    kinds = [e.kind for e in L.EventLog(repo, "r").read_all()[n:]]
    assert "lease.acquired" not in kinds and "lease.released" not in kinds, kinds
