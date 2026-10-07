"""Golden snapshots of what ddflow shows (B-uni-property-tests; D-unify 4).

Fixtures shared by this directory's tests, imported by each module rather than kept in a
`conftest.py`: the suite's own modules do `from conftest import run_cli`, and a second
`conftest.py` here would shadow tests/conftest.py under that name for every module
collected after it.

Every P-unify interface task moves code behind these surfaces and must leave them
byte-identical, or explain the diff. A snapshot that changes on purpose is re-recorded
with `--snapshot-update` in the same commit as the change, so the diff is reviewed with it:

    uv run pytest tests/golden --snapshot-update

One snapshot serves every Python version CI runs (3.11 and 3.13): the only layout argparse
changed between them is where a long usage line wraps, so the help goldens record the usage
paragraph on one line (`test_golden_cli_help.unwrap_usage`).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from ddflow import __version__
from ddflow.surfaces.cli import main

HERE = Path(__file__).parent
FIXTURE_LOG = HERE / "fixtures" / "project.jsonl"
#: Fixed so neither the parallelism line nor any wrapped text depends on the machine.
FIXTURE_CONFIG = '[schedule]\nparallel = "fixed"\nmax_parallel_tasks = 4\n'


@pytest.fixture(autouse=True)
def _pinned_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing from the developer's shell reaches the output: no DDFLOW_* override (each
    would show as an `[env]` source), a fixed terminal width for argparse and textwrap,
    and no colour."""
    for key in list(os.environ):
        if key.startswith("DDFLOW_") and key != "DDFLOW_LAUNCH_ROOT":
            monkeypatch.delenv(key)
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "off")
    monkeypatch.setenv("COLUMNS", "100")
    # Python 3.14's argparse colours help when the environment asks for it.
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A small adopted project, read from a COMMITTED event log: a phase, three tasks
    (one done, one ready, one waiting on it), a bug, a lesson, a decision, research and a
    memory, with fixed timestamps -- so its brief, recall and exports are stable."""
    root = tmp_path / "proj"
    events = root / ".ddflow" / "events"
    events.mkdir(parents=True)
    git = ["git", "-C", str(root)]
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for k, v in (
        ("user.email", "t@example.com"),
        ("user.name", "Test"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run([*git, "config", k, v], check=True)
    (root / "README.md").write_text("# proj\n")
    subprocess.run([*git, "add", "README.md"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    (events / "golden.jsonl").write_bytes(FIXTURE_LOG.read_bytes())
    (root / ".ddflow" / "config.toml").write_text(FIXTURE_CONFIG)
    return root


def normalise(text: str, *roots: Path) -> str:
    """Replace what differs between runs and machines -- the temporary paths, the running
    version -- with placeholders. Nothing else is touched."""
    for root in roots:
        text = text.replace(str(root), "<ROOT>")
    return re.sub(rf"(?<![\w.]){re.escape(__version__)}(?![\w.])", "<VERSION>", text)


@pytest.fixture
def ddflow(project: Path, capsys: pytest.CaptureFixture):
    """Run one ddflow command in-process on `project`; returns (exit code, stdout+stderr),
    normalised."""

    def run(*argv: str) -> tuple[int, str]:
        capsys.readouterr()
        code = main(["--repo", str(project), "--agent", "golden", *argv])
        out = capsys.readouterr()
        return code, normalise(out.out + out.err, project, project.parent)

    return run
