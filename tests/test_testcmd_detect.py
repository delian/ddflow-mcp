"""One implementation of "how does this project test itself" (B-uni-onboard-testcmd):
onboarding and the unit_tests gate share the runner detection, the xdist and pytest
evidence, and the summary parser."""

from __future__ import annotations

import re
from pathlib import Path

from ddflow.services import gates as G
from ddflow.services import onboard_tests as OT
from ddflow.services.gates import testcmd as TC


def _write(repo: Path, rel: str, text: str = "") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_onboarding_and_the_gate_share_one_detector():
    assert OT.detect_runner is TC.detect and OT.Runner is TC.Runner
    assert G.detect is TC.detect and G.summary_lines is TC.summary_lines


def test_xdist_declared_outside_pyproject_is_seen_by_onboarding(repo):
    """Bef689db128: onboarding read pyproject.toml only, the gate read every manifest."""
    _write(repo, "tests/test_a.py", "def test_a(): pass\n")
    _write(repo, "requirements-dev.txt", "pytest\npytest-xdist>=3\n")
    runner = TC.detect(repo)
    assert runner and runner.family == "python"
    assert re.fullmatch(r"-n [0-9]+", runner.workers), runner
    assert G.declares_xdist(repo), "the gate agrees"


def test_a_commented_out_xdist_is_not_declared(repo):
    _write(repo, "tests/test_a.py", "def test_a(): pass\n")
    _write(repo, "requirements-dev.txt", "pytest\n# pytest-xdist>=3\n")
    assert TC.detect(repo).workers == ""


def test_pytest_named_only_in_a_manifest_is_a_python_runner(repo):
    _write(repo, "requirements.txt", "pytest==8\n")
    runner = TC.detect(repo)
    assert runner and runner.family == "python"
    assert runner.evidence.endswith("requirements.txt"), runner


def test_evidence_names_the_file_that_said_so(repo):
    _write(repo, "pyproject.toml", "[tool.pytest.ini_options]\n")
    assert TC.detect(repo).evidence.endswith("pyproject.toml")
    (repo / "pyproject.toml").unlink()
    _write(repo, "tests/test_a.py", "")
    assert TC.detect(repo).evidence == "tests/ + conftest"


def test_one_parser_reads_both_summary_shapes():
    pytest_out = "x\n== 2 failed, 112 passed, 1 error in 4.2s ==\n"
    assert TC.summary_lines(pytest_out) == ["== 2 failed, 112 passed, 1 error in 4.2s =="]
    line = TC.last_count_line(pytest_out)
    assert TC.counts_of(line) == {"failed": 2, "passed": 112, "error": 1}
    # a line the banner grammar does not match still counts for a baseline (jest)
    assert TC.counts_of(TC.last_count_line("Tests:  3 passed, 3 total\n")) == {"passed": 3}
    assert TC.last_count_line("nothing here\n") == "" and TC.counts_of("") == {}


def test_a_package_json_that_is_not_an_object_is_no_runner_not_a_crash(repo):
    """Be91dfbcca0: valid JSON of another shape ([] / null / "x") raised AttributeError."""
    for body in ("[]", "null", '"x"', "3"):
        _write(repo, "package.json", body)
        assert TC.detect(repo) is None, body
