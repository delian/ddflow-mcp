"""Regression for B-widen-over-standing-wait-silent.

With ``[lease].wait_queue`` off, ``update --globs`` that widens onto a file another
agent's standing (non-queued) wait already asks for SUCCEEDS -- the grant is right,
there is no queue to jump -- but the reply said nothing, so the overtaken waiter had to
be found in a status read. Both of the widening agent's replies now name that waiter:
the ``update`` reply names it as overtaken, and the brief's "Waiting on you" section
names the standing ``lease.waiting`` waiter for the files the holder now holds.
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


def test_the_update_reply_names_the_overtaken_waiter(repo):
    _overtake(repo)
    code, out, _ = run_cli(
        repo, "update", "DX", "--globs", "ddflow/tools/foo.py,ddflow/api/lifecycle/brief.py", agent="dx"
    )
    assert code == 0
    assert "overtook" in out and "bhy" in out and "BHY" in out, out


def test_the_brief_names_the_overtaken_waiter(repo):
    _overtake(repo)
    assert (
        run_cli(repo, "update", "DX", "--globs", "ddflow/tools/foo.py,ddflow/api/lifecycle/brief.py", agent="dx")[0]
        == 0
    )
    code, out, _ = run_cli(repo, "brief", agent="dx")
    assert code == 0
    assert "## Waiting on you" in out, out
    waiting_on_you = out.split("## Waiting on you")[1].split("##")[0]
    assert "bhy" in waiting_on_you and "BHY" in waiting_on_you, waiting_on_you


def test_a_later_unrelated_widening_does_not_name_the_waiter_again(repo):
    _overtake(repo)
    assert (
        run_cli(repo, "update", "DX", "--globs", "ddflow/tools/foo.py,ddflow/api/lifecycle/brief.py", agent="dx")[0]
        == 0
    )
    code, out, _ = run_cli(
        repo, "update", "DX", "--globs", "ddflow/tools/foo.py,ddflow/api/lifecycle/brief.py,ddflow/tools/extra.py", agent="dx"
    )
    assert code == 0
    # extra.py is new but overlaps nothing BHY asked for: it was not overtaken by THIS widen.
    assert "overtook" not in out, out

