"""`ddflow_task_add` carries `kind` on the MCP surface too (Bbbc9b5d341, Bmcp-task-add-no-kind).

`ddflow task add --kind task|doc|research|job` has existed on the CLI, and `ddflow_update`
took `kind` on both surfaces (B-doc-task-kind-not-settable-after-add), but the `task add`
declaration kept `kind` out of the MCP schema (`cli_only`) with a byte-budget
`flag_exempt` note. An MCP-only agent filing a doc/research task therefore had no way to
say the kind: the schema did not advertise `kind`, and a `kind` argument a caller sent
anyway was refused at the boundary as an unknown argument. The only path was filing a
plain task and retyping it with `ddflow_update --kind`, which `update` cannot do for a
task already worked under the code pipeline.

* the `ddflow_task_add` schema advertises `kind`, next to `update`'s;
* a `kind` argument files the task with that kind (a doc runs the doc pipeline);
* the CLI flag still works, and an unknown kind is refused on BOTH surfaces.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import TOOLS, Server


def _seed(repo: Path) -> EventLog:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "one"})
    return log


def _mcp(repo: Path, args: dict) -> dict:
    r = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_task_add", "arguments": args},
        }
    )["result"]
    return {"error": r.get("isError", False), "text": r["content"][0]["text"]}


# -- the MCP schema advertises kind ----------------------------------------------------


def test_task_add_mcp_schema_has_kind():
    props = TOOLS["ddflow_task_add"]["properties"]
    assert "kind" in props, (
        "ddflow_task_add has no `kind` property: an MCP-only agent cannot file a doc "
        "task that runs the doc pipeline (Bmcp-task-add-no-kind, Bbbc9b5d341)"
    )
    assert "kind" in TOOLS["ddflow_update"]["properties"]  # the convention to match


def test_task_add_over_mcp_files_the_kind_it_was_given(repo):
    _seed(repo)
    reply = _mcp(repo, {"id": "D1", "title": "docs", "kind": "doc"})
    assert not reply["error"], reply["text"]
    assert state(repo).items["D1"].kind == "doc"


def test_task_add_over_mcp_refuses_an_unknown_kind(repo):
    _seed(repo)
    reply = _mcp(repo, {"id": "D2", "title": "docs", "kind": "pamphlet"})
    assert reply["error"], reply["text"]


def test_task_add_cli_kind_still_works(repo):
    _seed(repo)
    code, out, err = run_cli(repo, "task", "add", "J1", "--title", "job", "--kind", "job")
    assert code == 0, out + err
    assert state(repo).items["J1"].kind == "job"
