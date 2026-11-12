"""A legitimate takeover of an expired lease (GR-lease-takeover): `recover --adopt`.

An agent's forced claim is dropped (B49fa10bbb1), so salvaging a dead lane meant waiting for an
operator's approval even when nothing could be lost. `recover --item ID --adopt --reason WHY`
adopts the expired lease of a tree MEASURED to hold no uncommitted work (committed work stays on
its branch), records who took over, from whom and why in the lease note, and needs no flag."""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.api import recover_adopt as RA
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _lease(repo: Path):
    return fold(EventLog(repo, "r").read_all()).items["T1"].lease


def _crashed(repo: Path, *, ttl: int = 1) -> Path:
    """T1 claimed by `crashed`, whose lease expires after `ttl` s (a real sleep: expiry only
    grows with time, so this cannot flake short)."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    text = re.sub(r"(?m)^(ttl_s|grace_s) = .*\n", "", cfg.read_text())
    cfg.write_text(text.replace("[lease]\n", f"[lease]\nttl_s = {ttl}\ngrace_s = 0\n", 1))
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": ["src/*"]})
    out = LC.claim(repo, "T1", agent="crashed")
    assert out.exit == 0, out.reason
    return Path(out.data["worktree"])


def _expire() -> None:
    time.sleep(2.2)


def test_a_clean_expired_tree_is_adopted_with_no_force_and_no_approval(repo):
    wt = _crashed(repo)
    _expire()
    out = RA.adopt(repo, "T1", reason="lane died, tree clean", agent="next")
    assert out.exit == 0, out.reason
    lease = _lease(repo)
    assert lease.holder == "next"
    assert "crashed" in lease.note and "lane died, tree clean" in lease.note
    assert Path(out.data["worktree"]).resolve() == wt.resolve()


def test_committed_work_is_adopted_and_stays_on_its_branch(repo):
    wt = _crashed(repo)
    (wt / "work.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-qm", "work"], check=True)
    _expire()
    out = RA.adopt(repo, "T1", reason="finished work committed", agent="next")
    assert out.exit == 0, out.reason
    assert (wt / "work.py").read_text() == "x = 1\n"
    assert "1 unmerged" in _lease(repo).note


def test_a_tree_with_uncommitted_work_is_refused_and_untouched(repo):
    wt = _crashed(repo)
    (wt / "work.py").write_text("x = 1  # the only copy\n")
    _expire()
    out = RA.adopt(repo, "T1", reason="try", agent="next")
    assert out.exit == 3
    assert "uncommitted" in (out.reason or "")
    assert _lease(repo).holder == "crashed"
    assert (wt / "work.py").exists()


def test_a_reason_is_required(repo):
    _crashed(repo)
    _expire()
    out = RA.adopt(repo, "T1", reason="", agent="next")
    assert out.exit == 3 and "reason" in (out.reason or "")
    assert _lease(repo).holder == "crashed"


def test_a_live_lease_is_not_adopted(repo):
    _crashed(repo, ttl=3600)
    out = RA.adopt(repo, "T1", reason="eager", agent="next")
    assert out.exit == 3 and "live" in (out.reason or "")
    assert _lease(repo).holder == "crashed"


def test_adopt_names_the_item(repo):
    _crashed(repo)
    out = RA.adopt(repo, "", reason="x", agent="next")
    assert out.exit == 3 and "--item" in (out.reason or "")


def test_the_cli_route_needs_no_force_flag(repo):
    _crashed(repo)
    _expire()
    code, out, err = run_cli(
        repo, "recover", "--item", "T1", "--adopt", "--reason", "lane died", agent="next"
    )
    assert code == 0, out + err
    assert "adopted T1" in out, out  # the adoption line, not a sweep's prose
    assert _lease(repo).holder == "next"


def test_adopt_with_plan_or_apply_is_refused_and_changes_nothing(repo):
    _crashed(repo)
    _expire()
    for flag in ("--plan", "--apply", "--all"):
        code, out, err = run_cli(
            repo, "recover", "--item", "T1", "--adopt", "--reason", "x", flag, agent="next"
        )
        assert code == 3, (flag, out + err)
        assert "does nothing else" in out + err
    assert _lease(repo).holder == "crashed"


def test_a_refused_adopt_through_the_cli_is_a_refusal_not_a_crash(repo):
    _crashed(repo, ttl=3600)
    code, out, err = run_cli(
        repo, "recover", "--item", "T1", "--adopt", "--reason", "x", agent="next"
    )
    assert code == 3 and "live" in out + err, out + err
