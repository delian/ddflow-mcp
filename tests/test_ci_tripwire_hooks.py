"""The default CI command does not run commit-time tripwires over every tracked file
(ci-default-all-files-fails-tripwire-hooks).

A hook with `language: fail` and a `files:` pattern is a tripwire: it fires when a changed
path matches. `pre-commit run --all-files` runs it over the whole tree and so fails whenever the
frozen paths exist at all -- merge-time CI red by construction (run_nemo_run's
frozen-workflow-files failed `ci on master` on every merge). The default command now runs the
tripwires on the DIFF the CI is about (the branch against its base, or the range a merge
added) and everything else over all files.
"""

from __future__ import annotations

import shutil

import pytest
from conftest import run_cli
from helpers import commit as _commit
from helpers import git as _git

from ddflow.config import Config
from ddflow.services import ci as CI

pytestmark = pytest.mark.skipif(
    shutil.which("pre-commit") is None, reason="pre-commit not installed"
)

CONFIG = """\
repos:
  - repo: local
    hooks:
      - id: frozen-workflow-files
        name: workflow files frozen since the cutover
        language: fail
        entry: workflow files are frozen
        files: ^frozen/
      - id: always-fine
        name: always fine
        language: system
        entry: "true"
        pass_filenames: false
"""


def _project(repo):
    run_cli(repo, "init")
    (repo / ".pre-commit-config.yaml").write_text(CONFIG)
    (repo / "frozen").mkdir()
    (repo / "frozen" / "old.txt").write_text("frozen long ago\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def test_a_tripwire_over_files_nobody_changed_does_not_fail_ci(repo):
    default = _project(repo)
    _git(repo, "switch", "-q", "-c", "work")
    _commit(repo, {"ok.txt": "fine\n"}, "unrelated work")
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.status == "passed", (res.status, res.reason, res.output_tail)


def test_a_tripwire_still_fires_on_a_branch_that_touches_the_frozen_paths(repo):
    default = _project(repo)
    _git(repo, "switch", "-q", "-c", "work")
    _commit(repo, {"frozen/new.txt": "touched\n"}, "touches the frozen paths")
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.status == "failed", (res.status, res.reason, res.output_tail)
    assert [c.id for c in res.checks if not c.ok] == ["workflow files frozen since the cutover"]


def test_the_after_merge_check_runs_tripwires_on_the_range_the_merge_added(repo):
    default = _project(repo)
    _git(repo, "switch", "-q", "-c", "work")
    _commit(repo, {"ok.txt": "fine\n"}, "unrelated work")
    before = _git(repo, "rev-parse", default)
    _git(repo, "switch", "-q", default)
    _git(repo, "merge", "-q", "--no-ff", "work", "-m", "merge work")
    cfg = Config.load(repo)
    cmd, _ = CI.main_command(repo, cfg)
    res = CI.run(repo, cfg, ref="HEAD", command=cmd, changed_from=before)
    assert res.status == "passed", (res.status, res.reason, res.output_tail)


def test_the_tripwire_ids_are_the_language_fail_hooks_of_the_config(tmp_path):
    (tmp_path / ".pre-commit-config.yaml").write_text(CONFIG)
    assert CI.tripwire_ids(tmp_path) == ["frozen-workflow-files"]
    assert CI.tripwire_ids(tmp_path / "nowhere") == []


def test_an_explicit_ci_command_is_run_as_written(repo):
    default = _project(repo)
    _git(repo, "switch", "-q", "-c", "work")
    _commit(repo, {"ok.txt": "fine\n"}, "unrelated work")
    cfg = Config.load(repo)
    cfg.ci.command = "pre-commit run --hook-stage pre-push --all-files"
    res = CI.run(repo, cfg, base=default)
    assert res.status == "failed", "an operator's own command is not rewritten"


def test_merge_passes_the_range_it_added_to_the_after_merge_check(repo, monkeypatch):
    import importlib

    M = importlib.import_module("ddflow.api.lifecycle.merge")
    default = _project(repo)
    run_cli(repo, "task", "add", "T1", "--globs", "ok.txt")
    code, out, err = run_cli(repo, "claim", "T1", agent="alpha")
    assert code == 0, out + err
    import json

    code, out, err = run_cli(repo, "--json", "show", "T1")
    tree = json.loads(out)["worktree"]
    from pathlib import Path

    tree = Path(tree) if Path(tree).is_absolute() else repo / tree
    (tree / "ok.txt").write_text("fine\n")
    _git(tree, "add", "ok.txt")
    _git(tree, "commit", "-qm", "work", "--no-verify")
    before = _git(repo, "rev-parse", default)
    seen = {}
    real = M.check_after_merge

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(M, "check_after_merge", spy)
    from ddflow import api

    out = api.merge_item(repo, "T1", agent="alpha")
    assert out.exit == 0, out
    assert seen.get("since") == before, seen
    assert out.data["ci"]["status"] == "passed", out.data["ci"]


@pytest.mark.parametrize(
    "extra,expected",
    [
        ("        stages: [pre-commit]\n", []),
        ("        stages: [pre-push]\n", ["frozen-workflow-files"]),
        ("        stages:\n          - pre-commit\n          - push\n", ["frozen-workflow-files"]),
        ("        stages:\n          - pre-commit\n", []),
        ("        stages:\n        - pre-commit\n", []),  # a list at its key's own indent
        ("        stages:\n        - pre-push\n", ["frozen-workflow-files"]),
    ],
)
def test_a_tripwire_kept_to_another_stage_is_not_run_at_pre_push(tmp_path, extra, expected):
    """`pre-commit run <id> --hook-stage pre-push` for a hook that has no such stage is an
    error; the tripwire is named only when the pre-push run has it."""
    (tmp_path / ".pre-commit-config.yaml").write_text(
        CONFIG.replace("language: fail\n", "language: fail\n" + extra, 1)
    )
    assert CI.tripwire_ids(tmp_path) == expected


def test_default_stages_without_pre_push_keep_an_unstaged_tripwire_out(tmp_path):
    (tmp_path / ".pre-commit-config.yaml").write_text("default_stages: [pre-commit]\n" + CONFIG)
    assert CI.tripwire_ids(tmp_path) == []


def test_default_stages_as_a_list_at_the_left_margin_are_read(tmp_path):
    (tmp_path / ".pre-commit-config.yaml").write_text("default_stages:\n- pre-commit\n" + CONFIG)
    assert CI.tripwire_ids(tmp_path) == []


def test_the_after_merge_check_without_a_range_still_checks_the_last_commit(repo):
    """No `changed_from` on the base itself: the commit just made against its first parent, so
    a merge that touched the frozen paths still fires the tripwire."""
    default = _project(repo)
    _git(repo, "switch", "-q", "-c", "work")
    _commit(repo, {"frozen/new.txt": "touched\n"}, "touches the frozen paths")
    _git(repo, "switch", "-q", default)
    _git(repo, "merge", "-q", "--no-ff", "work", "-m", "merge work")
    cfg = Config.load(repo)
    cmd, _ = CI.main_command(repo, cfg)
    res = CI.run(repo, cfg, ref="HEAD", command=cmd)
    assert res.status == "failed" and [c.id for c in res.checks if not c.ok], res


def test_a_hook_item_may_start_with_any_key(tmp_path):
    """YAML mappings are unordered: `- name:` first, `id:` later, `language:` before it."""
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: lint\n"
        "        language: system\n"
        "        entry: flake8\n"
        "      - name: guard\n"
        "        language: fail\n"
        "        id: guard\n"
        "        files: ^forbidden/\n"
        "      - language: fail\n"
        "        id: other-guard\n"
        "        entry: nope\n"
        "default_stages: [pre-push]\n"
    )
    assert CI.tripwire_ids(tmp_path) == ["guard", "other-guard"]


def test_comments_and_blank_lines_inside_a_stage_list_do_not_end_it(tmp_path):
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "default_stages:\n# tripwires run on push\n\n- pre-push\n" + CONFIG
    )
    assert CI.tripwire_ids(tmp_path) == ["frozen-workflow-files"]


def test_the_commands_own_skip_applies_to_the_tripwire_runs_too(tmp_path, repo):
    default = _project(repo)
    cmd = CI.with_tripwires_on_the_diff(
        "SKIP=frozen-workflow-files " + CI.DEFAULT_COMMAND, repo, default
    )
    named = cmd.split("|| rc=1; ", 1)[1]  # the run on the diff, after the all-files one
    assert named.startswith('SKIP="${SKIP:+$SKIP,}frozen-workflow-files" pre-commit run'), cmd
