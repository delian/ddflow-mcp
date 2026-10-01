"""`ddflow --json board` answers in JSON (bug B1f1d4f9f54).

`cmd_board` printed the markdown board whatever the flags, while the README says every
read command takes `--json`: a script piping it to a JSON parser got a markdown comment.
"""

from __future__ import annotations

import json

from conftest import run_cli


def test_json_board_is_json_with_the_boards_structure(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "Core")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--title", "first", "--globs", "a.py")
    run_cli(repo, "task", "add", "P1.T1a", "--parent", "P1.T1", "--globs", "b.py")
    run_cli(repo, "task", "add", "P1.T2", "--phase", "P1", "--needs", "P1.T1", "--globs", "c.py")
    run_cli(repo, "claim", "P1.T1a", "--no-worktree", agent="a1")

    code, out, err = run_cli(repo, "--json", "board")
    assert code == 0, err
    body = json.loads(out)
    [ph] = body["phases"]
    assert (ph["id"], ph["title"]) == ("P1", "Core")
    rows = {t["id"]: t for t in ph["tasks"]}
    assert list(rows) == ["P1.T1", "P1.T1a", "P1.T2"]  # a sub-task follows its parent
    assert rows["P1.T1a"]["depth"] == 1 and rows["P1.T1a"]["parent"] == "P1.T1"
    assert rows["P1.T1a"]["owner"] == "a1" and rows["P1.T1a"]["state"] == "running"
    assert rows["P1.T2"]["needs"] == ["P1.T1"] and rows["P1.T1"]["globs"] == ["a.py"]
    assert rows["P1.T1"]["gates"]["research"] == ""  # every configured gate, recorded or not
    assert body["critical_path"] == ["P1.T1a", "P1.T1", "P1.T2"]  # T1 closes after T1a
    assert body["text"].startswith("<!--"), "the markdown rides along for a JSON caller"


def test_json_board_for_one_phase(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "one")
    run_cli(repo, "phase", "add", "P2", "--title", "two")
    code, out, _err = run_cli(repo, "--json", "board", "--phase", "P2")
    assert code == 0
    body = json.loads(out)
    assert [p["id"] for p in body["phases"]] == ["P2"] and body["phase"] == "P2"


def test_plain_board_is_still_markdown(repo):
    run_cli(repo, "init")
    _code, out, _err = run_cli(repo, "board")
    assert out.startswith("<!--") and "# Work queue" in out
