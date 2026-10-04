"""brief shows every dangerous crash leftover, not only the salvageable ones (B20e103326b).

`api.brief` passed `[r for r in recovery if r.salvageable]` to the view, so the
recovery block dropped a RUNNING item with no lease (`stale_running`, salvageable False)
and a worktree git could not read (`salvageable is None`, "treat it as containing
work"). Those are the most dangerous leftovers, and the brief is where an agent looks
first. Clean, merged leftovers are counted in one line rather than dropped silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.infra.log import EventLog


def _seed(repo: Path, ids: list[str]) -> EventLog:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    for t in ids:
        log.append("task.added", t, {"parent": "P1", "title": t, "globs": [f"src/{t}/*"]})
    return log


def _orphan(repo: Path, item: str) -> Path:
    """Claim ``item`` (a real worktree) and let go of it: the tree stays, unclaimed."""
    out = LC.claim(repo, item, agent="crashed")
    assert out.exit == 0, out.reason
    path = Path(out.data["worktree"])
    assert LC.release(repo, item, agent="crashed").exit == 0
    return path


def _recovery_block(repo: Path) -> str:
    out = LC.brief(repo, item="T9", check_recovery=True, agent="next")
    assert out.exit == 0, out.reason
    text = out.data["text"]
    assert "Recoverable work found" in text, text
    return text.split("Recoverable work found", 1)[1].split("\n## ", 1)[0]


def test_a_running_item_with_no_lease_is_in_the_brief(repo):
    log = _seed(repo, ["T1", "T9"])
    log.append("item.started", "T1", {})
    block = _recovery_block(repo)
    assert "**T1** (stale_running" in block, block


def test_an_unmeasurable_worktree_is_in_the_brief(repo):
    _seed(repo, ["T2", "T9"])
    wt = _orphan(repo, "T2")
    (wt / "unshipped.py").write_text("# the only copy\n")
    (wt / ".git").write_text("gitdir: /nonexistent/broken\n")
    block = _recovery_block(repo)
    assert "**T2** (orphan_worktree" in block and "COULD NOT MEASURE" in block, block


def test_salvageable_work_still_comes_first_and_clean_leftovers_are_counted(repo):
    log = _seed(repo, ["T1", "T3", "T4", "T9"])
    log.append("item.started", "T1", {})
    dirty = _orphan(repo, "T3")
    (dirty / "work.py").write_text("x = 1\n")
    _orphan(repo, "T4")  # clean and merged: nothing to lose
    block = _recovery_block(repo)
    assert block.index("**T3**") < block.index("**T1**"), block
    assert "**T4**" not in block, "a clean leftover is a count, not an alarm"
    assert "1 leftover(s) with nothing to salvage" in block, block


# -- the view, on its own (roborev job 1451 #2, rubber_duck #1) ---------------------------


def _rec(item, kind, salvageable):
    from ddflow.services.leases import Recovery

    return Recovery(item=item, holder="h", kind=kind, salvageable=salvageable, advice="a")


def _render(recovery) -> str:
    from ddflow.views.markdown import _brief_recovery

    out: list[str] = []
    _brief_recovery(out, recovery)
    return "\n".join(out)


def test_each_kind_is_ranked_and_capped_on_its_own():
    work = [_rec(f"W{i}", "orphan_worktree", True) for i in range(7)]
    text = _render([_rec("S1", "stale_running", False), _rec("U1", "expired_lease", None), *work])
    assert text.index("**W0**") < text.index("**U1**") < text.index("**S1**"), text
    # Seven trees holding work do not push the others off: 5 named, 2 counted.
    assert "**W4**" in text and "**W5**" not in text and "2 more holding work" in text


def test_only_quiet_leftovers_are_one_line_without_the_alarm():
    text = _render([_rec("C1", "orphan_worktree", False), _rec("C2", "expired_lease", False)])
    assert "Recoverable work found" not in text and "**C1**" not in text
    assert "2 leftover(s) with nothing to salvage" in text, text
    assert _render([]) == ""


def test_every_band_has_its_own_cap_and_label():
    """roborev job 1453 #2."""
    unmeasured = [_rec(f"U{i}", "orphan_worktree", None) for i in range(6)]
    unheld = [_rec(f"S{i}", "stale_running", False) for i in range(6)]
    text = _render([*unmeasured, *unheld])
    assert "1 more could not be measured" in text and "1 more RUNNING with nobody" in text
    assert "**U4**" in text and "**S4**" in text, text
