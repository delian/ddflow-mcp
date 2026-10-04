"""recover and status count what the brief counts as possibly holding work
(B3f8c406fea, Be0d308babd).

Both filtered recovery entries on a truthy `salvageable`, so a worktree git could not
measure (`salvageable is None`, "treat it as containing work") and an item RUNNING with
nobody on it (`stale_running`, never measured) were left out of recover's "may contain
work" count and its `!!` flag, out of its closing warning, and out of status's
`recoverable` list and its "may contain unsaved work" count. The brief was fixed by
B20e103326b; these surfaces now use the same bands.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.api import reporting as R
from ddflow.infra.log import EventLog


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


def _unmeasurable(repo: Path, item: str) -> None:
    wt = _orphan(repo, item)
    (wt / "unshipped.py").write_text("# the only copy\n")
    (wt / ".git").write_text("gitdir: /nonexistent/broken\n")


def test_recover_flags_and_counts_an_unmeasurable_tree(repo):
    _seed(repo, ["T2"])
    _unmeasurable(repo, "T2")
    code, out, err = run_cli(repo, "recover")
    assert code == 0, err
    assert "1 may contain work" in out, out
    line = next(ln for ln in out.splitlines() if "T2" in ln and "[orphan_worktree]" in ln)
    assert not line.startswith("   "), f"an unmeasurable tree must be flagged: {line!r}"
    assert "NOT touched automatically" in out, out


def test_recover_counts_a_running_item_nobody_holds(repo):
    log = _seed(repo, ["T1"])
    log.append("item.started", "T1", {})
    code, out, err = run_cli(repo, "recover")
    assert code == 0, err
    assert "1 may contain work" in out, out


def test_recover_still_leaves_a_clean_tree_unflagged(repo):
    _seed(repo, ["T4"])
    _orphan(repo, "T4")
    _, out, _ = run_cli(repo, "recover")
    assert "0 may contain work" in out, out
    line = next(ln for ln in out.splitlines() if "T4" in ln and "[orphan_worktree]" in ln)
    assert line.startswith("   "), line
    assert "NOT touched automatically" not in out


def test_recover_json_counts_them(repo):
    log = _seed(repo, ["T1", "T2", "T4"])
    log.append("item.started", "T1", {})
    _unmeasurable(repo, "T2")
    _orphan(repo, "T4")
    out = R.recover(repo)
    assert out.data["count"] == 3 and out.data["salvageable"] == 2, out.data


def test_status_lists_and_counts_them(repo):
    log = _seed(repo, ["T1", "T2", "T4"])
    log.append("item.started", "T1", {})
    _unmeasurable(repo, "T2")
    _orphan(repo, "T4")
    code, out, err = run_cli(repo, "--json", "status")
    assert code == 0, err
    ids = {r["item"] for r in json.loads(out)["recoverable"]}
    assert ids == {"T1", "T2"}, ids
    code, out, err = run_cli(repo, "status")
    assert "2 may contain unsaved work" in out, out


def test_doctor_calls_an_unmeasurable_tree_a_problem(repo):
    log = _seed(repo, ["T1", "T2"])
    log.append("item.started", "T1", {})
    _unmeasurable(repo, "T2")
    out = R.doctor(repo)
    # A tree that may hold work is a PROBLEM: doctor exits 1 on it, as on a measured one.
    assert out.exit == 1, out.reason
    d = out.data
    assert any("orphan_worktree: T2" in p for p in d["problems"]), d["problems"]
    # A RUNNING item nobody holds stays a note: `next` offers it to resume.
    assert any("stale_running: T1" in n for n in d["notes"]), d["notes"]
