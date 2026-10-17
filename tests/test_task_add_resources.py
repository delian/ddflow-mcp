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
