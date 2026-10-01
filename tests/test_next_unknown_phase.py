"""`next --phase <id>` refuses an id that is not an item (bug Bde0c6e9fad).

`ddflow next --phase 159` answered "Nothing actionable (0 ready, 0 running, 0 blocked)"
with exit 2 when 159 was not an item at all -- its tasks lived under 159.A..159.I -- and
the implement loop, reading the empty answer as "phase done", stopped early.
"""

from __future__ import annotations

import json

from conftest import run_cli


def _proj(repo):
    run_cli(repo, "init")
    for ph in ("159.A", "159.B"):
        run_cli(repo, "phase", "add", ph, "--title", ph)
        run_cli(repo, "task", "add", f"{ph}.T1", "--phase", ph, "--globs", f"{ph}.py")


def test_an_unknown_phase_is_an_error_naming_the_phases_under_it(repo):
    _proj(repo)
    code, out, err = run_cli(repo, "next", "--phase", "159")
    assert code == 1, (code, out, err)
    assert "no such phase or item '159'" in err and "159.A, 159.B" in err, err
    assert "Nothing actionable" not in out + err


def test_an_unknown_phase_over_json_and_mcp(repo):
    from ddflow.surfaces.mcp import Server

    _proj(repo)
    code, out, _err = run_cli(repo, "--json", "next", "--phase", "NOPE")
    assert code == 1, out
    r = Server(repo, agent="agent-test").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_next", "arguments": {"phase": "159"}},
        }
    )["result"]
    assert r.get("isError") and "159.A" in json.dumps(r), r


def test_a_known_phase_still_answers(repo):
    _proj(repo)
    code, out, _err = run_cli(repo, "next", "--phase", "159.A")
    assert code == 0 and "159.A.T1" in out, out
    run_cli(repo, "remove", "159.B.T1", "--reason", "gone")
    code, _out, _err = run_cli(repo, "next", "--phase", "159.B")
    assert code == 2, "a real phase with nothing left is 'nothing actionable', not an error"
