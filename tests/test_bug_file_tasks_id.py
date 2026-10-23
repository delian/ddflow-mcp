"""`bug file-tasks` takes the id of ONE bug; with none it only previews
(B-bug-file-tasks-no-id-files-all, B-bug-file-tasks-mcp-no-id).

It used to file a fix task for EVERY open bug lacking one -- including other agents' --
and listed them like a dry run although they were created. Now: `<id>` files that bug's
task; `--all` is the bulk form; neither is a dry run that says so. The tool takes the same
`id` and `all`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import Server


def seed(repo: Path) -> None:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("bug.found", "B-one", {"item": "", "summary": "first stray"})
    log.append("bug.found", "B-two", {"item": "", "summary": "second stray"})


def call(repo: Path, args: dict) -> dict:
    r = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_bug_file_tasks", "arguments": args},
        }
    )["result"]
    return json.loads(r["content"][0]["text"])


def test_cli_without_an_id_is_a_dry_run_that_says_so(repo):
    seed(repo)
    code, out, err = run_cli(repo, "bug", "file-tasks")
    assert code == 0, err
    assert "would file 2" in out and "dry run" in out
    assert "fix-B-one" not in state(repo).items and "fix-B-two" not in state(repo).items


def test_cli_with_an_id_files_only_that_bugs_task(repo):
    seed(repo)
    code, _, err = run_cli(repo, "bug", "file-tasks", "B-two")
    assert code == 0, err
    st = state(repo)
    assert "fix-B-two" in st.items and "fix-B-one" not in st.items
    assert st.bugs["B-two"].fix_task == "fix-B-two"
    assert run_cli(repo, "bug", "file-tasks", "B-two")[0] == 2, "it has its task now"


def test_cli_all_files_every_open_bug(repo):
    seed(repo)
    code, _, err = run_cli(repo, "bug", "file-tasks", "--all")
    assert code == 0, err
    assert {"fix-B-one", "fix-B-two"} <= set(state(repo).items)


def test_an_unknown_or_closed_bug_is_refused(repo):
    seed(repo)
    assert run_cli(repo, "bug", "file-tasks", "B-nope")[0] == 1
    EventLog(repo, "x").append("bug.fixed", "B-one", {"regression_test": "t::x"})
    code, _, err = run_cli(repo, "bug", "file-tasks", "B-one")
    assert code == 1 and "not open" in err
    assert "fix-B-one" not in state(repo).items


def test_id_with_all_is_refused(repo):
    seed(repo)
    code, _, err = run_cli(repo, "bug", "file-tasks", "B-one", "--all")
    assert code != 0 and "not both" in err
    assert "fix-B-one" not in state(repo).items


def test_the_tool_takes_an_id_and_without_one_only_previews(repo):
    seed(repo)
    body = call(repo, {})
    assert body["dry_run"] is True and sorted(body["tasks"]) == ["B-one", "B-two"]
    assert "fix-B-one" not in state(repo).items
    body = call(repo, {"id": "B-one"})
    assert body["dry_run"] is False and body["filed"] == ["B-one"]
    st = state(repo)
    assert "fix-B-one" in st.items and "fix-B-two" not in st.items
    assert call(repo, {"all": True})["filed"] == ["B-two"]


def test_a_bare_api_call_previews_too(repo):
    from ddflow.api import knowledge as K

    seed(repo)
    out = K.bug_file_tasks(repo, agent="a")
    assert out.data["preview"] is True and out.data["dry_run"] is True
    assert "fix-B-one" not in state(repo).items
    assert K.bug_file_tasks(repo, every=True, agent="a").data["filed"] == ["B-one", "B-two"]
