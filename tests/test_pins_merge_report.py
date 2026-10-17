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


def test_merge_check_all_checks_the_suite_pins_too_and_off_says_nothing(repo):
    """critic on this fix: the one case the bug is about is a suite pin that has drifted."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "pins.toml").write_text(RECIPES.replace("sys.exit(0)", "sys.exit(1)"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    cfg = Config.load(repo)
    cfg.pins.merge_check = "all"
    assert PM.merge_report(repo, cfg) == {"stale": ["fast-table", "golden-suite"]}
    cfg.pins.merge_check = "off"
    assert PM.merge_report(repo, cfg) == {}


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


def test_a_broken_recipes_file_is_said_not_silent(repo):
    """rubber_duck on this fix: an unreadable recipe must not read as 'nothing to say'."""
    _project(repo)
    (repo / ".ddflow" / "pins.toml").write_text('[[pin]]\nname = "x"\n')  # no check, no regen
    report = PM.merge_report(repo, Config.load(repo))
    assert list(report) == ["unknown"] and "check" in report["unknown"][0], report
    assert "not checked after this merge" in "\n".join(PM.merge_lines(report))


def test_a_suite_check_that_times_out_at_merge_is_unknown_not_stale(repo):
    """critic on this fix: a check that did not finish is not a verdict."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "pins.toml").write_text(
        '[[pin]]\nname = "slow-suite"\nafter_merge = false\nfiles = ["g.json"]\n'
        'check = ["{python}", "-c", "import time; time.sleep(30)"]\n'
        'regen = ["{python}", "-c", "pass"]\n'
    )
    cfg = Config.load(repo)
    cfg.pins.merge_check = "all"
    cfg.pins.after_merge_timeout_s = 1
    assert PM.merge_report(repo, cfg) == {"unknown": ["slow-suite"]}


def test_any_failure_loading_the_recipes_is_said_not_raised(repo, monkeypatch):
    _project(repo)

    def boom(*_a, **_k):
        raise PermissionError("pins.toml: permission denied")

    monkeypatch.setattr(PM, "load_recipes", boom)
    assert PM.merge_report(repo, Config.load(repo)) == {"unknown": ["pins.toml: permission denied"]}


def test_a_check_that_cannot_start_is_unknown_not_stale(repo):
    """roborev on this fix: a recipe tool that is not installed is not a drifted pin."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "pins.toml").write_text(
        '[[pin]]\nname = "missing-tool"\nfiles = ["g.json"]\n'
        'check = ["no-such-tool-for-pins-xyz"]\nregen = ["{python}", "-c", "pass"]\n'
    )
    assert PM.merge_report(repo, Config.load(repo)) == {"unknown": ["missing-tool"]}
