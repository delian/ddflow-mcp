"""`ddflow report` lists what the agent filed during the run, linked or not (DX-report-filed-by-agent).

fleet-gaterecords filed four tasks that the report's 'Filed during the item' section did not
show, because they were not linked to the item.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli
from helpers import git as _git


def _run(repo):
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "Widen the widget", "--globs", "a.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, err
    tree = Path(json.loads(out)["worktree"])
    # Filed during the run, with no --item link:
    run_cli(repo, "task", "add", "DX-side", "--title", "A side task", "--new")
    run_cli(repo, "bug", "found", "--id", "BSIDE", "--summary", "a side bug")
    run_cli(repo, "lesson", "add", "--id", "LSIDE", "--title", "A side lesson", "--new")
    run_cli(
        repo,
        "research",
        "add",
        "--id",
        "RSIDE",
        "--claim",
        "a side claim",
        "--verdict",
        "THEORETICAL",
        "--new",
    )
    run_cli(
        repo,
        "research",
        "add",
        "--id",
        "RLINK",
        "--claim",
        "linked claim",
        "--verdict",
        "THEORETICAL",
        "--item",
        "T1",
        "--new",
    )
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "widen")
    pass_pipeline(repo, "T1", omit=("merge",))
    assert run_cli(repo, "merge", "T1", "--model", "claude-opus-5")[0] == 0
    assert run_cli(repo, "complete", "T1", "--model", "claude-opus-5")[0] == 0
    # ...and after complete, still the same run:
    run_cli(repo, "lesson", "add", "--id", "LLATE", "--title", "A late lesson", "--new")


def test_everything_the_agent_filed_in_the_run_is_listed_and_marked_unlinked(repo):
    _run(repo)
    data = json.loads(run_cli(repo, "--json", "report", "T1")[1])["filed"]
    tasks = {t["id"]: t for t in data["tasks"]}
    assert "DX-side" in tasks and not tasks["DX-side"]["linked"]
    assert [b["id"] for b in data["bugs"]] == ["BSIDE"]
    notes = {n["id"]: n for n in data["notes"]}
    assert set(notes) == {"LSIDE", "RSIDE", "RLINK", "LLATE"}
    assert notes["RLINK"]["linked"] and not notes["RSIDE"]["linked"]
    out = run_cli(repo, "report", "T1")[1].split("## Filed during the item")[1].split("##")[0]
    assert "DX-side (task, open, not linked to the item)" in out
    assert "LSIDE (lesson, not linked to the item): A side lesson" in out
    assert "RLINK (research):" in out
