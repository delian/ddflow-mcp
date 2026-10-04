"""The ci gate: the pre-push checks run on the MERGE RESULT in a scratch worktree (B-ci-gate)."""

from __future__ import annotations

import json
import os
import stat
import subprocess

import pytest
from conftest import run_cli

from ddflow.api import ci as A
from ddflow.config import Config
from ddflow.services import ci as CI


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo, files, msg):
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def fake_precommit(tmp_path, monkeypatch):
    """A `pre-commit` on PATH that reports like the real one and fails when BOTH a.txt and
    b.txt exist (an interaction no single branch can show), or when BAD.txt exists."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pc = bin_dir / "pre-commit"
    pc.write_text(
        "#!/bin/sh\n"
        "rc=0\n"
        "if [ -e BAD.txt ]; then echo 'ruff check.....................Failed'; rc=1; "
        "else echo 'ruff check.....................Passed'; fi\n"
        "if [ -e a.txt ] && [ -e b.txt ]; then echo 'no-a-with-b....................Failed'; rc=1; "
        "else echo 'no-a-with-b....................Passed'; fi\n"
        "echo 'bandit.....................Skipped'\n"
        "exit $rc\n"
    )
    pc.chmod(pc.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return pc


def _project(repo):
    run_cli(repo, "init")
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt")
    return repo


def _branch(repo, name="work"):
    _git(repo, "switch", "-q", "-c", name)


def test_a_clean_branch_passes_and_reports_each_check(repo, fake_precommit):
    _project(repo)
    _branch(repo)
    _commit(repo, {"a.txt": "a\n"}, "work")
    res = CI.run(repo, Config.load(repo))
    assert res.ok and res.exit == 0
    assert {c.id: c.ok for c in res.checks} == {"ruff check": True, "no-a-with-b": True}


def test_a_failing_check_fails_the_gate_with_its_name(repo, fake_precommit):
    _project(repo)
    _branch(repo)
    _commit(repo, {"BAD.txt": "x\n"}, "work")
    res = CI.run(repo, Config.load(repo))
    assert res.status == "failed" and res.exit == 1
    assert [c.id for c in res.checks if not c.ok] == ["ruff check"]


def test_a_parallel_merge_interaction_is_caught_on_the_merge_result_not_the_branch(
    repo, fake_precommit
):
    """The branch adds b.txt, main meanwhile added a.txt: each is clean alone, the pair is not."""
    _project(repo)
    default = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _branch(repo)
    _commit(repo, {"b.txt": "b\n"}, "work")
    _git(repo, "switch", "-q", default)
    _commit(repo, {"a.txt": "a\n"}, "main moved on")
    _git(repo, "switch", "-q", "work")
    alone = CI.run(repo, Config.load(repo), base="__no_such_branch__")
    assert alone.ok, "the branch by itself is clean"
    merged = CI.run(repo, Config.load(repo), base=default)
    assert merged.status == "failed" and merged.merged_with == default
    assert [c.id for c in merged.checks if not c.ok] == ["no-a-with-b"]


def test_a_branch_that_conflicts_with_the_base_fails_not_unavailable(repo, fake_precommit):
    _project(repo)
    default = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _commit(repo, {"f.txt": "base\n"}, "base file")
    _branch(repo)
    _commit(repo, {"f.txt": "branch\n"}, "branch edit")
    _git(repo, "switch", "-q", default)
    _commit(repo, {"f.txt": "main\n"}, "main edit")
    _git(repo, "switch", "-q", "work")
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.status == "failed" and "does not merge" in res.reason


def test_no_precommit_binary_is_unavailable_never_a_pass(repo, monkeypatch, tmp_path):
    _project(repo)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    res = CI.run(repo, Config.load(repo))
    assert res.status == "unavailable" and res.exit == 2 and "not installed" in res.reason


def test_nothing_configured_is_unavailable(repo):
    run_cli(repo, "init")
    res = CI.run(repo, Config.load(repo))
    assert res.status == "unavailable" and "nothing to run" in res.reason


def test_the_scratch_worktree_is_removed_and_the_tree_untouched(repo, fake_precommit):
    _project(repo)
    _branch(repo)
    _commit(repo, {"a.txt": "a\n"}, "work")
    before = _git(repo, "worktree", "list")
    CI.run(repo, Config.load(repo))
    assert _git(repo, "worktree", "list") == before
    assert not _git(repo, "status", "--porcelain", "--untracked-files=no")


def test_the_configured_command_wins_over_the_pre_commit_default(repo):
    _project(repo)
    (repo / ".ddflow" / "config.toml").write_text('[ci]\ncommand = "true"\n')
    res = CI.run(repo, Config.load(repo))
    assert res.ok and res.command == "true"
    (repo / ".ddflow" / "config.toml").write_text('[ci]\ncommand = "false"\n')
    res = CI.run(repo, Config.load(repo))
    assert res.status == "failed" and [c.id for c in res.checks] == ["command"]


def test_the_cli_and_the_tool_agree_and_exit_codes_follow_the_status(repo, fake_precommit):
    _project(repo)
    _branch(repo)
    _commit(repo, {"BAD.txt": "x\n"}, "work")
    code, out, _ = run_cli(repo, "--json", "ci", "run")
    body = json.loads(out)
    assert code == 1 and body["status"] == "failed" and body["checks"][0]["id"] == "ruff check"
    assert A.ci_tool(repo, action="run").data["status"] == "failed"
    code, out, _ = run_cli(repo, "ci", "status")
    assert code == 0 and "pre-commit run --hook-stage pre-push" in out and "available: yes" in out
    assert A.ci_tool(repo, action="status", base="x").exit == 3  # run-only argument


def test_the_ci_gate_is_a_command_gate_that_maps_exit_2_to_unavailable():
    from ddflow.services import gates as G

    g = G.DEFAULT_GATES["ci"]
    assert g.command == "ddflow ci run" and g.unavailable_exits == [2] and not g.required
    assert "ci" not in Config().gates.task_pipeline  # opt-in: not in every project's pipeline


def test_leading_environment_assignments_are_not_the_program():
    assert CI.tool_missing("SKIP=tests true") == ""
    assert (
        CI.tool_missing("A=1 B=2 definitely-not-installed-xyz run")
        == "definitely-not-installed-xyz"
    )
    assert CI.tool_missing("") == ""


def test_an_item_id_names_its_branch_and_the_cli_defaults_to_the_directorys_head(
    repo, fake_precommit
):
    """As a gate it runs in the item's worktree: the primary checkout's HEAD is main, not the work."""
    _project(repo)
    default = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    run_cli(repo, "task", "add", "T1", "--globs", "x.txt")
    wt = repo.parent / "wt-T1"
    _git(repo, "worktree", "add", "-q", "-b", "ddflow/T1", str(wt))
    _commit(wt, {"BAD.txt": "x\n"}, "T1 work")  # only the item's branch is bad
    assert CI.run(repo, Config.load(repo), ref=default).ok  # the primary checkout is clean
    run_cli(repo, "claim", "T1", "--no-worktree")
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog

    EventLog(repo).append(
        "worktree.adopted", "T1", {"path": str(wt), "branch": "ddflow/T1", "base": ""}
    )
    assert fold(EventLog(repo).read_all(), strict=False).items["T1"].branch == "ddflow/T1"
    assert A.run(repo, ref="T1").data["status"] == "failed"
    proc = subprocess.run(
        ["python", "-m", "ddflow", "--repo", str(repo), "ci", "run"],
        cwd=wt, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert proc.returncode == 1 and "ruff check" in proc.stdout, (proc.stdout, proc.stderr)
