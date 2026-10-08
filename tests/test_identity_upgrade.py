"""A lease claimed under the pre-B190 derived id is still this clone's after the upgrade.

B190 gave derived ids a per-clone suffix: `{host}-{tree}` became `{host}-{tree}-{hex6}`.
A lease claimed before the upgrade kept the bare id as its holder, and every holder
comparison afterwards -- renew, release, complete, merge, the gate lease-keeper, the
scheduler -- saw someone else's lease: heartbeat said "no lease held", complete refused,
and the agent's own item was offered to it as blocked by a stranger.

The fix re-homes such a lease to the suffixed id, once, on the record -- but only with
local evidence that it is this clone's: acquired BEFORE this clone had a suffix, and its
worktree registered in this clone's git on the lease's branch. Two clones sharing a
hostname and directory name derived the same bare id, so the id alone proves nothing.
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
    out = api.claim(repo, "T1", agent=bare)
    assert out.exit == 0, out.reason
    assert _lease(repo, "T1").holder == bare and _lease(repo, "T1").worktree
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
    # the note is log text, so the machine's hostname in the old id is redacted from it
    # (D-unify 6, bug B5deba76d04); the holder field still carries the id
    assert "re-homed" in after.note and "[REDACTED:hostname]" in after.note, after.note
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
    # WITH a worktree registered here, so only the time rule can spare it (critic, B205:
    # a no-worktree claim is never moved anyway, which made this test vacuous).
    assert api.claim(repo, "T1", agent=bare).exit == 0
    assert _lease(repo, "T1").worktree
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


def test_another_clones_lease_under_the_same_bare_id_is_not_taken(repo, monkeypatch):
    """Rubber-duck on B205: two clones with the same hostname and directory name both
    derived the bare id (the B190 collision). The first to upgrade swept up the OTHER's
    live lease -- and handed that clone the very "no lease held" this task fixes. Its
    worktree is not registered in this clone's git, so it is not ours."""
    _claimed_before_the_upgrade(repo, monkeypatch)
    bare = _bare(repo)
    # An explicit identity: deriving one here would create the suffix NOW, and then the
    # time rule, not the worktree rule, would be what spared T2.
    assert api.task_add(repo, "T2", title="theirs", globs="b.py", agent=bare)
    assert not (repo / L.CLONE_ID_FILE).exists()
    L.EventLog(repo, bare).append(
        "lease.acquired",
        "T2",
        {
            "holder": bare,
            "at": time.time(),
            "ttl_s": 1800,
            "globs": ["b.py"],
            "worktree": "../proj-worktrees/T2",  # the same relative path the other clone uses
            "branch": "ddflow/T2",
            "kind": "task",
        },
    )
    time.sleep(0.01)
    _derived(repo)
    api.heartbeat(repo, "T1")
    assert _lease(repo, "T2").holder == bare
    assert _lease(repo, "T1").holder != bare


def test_a_lease_without_a_worktree_is_left_alone(repo, monkeypatch):
    """Nothing local says whose it is, so it is not moved; `--agent <bare>` still works."""
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    monkeypatch.chdir(repo)
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")[0] == 0
    (repo / L.CLONE_ID_FILE).unlink(missing_ok=True)
    bare = _bare(repo)
    assert api.claim(repo, "T1", no_worktree=True, agent=bare).exit == 0
    time.sleep(0.01)
    _derived(repo)
    api.status(repo)
    assert _lease(repo, "T1").holder == bare
    assert api.heartbeat(repo, "T1", agent=bare).exit == 0


def test_an_empty_clone_id_is_no_suffix_for_the_cut_off_either(repo):
    """`_clone_suffix` treats an empty file as no suffix; the cut-off must agree."""
    (repo / L.CLONE_ID_FILE).parent.mkdir(parents=True)
    (repo / L.CLONE_ID_FILE).write_text("\n")
    assert L.clone_suffix_since(repo) == 0.0
    (repo / L.CLONE_ID_FILE).write_text("abc123\n")
    assert L.clone_suffix_since(repo) > 0
