"""A task whose literal globs name files that do not exist is WARNED about at claim and in
`plan check` (DX-plan-globs-exist, `[plan].warn_missing_globs`); a file the task creates is not."""

from __future__ import annotations

import json
import subprocess

import pytest
from conftest import run_cli

from ddflow.services import globcheck as GC

FILES = [
    "ddflow/surfaces/commands/setup.py",
    "ddflow/surfaces/commands/review.py",
    "ddflow/services/adopt.py",
    "README.md",
]


def _missing(*globs):
    present = set(FILES)
    dirs = {f.rsplit("/", 1)[0] for f in FILES if "/" in f} | {"ddflow", "ddflow/surfaces"}
    return GC.missing(globs, exists=lambda p: p in present or p in dirs, files=FILES)


def test_a_file_in_a_missing_directory_is_flagged_with_the_nearest_paths():
    [m] = _missing("ddflow/surfaces/command/setup.py")
    assert "nor does its directory" in m.why and "ddflow/surfaces/commands/setup.py" in m.nearest


def test_a_name_that_exists_elsewhere_is_flagged():
    [m] = _missing("ddflow/surfaces/commands/adopt.py")
    assert m.nearest == ["ddflow/services/adopt.py"]


def test_a_name_close_to_a_sibling_is_flagged():
    [m] = _missing("ddflow/surfaces/commands/revue.py")
    assert m.nearest == ["ddflow/surfaces/commands/review.py"]


def test_a_new_file_in_an_existing_directory_is_fine():
    assert _missing("ddflow/surfaces/commands/brand_new_thing.py") == []


def test_wildcards_and_existing_paths_are_never_flagged():
    assert (
        _missing("ddflow/nope/**", "ddflow/*/x.py", "README.md", "ddflow/surfaces/commands") == []
    )


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    (repo / "src").mkdir()
    (repo / "src" / "real.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "files"], cwd=repo, check=True)
    return repo


def test_claim_warns_about_a_glob_in_a_missing_directory(proj):
    assert run_cli(proj, "task", "add", "T1", "--globs", "sorc/real.py")[0] == 0
    code, out, err = run_cli(proj, "claim", "T1", "--no-worktree")
    assert code == 0, err
    assert "warning:" in out and "sorc/real.py" in out and "src/real.py" in out


def test_claim_json_carries_glob_warnings(proj):
    run_cli(proj, "task", "add", "T1", "--globs", "sorc/real.py")
    code, out, _e = run_cli(proj, "--json", "claim", "T1", "--no-worktree")
    assert code == 0 and json.loads(out)["glob_warnings"]


def test_claim_of_a_new_file_says_nothing(proj):
    run_cli(proj, "task", "add", "T1", "--globs", "src/brand_new.py")
    code, out, _e = run_cli(proj, "claim", "T1", "--no-worktree")
    assert code == 0 and "warning:" not in out


def test_the_knob_turns_the_warning_off(proj):
    run_cli(proj, "config", "--set", "plan.warn_missing_globs", "false")
    run_cli(proj, "task", "add", "T1", "--globs", "sorc/real.py")
    code, out, _e = run_cli(proj, "claim", "T1", "--no-worktree")
    assert code == 0 and "warning:" not in out


def test_plan_check_warns_and_keeps_its_exit_code(proj, tmp_path):
    plan = tmp_path / "plan.toml"
    plan.write_text('[A]\nglobs = ["sorc/real.py"]\nacceptance = "done"\n')
    code, out, err = run_cli(proj, "plan", "check", str(plan))
    assert code == 0, out + err
    assert "warning  A:" in out and "src/real.py" in out
