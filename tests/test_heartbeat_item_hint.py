"""`ddflow heartbeat` with no item id names what is missing (DX-heartbeat-item-hint).

argparse said only "the following arguments are required: id". Now the one lease the
caller holds is renewed; with none or several, the answer says an item id is needed and
lists what is held, each with the command to run.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import rpc_call

from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import Server


def _seed(repo: Path, *ids: str) -> None:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    for n, i in enumerate(ids):
        log.append("task.added", i, {"parent": "P1", "title": i, "globs": [f"f{n}.py"]})


def test_the_only_held_item_is_renewed_without_naming_it(repo):
    _seed(repo, "T1", "T2")
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent="w")[0] == 0
    code, out, err = run_cli(repo, "heartbeat", agent="w")
    assert code == 0 and "renewed T1" in out, out + err


def test_several_held_items_are_listed_with_the_command_for_each(repo):
    _seed(repo, "T1", "T2")
    for i in ("T1", "T2"):
        assert run_cli(repo, "claim", i, "--no-worktree", agent="w")[0] == 0
    code, out, err = run_cli(repo, "heartbeat", agent="w")
    assert code != 0, out + err
    text = out + err
    assert "needs an item id" in text and "heartbeat T1" in text and "heartbeat T2" in text, text


def test_holding_nothing_says_an_item_id_is_needed(repo):
    _seed(repo, "T1")
    code, out, err = run_cli(repo, "heartbeat", agent="w")
    assert code == 2, out + err
    assert "needs an item id" in out + err and "holds no lease" in out + err, out + err


def test_another_agents_lease_is_not_listed_as_the_callers(repo):
    _seed(repo, "T1", "T2")
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent="x")[0] == 0
    code, out, err = run_cli(repo, "heartbeat", agent="w")
    assert code == 2 and "holds no lease" in out + err and "T1" not in out + err, out + err
    assert run_cli(repo, "claim", "T2", "--no-worktree", agent="w")[0] == 0
    code, out, err = run_cli(repo, "heartbeat", agent="w")
    assert code == 0 and "renewed T2" in out, out + err


def test_the_mcp_tool_without_an_id_renews_the_held_item(repo):
    _seed(repo, "T1")
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent="w")[0] == 0
    out = rpc_call(Server(repo), "ddflow_heartbeat", as_agent="w")
    assert out["_meta"]["exit"] == 0 and "bad arguments" not in str(out), out
