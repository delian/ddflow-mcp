"""The recover scan derives each merged item's diff once, not once per verdict probe (Bc52bf2ef41).

`recover` over many merged stale leases made ~44 git processes per item: the completion
verdict (`CM.verdict`) reads the item's `changed_paths` on several probes (gate status,
the chore judge, reviewer independence, the README check, the stale-evidence refinement),
and each read re-ran the diff against git. The scan now shares one cache across the items
it weighs, so the diff is asked of git once per item.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.config import Config
from ddflow.infra.log import EventLog
from ddflow.services import changes as CH
from ddflow.services import leases as L


def _git(*a: str) -> None:
    subprocess.run(["git", *a], check=True, capture_output=True)


def _merged(repo: Path, item: str) -> None:
    """``item`` claimed, one file committed, merged with the lease kept so it later lapses."""
    rel = f"src/{item}/a.py"
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", item, {"parent": "P1", "title": item, "globs": [rel]})
    out = LC.claim(repo, item, agent="worker")
    assert out.exit == 0, out.reason
    wt = Path(out.data["worktree"])
    (wt / rel).parent.mkdir(parents=True, exist_ok=True)
    (wt / rel).write_text("x = 1\n")
    _git("-C", str(wt), "add", "-A")
    ident = ["-c", "user.email=t@e.com", "-c", "user.name=T"]
    _git("-C", str(wt), *ident, "commit", "-qm", f"{item} work")
    run_cli(repo, "config", "--set", "lease.release_at_merge", "false")
    assert LC.merge(repo, item, agent="worker", keep=False).exit == 0


def test_a_scan_derives_each_merged_items_diff_once(repo, monkeypatch):
    items = ["T1", "T2", "T3"]
    run_cli(repo, "init")
    for item in items:
        _merged(repo, item)
    EventLog(repo, "worker").append(
        "session.started", "s1", {"agent": "worker", "model": "claude-opus-5"}
    )

    derived = []
    real = CH.changed_paths

    def counting(repo_arg, *a, **k):
        derived.append(1)
        return real(repo_arg, *a, **k)

    monkeypatch.setattr(CH, "changed_paths", counting)
    recs = L.scan(
        EventLog(repo, "scanner"), Config.load(repo), repo, now=time.time() + 10_000
    )
    assert recs, "the scan found no lapsed leases"

    # The diff is a property of the item; the scan weighs it per item, not per probe.
    # `== len(items)` and not `<=`: a vacuous zero (the counter never saw the hot path)
    # and the unfixed ~5x-per-item re-derivation must both fail, only once-per-item passes.
    assert len(derived) == len(items), (
        f"derived the per-item diff {len(derived)} times for {len(items)} items; "
        "the scan must derive it exactly once per item"
    )
