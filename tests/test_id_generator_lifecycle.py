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


def test_complete_does_not_refile_the_bug_its_configured_fix_task_fixes(repo: Path) -> None:
    """The `reported_against` half: a bug whose fix_task names the hand-filed configured
    task is the task's own, not a report left open that needs another fix task."""
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog

    assert run_cli(repo, "init")[0] == 0
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text("utf-8") + '\n[ids]\nfix_task = "bugfix-{parent}"\n', "utf-8")
    bid = "B0123456789"
    task = f"bugfix-{bid}"
    # the fix task first, hand-filed and tagged a bug fix; then the bug, filed against it
    assert run_cli(repo, "task", "add", task, "--globs", "a.py", "--tags", "bugfix")[0] == 0
    K.bug_found(repo, id=bid, summary="the gasket fails", item=task)
    st = fold(EventLog(repo, "t").read_all(), strict=False)
    assert st.bugs[bid].fix_task == task  # the precondition the refile check reads
    before = set(st.items)
    # forced past its open bug: the bug stays open, and is the task's OWN -- not a report
    # against it that `complete` should file another fix task for
    out = complete(repo, task, force=True)
    after = set(fold(EventLog(repo, "t").read_all(), strict=False).items)
    assert out.data.get("bugs_refiled") == {}, out.data.get("bugs_refiled")
    assert after - before == set(), f"a fix task was refiled: {after - before}"
