"""brief --phase and board --phase refuse an id that is not an item (bug Bc2acd426f4).

`next --phase` learned it (Bde0c6e9fad); `brief --phase ZZZ` still answered exit 0 with
an ordinary brief and `board --phase ZZZ` printed "_No phases yet_" -- the same empty
answer a driver reads as "nothing here".
"""

from __future__ import annotations

import json

import pytest
from conftest import run_cli


def _proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "159.A", "--title", "a")
    run_cli(repo, "task", "add", "159.A.T1", "--phase", "159.A", "--globs", "a.py")


@pytest.mark.parametrize("verb", ["brief", "board"])
def test_an_unknown_phase_is_refused_naming_what_is_under_it(repo, verb):
    _proj(repo)
    code, out, err = run_cli(repo, verb, "--phase", "159")
    assert code == 1, (code, out, err)
    assert "no such phase or item '159'" in err and "159.A" in err, err
    code, out, err = run_cli(repo, "--json", verb, "--phase", "159")
    assert code == 1 and "no such phase or item '159'" in err, (out, err)


@pytest.mark.parametrize("tool", ["ddflow_brief", "ddflow_board"])
def test_mcp_refuses_it_too(repo, tool):
    from ddflow.surfaces.mcp import Server

    _proj(repo)
    r = Server(repo, agent="agent-test").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": {"phase": "159"}},
        }
    )["result"]
    assert r.get("isError") and "159.A" in json.dumps(r), r


@pytest.mark.parametrize("verb", ["brief", "board"])
def test_a_real_phase_still_answers(repo, verb):
    _proj(repo)
    code, out, _err = run_cli(repo, verb, "--phase", "159.A")
    assert code == 0 and "159.A" in out, out


def test_board_refuses_a_task_id_naming_its_phase(repo):
    """roborev and critic: board slices by PHASE id, so a task id -- which next and brief
    accept -- answered an empty board at exit 0."""
    _proj(repo)
    code, out, err = run_cli(repo, "board", "--phase", "159.A.T1")
    assert code == 1, (out, err)
    assert "'159.A.T1' is a task, not a phase -- it is under phase '159.A'" in err, err
    assert run_cli(repo, "brief", "--phase", "159.A.T1")[0] == 0, "brief slices by any item"
