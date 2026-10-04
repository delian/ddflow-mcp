"""Onboarding test gate: detection, an honest baseline, and the two gate proposals.

The baseline has to come from a detached tree (never the checkout being changed), a
failure to run is never a pass, and the smoke run must fail when it prints nothing.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import onboard_tests as OT


def _write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _commit(repo: Path, rel: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", rel], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", f"add {rel}"], check=True, capture_output=True
    )


def test_detects_python_and_sizes_workers_only_when_parallelism_is_declared(repo):
    _write(repo, "pyproject.toml", "[project]\nname = 'x'\n[tool.pytest.ini_options]\n")
    _write(repo, "uv.lock", "")
    runner = OT.detect_runner(repo)
    assert runner.family == "python" and runner.command == "uv run pytest" and runner.workers == ""
    _write(
        repo,
        "pyproject.toml",
        "[project]\nname = 'x'\ndependencies = ['pytest-xdist>=3']\n[tool.pytest.ini_options]\n",
    )
    runner = OT.detect_runner(repo)
    assert re.fullmatch(r"-n [0-9]+", runner.workers) and int(runner.workers.split()[1]) >= 2


def test_detects_node_and_make(repo):
    _write(repo, "package.json", '{"scripts": {"test": "jest"}}')
    assert OT.detect_runner(repo).family == "node"
    (repo / "package.json").unlink()
    _write(repo, "Makefile", "test:\n\tgo test ./...\n")
    assert OT.detect_runner(repo).family == "make"


def test_nothing_says_how_tests_run(repo):
    assert OT.detect_runner(repo) is None
    report = OT.propose(repo)
    assert "no runner detected" in OT.render(report)


def test_the_baseline_runs_in_a_detached_tree_that_is_cleaned_up(repo):
    stub = "python -c \"import pathlib; print(pathlib.Path.cwd()); print('2 passed in 0.10s')\""
    result = OT.baseline(repo, stub, timeout=60)
    assert result.ran and result.green and result.counts.get("passed") == 2
    printed = [line for line in result.tail.splitlines() if line.startswith("/")]
    assert printed, result.tail
    assert str(repo) not in printed[0], "it ran in the checkout being changed"
    assert not Path(printed[0]).exists(), "the detached tree must be removed"


def test_a_failed_baseline_names_the_failing_ids(repo):
    stub = "python -c \"print('2 passed, 1 failed in 3.40s'); print('FAILED tests/x.py::t')\""
    result = OT.baseline(repo, stub, timeout=60)
    assert not result.green and result.failing == ["tests/x.py::t"]
    report = OT.Report(
        OT.Runner("python", "python -m pytest", "tests/"), result, None, None, result.failing, []
    )
    assert "known failures" in OT.render(report) and "tests/x.py::t" in OT.render(report)


def test_a_timeout_is_not_a_pass(repo):
    result = OT.baseline(repo, 'python -c "import time; time.sleep(5)"', timeout=1)
    assert not result.ran and not result.green and "did not finish" in result.detail


def test_a_timed_out_command_takes_its_whole_process_group_with_it(repo):
    """`subprocess.run` kills only the shell; a grandchild holding the pipes made the
    bound vanish and left workers in a tree about to be deleted (rubber_duck)."""
    started = time.monotonic()
    result = OT.baseline(repo, "sh -c 'sleep 600 & wait'", timeout=1)
    elapsed = time.monotonic() - started
    assert not result.ran and "did not finish" in result.detail
    assert elapsed < 20, f"the bound was not enforced ({elapsed:.1f}s)"


def test_a_command_that_fails_is_not_green(repo):
    result = OT.baseline(repo, "no-such-runner-xyz", timeout=30)
    assert not result.green and result.exit_code != 0


def test_live_test_wraps_the_entry_point_so_empty_output_fails(repo):
    _write(repo, "thing/__init__.py", "")
    _write(repo, "thing/__main__.py", "print('x')\n")
    prop = OT.live_test(repo)
    assert prop.kind == "live_test"
    assert prop.command == 'test -n "$(python -m thing --version)"'


def test_live_test_uses_uv_when_the_project_does(repo):
    _write(repo, "uv.lock", "")
    _write(repo, "pyproject.toml", "[project]\nname = 'x'\n[project.scripts]\nmytool = 'x:main'\n")
    prop = OT.live_test(repo)
    assert prop.command == 'test -n "$(uv run mytool --version)"'


def test_live_test_is_none_when_no_entry_point(repo):
    assert OT.live_test(repo) is None
    report = OT.Report(OT.Runner("python", "python -m pytest", "tests/"), None, None, None, [], [])
    assert "no entry point" in OT.render(report)


def test_propose_end_to_end_with_a_makefile(repo):
    _write(repo, "Makefile", "test:\n\t@echo '1 passed in 0.10s'\n")
    _commit(repo, "Makefile")
    report = OT.propose(repo, timeout=60)
    assert report.runner is not None and report.runner.family == "make"
    assert report.baseline is not None and report.baseline.green
    assert report.unit_tests.command == "make test"
    assert "unit_tests: make test" in OT.render(report)


def test_render_shows_the_gates_and_notes(repo):
    runner = OT.Runner("python", "uv run pytest", "pyproject.toml", "-n 4")
    base = OT.Baseline("uv run pytest -n 4", True, 0, "2 passed in 1s", {"passed": 2}, [], 1.0, "")
    report = OT.Report(
        runner,
        base,
        OT.Proposal("unit_tests", "uv run pytest -n 4", ".ddflow/gates.toml", "x"),
        OT.Proposal("live_test", 'test -n "$(x)"', ".ddflow/gates.toml", "y"),
        [],
        ["workers for THIS machine belong in .ddflow/local/gates.toml: -n 4"],
    )
    text = OT.render(report)
    assert "unit_tests: uv run pytest -n 4" in text
    assert "live_test:" in text
    assert "note: workers for THIS machine" in text
