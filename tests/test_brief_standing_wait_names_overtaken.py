"""Regression for B-widen-over-standing-wait-silent.

With ``[lease].wait_queue`` off, ``update --globs`` that widens onto a file another
agent's standing (non-queued) wait already asks for SUCCEEDS -- the grant is right,
there is no queue to jump -- but the widening agent was told nothing, so its brief
did not name the waiter it just overtook. The brief's "Waiting on you" section only
read the deliberate `ddflow wait` registrations, never the standing `lease.waiting`
records; this pins that the overtaken waiter is named there.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _overtake(repo) -> None:
    """BRES holds `brief.py`; BHY waits on `lifecycle/` (standing, non-queued); BRES lets
    go; DX widens onto `brief.py` -- the overtake the bug is about."""
    for item, g, agent in (
        ("DX", "ddflow/tools/foo.py", "dx"),
        ("BHY", "ddflow/tools/stop.py", "bhy"),
        ("BRES", "ddflow/api/lifecycle/brief.py", "bres"),
    ):
        assert run_cli(repo, "task", "add", item, "--title", item, "--globs", g, agent=agent)[0] == 0
    assert run_cli(repo, "claim", "BRES", "--no-worktree", agent="bres")[0] == 0
    assert run_cli(repo, "claim", "BHY", "--no-worktree", agent="bhy")[0] == 0
    assert run_cli(repo, "claim", "DX", "--no-worktree", agent="dx")[0] == 0
    # BHY's standing non-queued wait on lifecycle/ (refused: BRES holds brief.py).
    assert (
        run_cli(repo, "update", "BHY", "--globs", "ddflow/tools/stop.py,ddflow/api/lifecycle/", agent="bhy")[0]
        == 3
    )
    assert run_cli(repo, "release", "BRES", agent="bres")[0] == 0
    assert (
        run_cli(repo, "update", "DX", "--globs", "ddflow/tools/foo.py,ddflow/api/lifecycle/brief.py", agent="dx")[0]
        == 0
    )


def test_the_brief_names_the_overtaken_waiter(repo):
    _overtake(repo)
    code, out, _ = run_cli(repo, "brief", agent="dx")
    assert code == 0
    assert "## Waiting on you" in out, out
    waiting_on_you = out.split("## Waiting on you")[1].split("##")[0]
    assert "bhy" in waiting_on_you and "BHY" in waiting_on_you, waiting_on_you
