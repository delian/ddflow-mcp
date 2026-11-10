"""A read-only view spends its measurement budget where real work most likely is
(Bfb4b6306d1).

`scan(view=True)` measured stale worktrees in state order up to
`lease.scan_view_trees_max`; past that cap a clean merged run ate the budget and the one
tree holding work was reported "NOT MEASURED", while the cap overflow raised the RESUME
alarm over dozens of clean leftovers. The view now ranks candidates by their cheap
unmerged-commit signal (ahead of the base) before spending the budget, so the budget goes
to likely-work first, and the clean leftovers past the cap are a quiet count line, not the
"could not be measured" alarm.

The free, batched signal is the unmerged count; uncommitted-only dirt is `git status`
(one process per tree), the exact process the cap exists to bound, so it cannot be ranked
without erasing the cap. A tree that holds only uncommitted files is therefore still
`budget_capped` in the view -- but `salvageable` stays None (never clean, never swept), so
the UNCAPPED `ddflow recover` still measures and flags it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.config import Config
from ddflow.infra.log import EventLog
from ddflow.services import leases as L


def _git(*a: str) -> None:
    subprocess.run(["git", *a], check=True, capture_output=True)


def _seed(repo: Path, ids: list[str]) -> EventLog:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    for t in ids:
        log.append("task.added", t, {"parent": "P1", "title": t, "globs": [f"src/{t}/*"]})
    return log


def _orphan(repo: Path, item: str) -> Path:
    out = LC.claim(repo, item, agent="crashed")
    assert out.exit == 0, out.reason
    path = Path(out.data["worktree"])
    assert LC.release(repo, item, agent="crashed").exit == 0
    return path


def _commit_work(wt: Path, name: str) -> None:
    (wt / "work.py").write_text("x = 1\n")
    _git("-C", str(wt), "add", "-A")
    _git(
        "-C",
        str(wt),
        "-c",
        "user.email=t@e.com",
        "-c",
        "user.name=T",
        "commit",
        "-qm",
        name,
    )


def test_a_tree_holding_unmerged_work_is_measured_before_clean_leftovers(repo):
    _seed(repo, ["A", "B", "C"])
    # A and B are clean merged leftovers; C holds an unmerged commit. C is LAST in the
    # state order, so the unfixed view spent its one measurement slot on A and reported C
    # as "NOT MEASURED".
    _orphan(repo, "A")
    _orphan(repo, "B")
    _commit_work(_orphan(repo, "C"), "unshipped work")

    cfg = Config.load(repo)
    cfg.lease.scan_view_trees_max = 1
    recs = L.scan(EventLog(repo, "scanner"), cfg, repo, view=True)

    by = {r.item: r for r in recs}
    assert by["C"].salvageable is True, "the unmerged tree must be measured, not capped"
    assert by["C"].unmerged_commits > 0
    capped = [r for r in recs if r.budget_capped]
    assert capped, "the clean leftovers past the cap are budget-capped, not unmeasurable"


def test_an_uncommitted_tree_past_the_view_cap_is_still_flagged_by_recover(repo):
    """A tree holding only UNCOMMITTED files is `git status`, the capped process, so the
    view cannot rank it ahead of clean leftovers -- but it must still never read "clean".
    The capped view leaves it budget_capped (salvageable None), and UNCAPPED recover
    measures it."""
    _seed(repo, ["A", "B", "C"])
    _orphan(repo, "A")
    _orphan(repo, "B")
    dirty = _orphan(repo, "C")  # ahead == 0, but an uncommitted file
    (dirty / "unshipped.py").write_text("# only copy\n")

    cfg = Config.load(repo)
    cfg.lease.scan_view_trees_max = 1
    view = {r.item: r for r in L.scan(EventLog(repo, "scanner"), cfg, repo, view=True)}
    assert view["C"].salvageable is None  # never clean
    assert view["C"].budget_capped
    full = {r.item: r for r in L.scan(EventLog(repo, "scanner"), cfg, repo, view=False)}
    assert full["C"].salvageable is True, "uncapped recover must still flag the work"


def test_capped_clean_trees_render_as_one_quiet_line_not_the_alarm():
    """The view on its own: budget-capped trees are a count line, not the RESUME alarm."""
    from ddflow.services.leases.model import Recovery
    from ddflow.views.markdown import _brief_recovery

    recs = [
        Recovery(item="C1", holder="h", kind="orphan_worktree", salvageable=False, advice="a"),
        Recovery(
            item="C2",
            holder="h",
            kind="orphan_worktree",
            salvageable=None,
            budget_capped=True,
            advice="a",
        ),
        Recovery(
            item="C3",
            holder="h",
            kind="orphan_worktree",
            salvageable=None,
            budget_capped=True,
            advice="a",
        ),
    ]
    out: list[str] = []
    _brief_recovery(out, recs)
    text = "\n".join(out)
    assert "Recoverable work found" not in text, text
    assert "past the measurement cap" in text, text
