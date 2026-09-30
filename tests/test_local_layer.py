"""Machine-local configuration lives under `.ddflow/local/`, and only there.

`config_paths` has always read `.ddflow/local/{config,gates,reviewers}.toml` last, and
init git-ignores `local/`. This repository instead ignored `.ddflow/gates.toml` and
`.ddflow/reviewers.toml` themselves, in its root `.gitignore`. `gates.toml` is also the
only supported place to declare a `human = true` gate -- no tool writes it -- so a human
checkpoint declared there reached no other clone (bug B-gates-toml-dual-role). And a new
project's `.ddflow/.gitignore` did not ignore a top-level `reviewers.toml` at all, so the
first `git add .ddflow` committed its LAN reviewer endpoints (bug B-init-ignore-local).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

ROOT = Path(__file__).resolve().parents[1]


def _ignored(repo: Path, path: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", path]).returncode == 0


def test_gates_toml_is_committed_project_policy_here():
    assert not _ignored(ROOT, ".ddflow/gates.toml"), (
        "a human gate declared in .ddflow/gates.toml would never reach another clone"
    )
    assert _ignored(ROOT, ".ddflow/local/gates.toml")


def test_init_ignores_a_top_level_reviewers_toml_but_not_gates_toml(repo):
    assert run_cli(repo, "init")[0] == 0
    assert "/reviewers.toml" in (repo / ".ddflow" / ".gitignore").read_text()
    assert _ignored(repo, ".ddflow/reviewers.toml"), "LAN endpoints would be committed"
    assert _ignored(repo, ".ddflow/local/reviewers.toml")
    assert _ignored(repo, ".ddflow/local/gates.toml")
    assert not _ignored(repo, ".ddflow/gates.toml")


def test_the_local_layer_wins_over_the_committed_gate_file(repo):
    """Pins EXISTING precedence (`tomlcfg.config_paths` already read `local/` last); it is
    the reason the fix can move machine values there, not evidence for the fix."""
    from ddflow.config import Config
    from ddflow.services import gates as G

    assert run_cli(repo, "init")[0] == 0
    d = repo / ".ddflow"
    (d / "gates.toml").write_text('[gate.unit_tests]\ncommand = "pytest -q"\n')
    (d / "local").mkdir(exist_ok=True)
    (d / "local" / "gates.toml").write_text('[gate.unit_tests]\ncommand = "pytest -q -n 48"\n')
    assert G.load_gates(repo, Config.load(repo))["unit_tests"].command == "pytest -q -n 48"


def test_the_local_layer_cannot_switch_off_a_committed_human_gate(repo):
    """Found by the rubber-duck: `human = false` in `.ddflow/local/gates.toml` won, and
    that file is in no diff and no review."""
    from ddflow.config import Config
    from ddflow.services import gates as G

    assert run_cli(repo, "init")[0] == 0
    d = repo / ".ddflow"
    (d / "gates.toml").write_text("[gate.signoff]\nhuman = true\n")
    (d / "local").mkdir(exist_ok=True)
    (d / "local" / "gates.toml").write_text("[gate.signoff]\nhuman = false\n")
    assert G.load_gates(repo, Config.load(repo))["signoff"].human is True
