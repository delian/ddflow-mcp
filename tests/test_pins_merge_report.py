"""Bug Bd92e5da53c: the post-merge hook no longer runs the golden suites, and a stale pin was
silent. `ddflow merge` now checks the pins the hook may run, read-only and whether or not the
hook is installed, and names the pins it never re-records."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

from ddflow.config import Config
from ddflow.services import pins_merge as PM

RECIPES = """\
[[pin]]
name = "fast-table"
files = ["table.txt"]
check = ["{python}", "-c", "import sys; sys.exit(1)"]
regen = ["{python}", "-c", "pass"]

[[pin]]
name = "golden-suite"
after_merge = false
files = ["golden.json"]
check = ["{python}", "-c", "import sys; sys.exit(0)"]
regen = ["{python}", "-c", "pass"]
"""


def _project(repo: Path) -> None:
    run_cli(repo, "init")
    (repo / ".ddflow" / "pins.toml").write_text(RECIPES)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")


def test_the_report_names_a_stale_pin_and_the_ones_the_hook_never_runs(repo):
    _project(repo)
    report = PM.merge_report(repo, Config.load(repo))
    assert report == {"stale": ["fast-table"], "unchecked": ["golden-suite"]}, report
    text = "\n".join(PM.merge_lines(report))
    assert "pins STALE" in text and "fast-table" in text and "ddflow pins regenerate" in text
    assert "golden-suite" in text and "never re-records" in text


def test_no_recipes_means_nothing_to_say(repo):
    run_cli(repo, "init")
    assert PM.merge_report(repo, Config.load(repo)) == {}


def test_ddflow_merge_reports_the_pins(repo):
    _project(repo)
    run_cli(repo, "task", "add", "T1", "--title", "add a", "--globs", "a.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "a.py").write_text("x = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "add a")
    code, out, err = run_cli(repo, "--json", "merge", "T1")
    assert code == 0, out + err
    text = out + err
    assert "pins STALE after this merge: fast-table" in text, text
    assert "never re-records (they run a suite): golden-suite" in text, text
