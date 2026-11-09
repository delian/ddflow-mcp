"""What `recover --apply` does, and what the recovery help says it does (Bdb449f37cd).

help/recovery.md said "`ddflow recover --apply` adopts it, keeping the work". It does
not: `sweep(apply=True)` records expiry only for a lease whose tree it measured EMPTY and
never touches one holding work. What adopts the tree is `claim --force` on the expired
lease, which binds the existing worktree, work and all -- for an empty tree whose expiry
`--apply` recorded as well. The help now says that, and this pins both halves: the
behaviour and the words.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.api import reporting as RP
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _crashed_with_work(repo: Path, *, work: bool = True) -> Path:
    """T1 claimed by an agent that wrote work (or nothing) and died; its 1 s lease has
    expired. A real sleep: expiry only grows with time, so this cannot flake short."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    text = re.sub(r"(?m)^(ttl_s|grace_s) = .*\n", "", cfg.read_text())
    cfg.write_text(text.replace("[lease]\n", "[lease]\nttl_s = 1\ngrace_s = 0\n", 1))
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": ["src/*"]})
    out = LC.claim(repo, "T1", agent="crashed")
    assert out.exit == 0, out.reason
    wt = Path(out.data["worktree"])
    if work:
        (wt / "work.py").write_text("x = 1  # the only copy\n")
    time.sleep(2.2)
    return wt


def test_apply_leaves_a_tree_holding_work_alone_and_claim_force_adopts_it(repo, monkeypatch):
    wt = _crashed_with_work(repo)
    rec = RP.recover(repo, apply=True, agent="op")
    assert [(r.item, r.salvageable) for r in rec.data["_render"]["found"]] == [("T1", True)]
    lease = fold(EventLog(repo, "r").read_all()).items["T1"].lease
    assert lease is not None and lease.holder == "crashed" and not lease.expired_at
    # Not stolen by a plain claim...
    assert LC.claim(repo, "T1", agent="next").exit == 3
    # ...adopted by a forced one: the same tree, with the work in it.
    monkeypatch.setattr(
        "ddflow.services.approval.agent_marker", lambda _a="": ""
    )  # a person forcing
    out = LC.claim(repo, "T1", agent="next", force=True)
    assert out.exit == 0, out.reason
    assert Path(out.data["worktree"]).resolve() == wt.resolve()
    assert (wt / "work.py").read_text().startswith("x = 1")


def test_the_recovery_help_says_what_apply_and_claim_do(repo):
    run_cli(repo, "init")
    code, text, err = run_cli(repo, "help", "recovery")
    assert code == 0, err
    flat = " ".join(text.split())
    assert "`ddflow recover --apply` adopts" not in flat, "the promise the code never kept"
    assert "measured empty" in flat.lower(), flat
    assert "ddflow claim <item> --force" in flat, flat
    assert "could not measure, is never touched" in flat, flat
    # Nor the half-truth: a recorded expiry does not let a plain claim through.
    assert "free again" not in flat, "a plain claim still refuses the item"
    assert "Either way the item is taken back with `ddflow claim <item> --force`" in flat


def test_apply_expires_a_lease_whose_tree_it_measured_empty(repo, monkeypatch):
    """roborev job 1463 #1: the other half of what the help promises."""
    _crashed_with_work(repo, work=False)
    rec = RP.recover(repo, apply=True, agent="op")
    assert [(r.item, r.salvageable) for r in rec.data["_render"]["found"]] == [("T1", False)]
    lease = fold(EventLog(repo, "r").read_all()).items["T1"].lease
    assert lease is not None and lease.expired_at, "the expiry is RECORDED, not dropped"
    monkeypatch.setattr(
        "ddflow.services.approval.agent_marker", lambda _a="": ""
    )  # a person forcing
    out = LC.claim(repo, "T1", agent="next", force=True)
    assert out.exit == 0, out.reason


def test_a_plain_claim_after_apply_says_the_expiry_is_recorded_and_the_agent_route_is_approval(
    repo,
):
    """Bc58ea6b7a7: the help says a recorded expiry is still taken back. An agent's refusal
    names the agent's route (`--approval request`, never `--force`, which claim drops for
    agents) and says what the record says: nothing to salvage (Bbb8c158254)."""
    _crashed_with_work(repo, work=False)
    RP.recover(repo, apply=True, agent="op")
    out = LC.claim(repo, "T1", agent="next")
    assert out.exit == 3, "a recorded expiry is still taken back deliberately (the help)"
    flat = " ".join(out.reason.split())
    assert "recorded" in flat and "nothing to salvage" in flat, flat
    assert "ddflow claim T1 --approval request" in flat, flat
    assert "ddflow claim T1 --force" not in flat, "an agent's --force is dropped: " + flat
    assert "safe as of that sweep" in flat, "the measurement is dated, not current: " + flat
    assert "Run `ddflow recover" not in flat, "recover already ran: " + flat
    assert "wait --item" not in flat, flat


def test_a_person_claim_of_an_unswept_expired_lease_is_told_to_force(repo):
    """Bbb8c158254: the same refusal to a PERSON at a terminal says recover-then-`--force`,
    the route a person's claim honours (an agent's `--force` is dropped and becomes
    `--approval`)."""
    _crashed_with_work(repo)
    out = LC.claim(repo, "T1", agent="")
    assert out.exit == 3
    flat = " ".join(out.reason.split())
    assert "ddflow recover --item T1" in flat, flat
    assert "ddflow claim T1 --force" in flat, flat
    assert "--approval request" not in flat, flat


def test_a_plain_claim_of_an_unswept_expired_lease_still_sends_to_recover(repo):
    _crashed_with_work(repo)
    out = LC.claim(repo, "T1", agent="next")
    assert out.exit == 3
    assert "ddflow recover --item T1" in out.reason, out.reason
    assert "ddflow claim T1 --approval request" in out.reason, out.reason
    assert "ddflow claim T1 --force" not in out.reason, (
        "an agent's --force is dropped: " + out.reason
    )
