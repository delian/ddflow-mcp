"""B-parallel-tests: a test gate that runs pytest on one core is the slowest step in the
workflow, and ddflow says so.

Measured on this repository (research R537ed343e7): the unit_tests gate took 49 minutes
serially on a 192-core machine and under a minute with pytest-xdist. ddflow cannot run a
project's tests faster itself -- the command is the project's -- but it can propose the
parallel command where it proposes one, and point at a serial one where it judges the
workflow.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.services import gates as G
from ddflow.services import workflow as WF

XDIST_PYPROJECT = '[dependency-groups]\ndev = ["pytest>=9", "pytest-xdist>=3.8"]\n'
PLAIN_PYPROJECT = '[dependency-groups]\ndev = ["pytest>=9"]\n'


@pytest.mark.parametrize(
    ("command", "serial"),
    [
        ("uv run pytest tests/ -q --timeout=420", True),
        ("/usr/bin/pytest -q", True),
        ("python -m pytest --no-header", True),
        ("uv run pytest tests/ -q -n 48", False),
        ("pytest -nauto", False),
        ("pytest --numprocesses 4", False),
        ("pytest --dist loadfile -n 2", False),
        ("pytest -q -p no:xdist", False),  # serial, deliberately
        ("npm test", False),
        ("make check", False),
        ("uv run --with pytest-xdist ruff check .", False),
    ],
)
def test_a_serial_pytest_command_is_told_apart(command, serial):
    assert G.runs_pytest_serially(command) is serial


def test_xdist_is_found_in_whichever_manifest_declares_it(tmp_path):
    assert not G.declares_xdist(tmp_path)
    (tmp_path / "pyproject.toml").write_text(PLAIN_PYPROJECT)
    assert not G.declares_xdist(tmp_path)
    (tmp_path / "requirements-dev.txt").write_text("pytest\npytest_xdist==3.8.0\n")
    assert G.declares_xdist(tmp_path)


def test_the_proposed_command_is_parallel_only_where_xdist_is_declared(tmp_path):
    assert G.suggested_test_command(tmp_path) == "", "not a Python project: no pytest advice"
    (tmp_path / "pyproject.toml").write_text(PLAIN_PYPROJECT)
    assert G.suggested_test_command(tmp_path) == "pytest -q"
    (tmp_path / "pyproject.toml").write_text(XDIST_PYPROJECT)
    assert G.suggested_test_command(tmp_path) == "pytest -q -n auto"


def test_an_unset_test_gate_names_the_command_to_set(tmp_path):
    (tmp_path / "pyproject.toml").write_text(XDIST_PYPROJECT)
    outcome, ev = G.run_command_gate(G.GateDef(id="unit_tests"), tmp_path)
    assert outcome == "unavailable"
    assert "gate.unit_tests.command 'pytest -q -n auto'" in ev["reason"]
    # Only the test gate gets a test command proposed.
    _, ev = G.run_command_gate(G.GateDef(id="standards"), tmp_path)
    assert ev["reason"] == "no command configured for this gate"


def test_the_workflow_check_advises_only_when_it_can_read_the_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(XDIST_PYPROJECT)
    gates = {"unit_tests": G.GateDef(id="unit_tests", command="pytest -q")}
    advice = [f for f in WF.check(Config(), gates, tmp_path) if "ONE core" in f.detail]
    assert [(f.subject, f.level) for f in advice] == [("unit_tests", WF.ADVISORY)]
    assert "-n auto" in advice[0].detail and "although pytest-xdist is declared" in advice[0].detail
    assert not [f for f in WF.check(Config(), gates) if "ONE core" in f.detail]
    gates["unit_tests"].command = "pytest -q -n auto"
    assert not [f for f in WF.check(Config(), gates, tmp_path) if "ONE core" in f.detail]


def test_ddflow_workflow_shows_the_advice_end_to_end(repo):
    run_cli(repo, "init")
    (repo / "pyproject.toml").write_text(PLAIN_PYPROJECT)
    assert run_cli(repo, "config", "--set", "gate.unit_tests.command", "pytest -q")[0] == 0
    _, out, _ = run_cli(repo, "workflow")
    assert "runs pytest on ONE core: declare pytest-xdist" in out, out


@pytest.mark.parametrize(
    "manifest",
    [
        '[dependency-groups]\ndev = ["pytest>=9"]\n# "pytest-xdist>=3",  disabled\n',
        '[dependency-groups]\ndev = ["pytest>=9", "pytest-xdist-foo>=1"]\n',
    ],
)
def test_a_comment_or_a_longer_name_does_not_declare_xdist(tmp_path, manifest):
    """Regression (rubber-duck on B-parallel-tests): a substring match proposed `-n auto`
    for a project whose only mention of xdist was commented out, or another package."""
    (tmp_path / "pyproject.toml").write_text(manifest)
    assert not G.declares_xdist(tmp_path)
    assert G.suggested_test_command(tmp_path) == "pytest -q"


def test_a_python_project_that_does_not_use_pytest_is_not_told_to(tmp_path):
    """Regression (rubber-duck on B-parallel-tests): any Python manifest got `pytest -q`,
    including a unittest-only project."""
    (tmp_path / "tox.ini").write_text("[testenv]\ncommands = python -m unittest discover\n")
    assert G.suggested_test_command(tmp_path) == ""
    (tmp_path / "conftest.py").write_text("")
    assert G.suggested_test_command(tmp_path) == "pytest -q", "conftest.py is pytest's own"
