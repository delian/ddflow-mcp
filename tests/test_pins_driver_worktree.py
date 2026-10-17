"""The pins merge driver in a linked worktree, through a real `git merge` (DX-pins-driver-active-in-worktree)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.services import pins_merge as PM

ROOT = Path(__file__).resolve().parents[1]
RECIPES = (
    '[[pin]]\nname = "readme"\nfiles = ["README.md"]\ncheck = ["true"]\nregen = ["true"]\n'
    '[[pin]]\nname = "transcript"\nfiles = ["transcript.json"]\ncheck = ["true"]\nregen = ["true"]\n'
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        capture_output=True,
        text=True,
    )


def _line(args: str, *, extra: str = "", **_) -> str:
    return f'PYTHONPATH="{ROOT}" "{sys.executable}" -m ddflow {args} {extra}'.strip()


def _block(n: int) -> str:
    return f"<!-- ddflow:begin t -->\ngenerated {n}\n<!-- ddflow:end t -->\n"


def _history(tmp_path: Path, installed: bool = False) -> tuple[Path, Path]:
    """A repository whose `main` and a linked worktree's branch both re-recorded the pin files."""
    repo = tmp_path / "primary"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "pins.toml").write_text(RECIPES)
    (repo / "README.md").write_text("intro\n\n" + _block(1) + "\nmiddle\n\nend\n")
    (repo / "transcript.json").write_text('{\n  "a": 1,\n  "b": 2\n}\n')
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-qm", "base").returncode == 0
    if installed:
        _install(repo)
    wt = tmp_path / "wt"
    assert _git(repo, "worktree", "add", "-q", "-b", "work", str(wt)).returncode == 0
    (wt / "README.md").write_text("intro\n\n" + _block(2) + "\nmiddle\n\nend\nmine\n")
    (wt / "transcript.json").write_text('{\n  "a": 2,\n  "b": 2\n}\n')
    _git(wt, "commit", "-qam", "work")
    (repo / "README.md").write_text("intro\n\n" + _block(3) + "\nmiddle\n\nend\n")
    (repo / "transcript.json").write_text('{\n  "a": 3,\n  "b": 2\n}\n')
    _git(repo, "commit", "-qam", "main")
    return repo, wt


def _install(repo: Path) -> None:
    PM.install(repo, Config(), line=_line)
    (repo / ".git" / "hooks" / "post-merge").unlink()  # the merge, not the regeneration, is tested
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-qm", "attrs").returncode == 0


def test_a_plain_worktree_merge_conflicts_on_the_pin_files(tmp_path):
    _repo, wt = _history(tmp_path)
    r = _git(wt, "merge", "main", "-m", "m")
    assert r.returncode != 0
    assert "CONFLICT" in r.stdout


def test_a_worktree_merge_of_pin_files_uses_the_driver(tmp_path):
    """README.md (generated block plus prose) and the transcript, as `git merge main` in a
    claimed worktree sees them: no per-worktree setup, the repository's config applies."""
    _repo, wt = _history(tmp_path, installed=True)
    r = _git(wt, "merge", "main", "-m", "m")
    assert r.returncode == 0, r.stdout + r.stderr
    readme = (wt / "README.md").read_text()
    assert "<<<<" not in readme and "mine" in readme and "generated 2" in readme
    assert "<<<<" not in (wt / "transcript.json").read_text()


def test_a_tree_older_than_the_tracked_attributes_is_given_them_by_a_claim(tmp_path):
    """The attributes a merge reads are the tree's own: main's arrive only with the merge."""
    repo, wt = _history(tmp_path)
    _install(repo)
    cfg = Config()
    assert PM.driver_gaps(wt, cfg)
    assert PM.ensure_driver(wt, cfg)
    assert PM.driver_gaps(wt, cfg) == []
    r = _git(wt, "merge", "main", "-m", "m")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "<<<<" not in (wt / "README.md").read_text()


def test_a_file_with_another_merge_driver_is_left_alone(tmp_path):
    repo, _wt = _history(tmp_path)
    (repo / ".gitattributes").write_text("README.md merge=ours\n")
    cfg = Config()
    PM.ensure_driver(repo, cfg)
    gaps = PM.driver_gaps(repo, cfg)
    assert any("README.md (ours)" in g for g in gaps)
    assert not any("transcript.json" in g for g in gaps)  # given the driver, README.md was not


def test_a_foreign_driver_command_is_reported_and_left_alone(tmp_path):
    repo, wt = _history(tmp_path)
    _install(repo)
    _git(repo, "config", "--local", "merge.pinsregen.driver", "/opt/own/wrapper %A")
    assert any("another command" in g for g in PM.driver_gaps(wt, Config()))
    PM.ensure_driver(wt, Config())
    assert (
        _git(repo, "config", "--get", "merge.pinsregen.driver").stdout.strip()
        == "/opt/own/wrapper %A"
    )
    PM.install(repo, Config(), line=_line)  # the explicit install is what replaces it
    assert PM.driver_gaps(wt, Config()) == []


def test_a_directory_pin_with_a_file_of_another_driver_keeps_that_file_and_covers_the_rest(
    tmp_path,
):
    repo = tmp_path / "r"
    (repo / "docs" / "pins").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "pins.toml").write_text(
        '[[pin]]\nname = "a"\nfiles = ["docs/pins/"]\ncheck = ["true"]\nregen = ["true"]\n'
    )
    (repo / "docs" / "pins" / "notes.md").write_text("x\n")
    (repo / "docs" / "pins" / "other.md").write_text("x\n")
    (repo / ".gitattributes").write_text("docs/pins/notes.md merge=union\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "x")
    PM.ensure_driver(repo, Config())
    attr = _git(
        repo, "check-attr", "merge", "--", "docs/pins/notes.md", "docs/pins/other.md"
    ).stdout
    assert "notes.md: merge: union" in attr and "other.md: merge: pinsregen" in attr
    assert any(
        "union" in g for g in PM.driver_gaps(repo, Config())
    )  # the new-file case stays named


def test_a_pin_file_with_a_space_in_its_name_is_recognised(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "pins.toml").write_text(
        '[[pin]]\nname = "a"\nfiles = ["my file.md"]\ncheck = ["true"]\nregen = ["true"]\n'
    )
    PM.install(repo, Config(), line=_line)
    assert PM.driver_gaps(repo, Config()) == []


def test_the_config_a_claim_sets_serves_a_tree_whose_attributes_are_tracked(tmp_path):
    repo, wt = _history(tmp_path)
    _install(repo)
    _git(repo, "config", "--local", "--unset-all", "merge.pinsregen.driver")
    cfg = Config()
    assert any("driver is not set" in g for g in PM.driver_gaps(wt, cfg))
    assert PM.ensure_driver(wt, cfg)
    assert _git(repo, "config", "--get", "merge.pinsregen.driver").stdout.strip()
    assert PM.ensure_driver(wt, cfg) == []  # settled: nothing more to do


@pytest.mark.parametrize("attrs", ["", "README.md merge=text\n"])
def test_the_gaps_name_the_files_git_would_not_merge_with_the_driver(tmp_path, attrs):
    repo, _wt = _history(tmp_path)
    (repo / ".gitattributes").write_text(attrs)
    gaps = PM.driver_gaps(repo, Config())
    assert any("README.md" in g for g in gaps) and any("transcript.json" in g for g in gaps)
    _install(repo)
    assert PM.driver_gaps(repo, Config()) == []


def _claimed_repo(repo: Path):
    from conftest import run_cli

    run_cli(repo, "init")
    (repo / ".ddflow" / "pins.toml").write_text(RECIPES)
    (repo / "README.md").write_text("x\n")
    (repo / "transcript.json").write_text("{}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pins")
    run_cli(repo, "task", "add", "T1", "--globs", "t1.py")


def test_a_claim_gives_its_worktree_the_driver_and_doctor_names_the_gap(repo):
    import json

    from conftest import run_cli

    import ddflow.api.lifecycle as A

    _claimed_repo(repo)
    _code, out, _ = run_cli(repo, "--json", "doctor")
    assert "pin files will conflict" in json.dumps(json.loads(out))
    out = A.claim(repo, "T1", agent="a")
    assert out.ok, out.reason
    tree = Path(out.data["worktree"])
    assert _git(tree, "config", "--get", "merge.pinsregen.driver").stdout.strip()
    assert PM.driver_gaps(tree, Config()) == []
    _code, out, _ = run_cli(repo, "--json", "doctor")
    assert "pin files will conflict" not in out


def test_regenerate_adds_a_new_recipes_attribute_without_the_hook(tmp_path):
    """A recipe added after install gets its merge=pinsregen line from a plain `pins regenerate`;
    the post-merge hook is neither written nor changed (DX-pins-attributes-follow-recipes)."""
    from ddflow.api import pin_files as PF

    repo, _wt = _history(tmp_path, installed=True)
    (repo / ".ddflow" / "pins.toml").write_text(
        RECIPES + '[[pin]]\nname = "extra"\nfiles = ["extra.json"]\ncheck = ["true"]\nregen = ["true"]\n'
    )
    (repo / "extra.json").write_text("{}\n")
    hook = repo / ".git" / "hooks" / "post-merge"
    assert not hook.exists()
    assert "extra.json" not in (repo / ".gitattributes").read_text()
    out = PF.pins_regenerate(repo, start=repo)
    assert out.exit == 0
    assert "extra.json merge=pinsregen" in (repo / ".gitattributes").read_text()
    assert not hook.exists()
