"""B16: the tests a change reaches are derived from the diff, and run in parallel.

"Choosing which tests to run by reasoning about the change is guessing — derive it."
Each rule below is a statement about a real git repository and a real import graph,
because the selection is only worth anything if it names the tests a change can break
and leaves out the ones it cannot.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import gates as G
from ddflow.services import testselect as T

OK, FAIL, NOTHING = 0, 1, 2


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def proj(repo):
    """pkg.a <- pkg.b <- pkg.c (a chain), pkg.api re-exporting c, and a test per level."""
    files = {
        "pkg/__init__.py": "",
        "pkg/a.py": "def one():\n    return 1\n",
        "pkg/b.py": "from . import a\n\ndef two():\n    return a.one() + 1\n",
        "pkg/c.py": "from pkg import b\n\ndef three():\n    return b.two() + 1\n",
        "pkg/widget.py": "WIDTH = 3\n",
        "pkg/api/__init__.py": "from pkg.c import three\n",
        "tests/conftest.py": "",
        "tests/test_a.py": "from pkg.a import one\n\ndef test_one():\n    assert one() == 1\n",
        "tests/test_b.py": "import pkg.b\n\ndef test_two():\n    assert pkg.b.two() == 2\n",
        "tests/test_c.py": "from pkg.c import three\n\ndef test_three():\n    assert three() == 3\n",
        "tests/test_facade.py": "from pkg.api import three\n\ndef test_f():\n    assert three()\n",
        "tests/sub/test_deep.py": "def test_deep():\n    assert True\n",
        "tests/test_widget_misc.py": "def test_w():\n    assert True\n",
    }
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fixture")
    return repo


def _picked(repo: Path) -> dict[str, str]:
    sel = T.select(repo, "main")
    assert sel is not None
    return {t.path: t.reason for t in sel.tests}


def test_a_leaf_change_reaches_its_importers_within_two_hops(proj):
    (proj / "pkg/a.py").write_text("def one():\n    return 1  # changed\n")
    got = _picked(proj)
    assert got["tests/test_a.py"] == "imports pkg.a"
    assert got["tests/test_b.py"] == "imports pkg.b, which imports pkg.a", (
        "a relative import (`from . import a`) must resolve to pkg.a"
    )
    assert "tests/test_c.py" not in got, "three hops away is layering, not evidence"
    assert "tests/test_facade.py" not in got, "the graph does not continue through a facade"


def test_the_graph_does_not_continue_through_a_package_facade(proj):
    """`pkg/api/__init__.py` imports pkg.c, and test_facade imports pkg.api: two hops, but
    importing a re-exporting facade is not evidence a test exercises pkg.c."""
    (proj / "pkg/c.py").write_text("from pkg import b\n\ndef three():\n    return 3\n")
    got = _picked(proj)
    assert got == {"tests/test_c.py": "imports pkg.c"}, got


def test_a_new_test_file_is_selected_before_it_is_committed(proj):
    (proj / "tests/test_new.py").write_text("def test_n():\n    assert True\n")
    assert _picked(proj) == {"tests/test_new.py": "changed"}


def test_a_changed_conftest_selects_every_test_beneath_it(proj):
    (proj / "tests/conftest.py").write_text("import os\n")
    got = _picked(proj)
    assert "tests/sub/test_deep.py" in got and "tests/test_c.py" in got
    assert got["tests/test_a.py"] == "under changed tests/conftest.py"


def test_a_file_nothing_imports_is_matched_by_name(proj):
    (proj / "pkg/widget.py").write_text("WIDTH = 4\n")
    assert _picked(proj) == {"tests/test_widget_misc.py": "named after widget"}


def test_committed_branch_work_counts_as_well_as_the_working_tree(proj):
    _git(proj, "checkout", "-qb", "feature")
    (proj / "pkg/a.py").write_text("def one():\n    return 1  # committed\n")
    _git(proj, "commit", "-qam", "change a")
    assert "tests/test_a.py" in _picked(proj)


@pytest.mark.parametrize(
    ("configured", "want"),
    [
        (
            "uv run pytest tests/ -q --timeout=60 -n 4",
            "uv run pytest -q --timeout=60 -n 4 tests/test_a.py",
        ),
        ("pytest tests/", "pytest -n auto tests/test_a.py"),  # xdist declared: parallel
        ("pytest tests/ -p no:xdist", "pytest -p no:xdist tests/test_a.py"),  # deliberate
    ],
)
def test_the_run_command_keeps_the_projects_runner_and_runs_in_parallel(tmp_path, configured, want):
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[dependency-groups]\ndev = ["pytest", "pytest-xdist"]\n'
    )
    assert T.run_command(configured, ["tests/test_a.py"], tmp_path) == want


def test_a_project_that_does_not_run_pytest_gets_the_files_not_a_guess(tmp_path):
    assert T.run_command("npm test", ["src/a.test.js"], tmp_path) == ""
    assert T.run_command("", ["tests/test_a.py"], tmp_path) == ""


def test_ddflow_tests_lists_why_and_the_command(proj):
    run_cli(proj, "init")
    run_cli(proj, "config", "--set", "gate.unit_tests.command", "pytest tests/ -q -n 2")
    (proj / "pkg/a.py").write_text("def one():\n    return 1  # changed\n")
    code, out, err = run_cli(proj, "tests")
    assert code == OK, err
    assert "tests/test_a.py  -- imports pkg.a" in out
    assert "pytest -q -n 2 tests/test_a.py tests/test_b.py" in out
    assert "The unit_tests gate still runs the whole suite" in out
    code, out, _ = run_cli(proj, "--json", "tests")
    body = json.loads(out)
    assert {t["path"] for t in body["tests"]} == {"tests/test_a.py", "tests/test_b.py"}


def test_a_change_no_test_reaches_is_exit_2_and_not_a_pass(proj):
    run_cli(proj, "init")
    (proj / "README.md").write_text("# proj, edited\n")
    code, out, _ = run_cli(proj, "tests")
    assert code == NOTHING
    assert "not a pass" in out and "whole suite" in out


def test_doctor_names_a_test_command_that_uses_one_core(repo):
    run_cli(repo, "init")
    (repo / "pyproject.toml").write_text('[dependency-groups]\ndev = ["pytest", "pytest-xdist"]\n')
    run_cli(repo, "config", "--set", "gate.unit_tests.command", "pytest -q")
    _code, out, _ = run_cli(repo, "doctor")
    assert "runs pytest on ONE core although pytest-xdist is declared" in out, out


def test_the_agent_is_told_to_run_relevant_tests_in_parallel_and_all_at_the_gate():
    """The guidance is the deliverable as much as the command: an agent never told to run
    `ddflow tests` keeps guessing, and one told only that skips the full suite."""
    driver = (
        Path(__file__).resolve().parents[1] / "ddflow/templates/drivers/implement-phase.md"
    ).read_text()
    assert "ddflow tests --item <ID>" in driver
    assert "Never record `unit_tests` from a selection." in driver
    prompt = G.DEFAULT_GATES["unit_tests"].prompt
    assert "WHOLE suite" in prompt and "-n auto" in prompt and "ddflow tests" in prompt
