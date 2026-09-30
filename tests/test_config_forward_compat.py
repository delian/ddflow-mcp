"""A config newer than the code reading it is named on every command; a typo is refused
where it can only be a typo.

Which ddflow runs and which config it reads are chosen separately. The shell's
`PYTHONPATH=":"` puts the working directory ahead of the installed package, so a worktree
imports ITS OWN ddflow; `repo_root` resolves every tree to the primary, so it reads the
PRIMARY's `.ddflow/config.toml`. A knob main added is therefore unknown to every older
tree, which refused every command there (bug B9cb7dd1c3b: `enforce.forbidden_trailers`
broke twelve harness trees, and main had to disable `[worktree].local_files`).

81a52e3 made such a knob load-and-skip, but said so only in `doctor`: every other command
ran with the knob dropped and no word about it -- the silent-knob-drop class, and in the
primary, where the code IS the config's own, it let a typo through for good.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import config as C
from ddflow.config import Config

CODE = Path(__file__).resolve().parents[1] / "ddflow"


def _add_knob(repo: Path, line: str) -> None:
    cfg = repo / ".ddflow" / "config.toml"
    text = cfg.read_text()
    assert "\n[enforce]\n" in text, "init writes [enforce]; the key goes into that table"
    cfg.write_text(text.replace("\n[enforce]\n", f"\n[enforce]\n{line}\n", 1))


def _run_own_code(repo: Path, *argv: str) -> tuple[int, str, str]:
    """Run the copy of ddflow that lives IN `repo`, as the primary checkout runs its own."""
    env = {**os.environ, "PYTHONPATH": str(repo)}
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), *argv],
        capture_output=True, text=True, env=env, timeout=300, cwd=repo,
    )  # fmt: skip
    return p.returncode, p.stdout, p.stderr


def test_a_knob_newer_than_the_code_runs_and_is_named_on_every_command(repo):
    """The nested-tree case: the running code is not the config's own tree."""
    run_cli(repo, "init")
    _add_knob(repo, 'from_the_future = ["x"]')
    code, out, err = run_cli(repo, "status")
    assert code == 0, err
    assert "enforce.from_the_future" in err, "skipped, so it must be SAID: " + err
    assert "merge main" in err
    code, out, err = run_cli(repo, "--json", "status")
    assert code == 0, err
    json.loads(out)  # the warning goes to stderr, never into machine output
    assert "enforce.from_the_future" in err


def test_the_warning_is_given_once_per_process_not_once_per_load(tmp_path, capsys):
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text("[lease]\nfrom_the_future = 1\n")
    Config.load(tmp_path)
    Config.load(tmp_path)
    err = capsys.readouterr().err
    assert err.count("lease.from_the_future") == 1, err


def test_a_typo_is_refused_where_the_code_is_the_configs_own(repo):
    """The primary running its own code: its config cannot be newer than it, so an
    unknown knob there is a typo, and the loud refusal stays."""
    run_cli(repo, "init")
    shutil.copytree(CODE, repo / "ddflow", ignore=shutil.ignore_patterns("__pycache__"))
    code, _, err = _run_own_code(repo, "status")
    assert code == 0, err
    _add_knob(repo, 'forbiden_trailers = ["x"]')
    code, _, err = _run_own_code(repo, "status")
    assert code != 0, "a typo in the tree whose code runs must not load"
    assert "enforce.forbiden_trailers" in err


def test_own_code_check_is_by_tree_not_by_prefix(tmp_path, monkeypatch):
    """A tree NESTED under the primary has the primary as a path prefix; it is still
    another checkout, whose code may be older than the primary's config."""
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text("[lease]\nfrom_the_future = 1\n")
    monkeypatch.setattr(C, "_CODE_TREE", tmp_path / ".claude" / "worktrees" / "x")
    assert Config.load(tmp_path).unknown_knobs == ["lease.from_the_future"]
    monkeypatch.setattr(C, "_CODE_TREE", tmp_path.resolve())
    with pytest.raises(ValueError, match=r"lease\.from_the_future"):
        Config.load(tmp_path)


def test_the_machine_local_layer_stays_lenient_even_for_own_code(tmp_path, monkeypatch):
    """`.ddflow/local/config.toml` is versioned with no code at all -- every tree reads
    the same one -- so a knob there may be newer than the primary's code, and refusing
    it would break the primary for a setting another tree's branch introduced."""
    (tmp_path / ".ddflow" / "local").mkdir(parents=True)
    (tmp_path / ".ddflow" / "local" / "config.toml").write_text("[lease]\nlocal_future = 1\n")
    monkeypatch.setattr(C, "_CODE_TREE", tmp_path.resolve())
    assert Config.load(tmp_path).unknown_knobs == ["lease.local_future"]
