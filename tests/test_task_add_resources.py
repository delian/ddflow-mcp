"""`task add --resources` declares the physical resources up front (bug task-add-lacks-resources):
a GPU task is otherwise claimable between `task add` and the `update` that declares them."""

from __future__ import annotations

from conftest import run_cli
from helpers import items_of as _items


def test_task_add_takes_resources(repo):
    run_cli(repo, "init")
    code, _o, err = run_cli(
        repo, "task", "add", "G1", "--globs", "g.py", "--resources", "gpu:2,vllm-fleet"
    )
    assert code == 0, err
    assert _items(repo)["G1"].resources == ["gpu:2", "vllm-fleet"]


def test_task_add_without_resources_leaves_none(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "task", "add", "G1", "--globs", "g.py")[0] == 0
    assert _items(repo)["G1"].resources == []


def test_ddflow_task_add_takes_resources_over_mcp(repo):
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    res = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_task_add",
                "arguments": {"id": "G2", "globs": "g.py", "resources": "gpu:1"},
            },
        }
    )["result"]
    assert not res.get("isError"), res
    assert _items(repo)["G2"].resources == ["gpu:1"]
