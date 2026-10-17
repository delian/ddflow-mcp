"""Bug B09da3e0b13: two branches that each regenerated a generated table merged into a stale
one, and with the post-merge hook off nothing re-recorded it. `doctor` says when a plain
`git merge` leaves the pins with nothing to re-record them."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

from ddflow.config import Config
from ddflow.services import pins_merge as PM

RECIPES = """\
[[pin]]
name = "table"
files = ["table.txt"]
check = ["{python}", "-c", "pass"]
regen = ["{python}", "-c", "pass"]

[[pin]]
name = "golden"
after_merge = false
files = ["golden.json"]
check = ["{python}", "-c", "pass"]
regen = ["{python}", "-c", "pass"]
"""


def _project(repo: Path) -> Config:
    run_cli(repo, "init")
    (repo / ".ddflow" / "pins.toml").write_text(RECIPES)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    return Config.load(repo)


def test_a_project_without_the_hook_is_told_so_and_which_pins_it_never_runs(repo):
    cfg = _project(repo)
    text = "\n".join(PM.hook_notes(repo, cfg))
    assert "post-merge hook is not installed" in text and "pins regenerate --install" in text
    assert "never re-records golden" in text and "pins regenerate --check" in text


def test_an_installed_hook_leaves_only_the_suite_pins_to_say(repo):
    cfg = _project(repo)
    PM.install(repo, cfg)
    notes = PM.hook_notes(repo, cfg)
    assert len(notes) == 1 and "never re-records golden" in notes[0], notes


def test_the_hook_switched_off_is_named(repo):
    cfg = _project(repo)
    cfg.pins.after_merge = "off"
    assert "[pins].after_merge is off" in "\n".join(PM.hook_notes(repo, cfg))


def test_no_recipes_no_notes(repo):
    run_cli(repo, "init")
    assert PM.hook_notes(repo, Config.load(repo)) == []


def test_doctor_carries_the_note(repo):
    _project(repo)
    _, out, err = run_cli(repo, "doctor")
    assert "pin files are not re-recorded after a git merge" in out + err, out + err


def test_a_hand_written_hook_that_runs_the_regeneration_counts_as_installed(repo):
    """critic on this fix: advice to add the command to a foreign hook must not then be nagged."""
    cfg = _project(repo)
    hook = PM.hooks_dir(repo) / "post-merge"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nddflow pins regenerate --after-merge\n")
    hook.chmod(0o755)
    assert "not installed" not in "\n".join(PM.hook_notes(repo, cfg))


def test_a_hook_that_is_not_executable_is_not_a_hook(repo):
    cfg = _project(repo)
    PM.install(repo, cfg)
    (PM.hooks_dir(repo) / "post-merge").chmod(0o644)
    assert "not installed" in "\n".join(PM.hook_notes(repo, cfg))


def test_the_command_in_a_comment_is_not_a_hook(repo):
    """critic on this fix: a disabled line (`# ddflow pins regenerate --after-merge`) re-records nothing."""
    cfg = _project(repo)
    hook = PM.hooks_dir(repo) / "post-merge"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/bin/sh\n{PM.HOOK_MARK}\n# ddflow pins regenerate --after-merge\n")
    hook.chmod(0o755)
    assert "not installed" in "\n".join(PM.hook_notes(repo, cfg))
