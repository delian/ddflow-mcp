"""`ddflow list <kind>`: the CLI twin of `ddflow_list` (bugs B8ea46cd8a9, no-item-listing-command).

It takes the same kinds and filters as `task list`, `bug list` ... and as the MCP tool, and a
task row carries its `needs`, `globs` and `parent`, so a queue can be audited without the
sqlite index.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _seed(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "phase one")
    run_cli(repo, "task", "add", "T1", "--title", "first", "--phase", "P1", "--globs", "a/**")
    run_cli(repo, "task", "add", "T2", "--title", "second", "--needs", "T1", "--globs", "b.py")


def test_list_is_a_command_and_matches_task_list(repo):
    _seed(repo)
    code, out, err = run_cli(repo, "list", "task", "--json")
    assert code == 0, (out, err)
    code2, out2, _ = run_cli(repo, "task", "list", "--json")
    assert code2 == 0
    assert json.loads(out) == json.loads(out2)


def test_list_rows_carry_needs_and_globs(repo):
    _seed(repo)
    code, out, err = run_cli(repo, "list", "task", "--json")
    assert code == 0, (out, err)
    rows = {r["id"]: r for r in json.loads(out)["rows"]}
    assert rows["T2"]["needs"] == ["T1"]
    assert rows["T2"]["globs"] == ["b.py"]
    assert rows["T1"]["globs"] == ["a/**"]
    assert "parent" in rows["T1"]


def test_list_takes_the_filters_and_kinds(repo):
    _seed(repo)
    code, out, _ = run_cli(repo, "list", "task", "--phase", "P1")
    assert code == 0 and "T1" in out and "T2" not in out
    code, out, _ = run_cli(repo, "list", "phase")
    assert code == 0 and "P1" in out
    code, _, _ = run_cli(repo, "list", "nonsense")
    assert code != 0


def test_list_refuses_a_flag_the_kind_does_not_take(repo):
    _seed(repo)
    code, _, err = run_cli(repo, "list", "task", "--item", "T1")
    assert code == 3 and "--item" in err


def test_list_refusal_under_json_carries_a_body(repo):
    _seed(repo)
    code, out, _ = run_cli(repo, "list", "task", "--item", "T1", "--json")
    body = json.loads(out)
    assert code == 3 and "--item" in body["reason"] and body["schema"] == "list@1"
