"""B4659b50a63: `complete` reads a hand-filed fix task's bug through the CONFIGURED
`[ids]` fix templates, as `verdict` does -- not through the shipped defaults only."""

from __future__ import annotations

from pathlib import Path

from conftest import run_cli

from ddflow.api import knowledge as K
from ddflow.api.lifecycle import complete


def test_complete_closes_the_bug_a_configured_fix_task_names(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == 0
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text("utf-8") + '\n[ids]\nfix_task = "bugfix-{parent}"\n', "utf-8")
    bid = K.bug_found(repo, summary="the flange fails", no_task=True).data["id"]
    task = f"bugfix-{bid}"
    assert run_cli(repo, "task", "add", task, "--globs", "a.py")[0] == 0
    (repo / "tests").mkdir()
    (repo / "tests" / "test_flange.py").write_text("def test_it():\n    pass\n", "utf-8")
    out = complete(repo, task, regression_test="tests/test_flange.py::test_it", force=True)
    assert "is not the fix task of any open bug" not in (out.reason or ""), out.reason
    assert bid in out.data.get("bugs_closed", []), out.data
