"""B85bb921feb: the commit hook names a staged function whose complexity rose towards radon
grade D (the architecture guard's limit), so a one-line `if` in a function already at the
limit is seen at commit time, not in the 15-minute ci gate. Real git repository."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

pytest.importorskip("radon", reason="radon (a dev dependency) is not installed")

from ddflow.services import complexity_budget as CB
from ddflow.services import enforce as E


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "DDFLOW_AGENT": "alpha"},
        timeout=180,
    )


def _fn(name: str, branches: int) -> str:
    """A function of complexity ``branches + 1``."""
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(branches))
    return f"def {name}(x):\n{body}    return -1\n"


def _setup(repo: Path, branches: int, extra: str = "") -> Path:
    run_cli(repo, "adopt", "--agents", "claude")
    (repo / ".ddflow" / "config.toml").write_text(
        '[enforce]\ncommit_without_lease = "off"\n' + extra
    )
    (repo / "m.py").write_text(_fn("near_limit", branches))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "scaffold", "--no-verify")
    return repo


def test_a_branch_added_to_a_function_at_the_limit_is_named_before_the_ci_gate(repo):
    _setup(repo, 20)  # CC 21, already grade D
    (repo / "m.py").write_text(_fn("near_limit", 21))
    _git(repo, "add", "m.py")
    code, msg = E.check_commit(repo)
    assert code == 0, "advisory only: it must not refuse the commit"
    assert "m.py:1 near_limit CC 21 -> 22" in msg and "test_architecture_guards" in msg


def test_a_function_crossing_into_grade_d_says_the_guard_will_fail(repo):
    _setup(repo, 19)  # CC 20
    (repo / "m.py").write_text(_fn("near_limit", 20))
    _git(repo, "add", "m.py")
    _, msg = E.check_commit(repo)
    assert "CC 20 -> 21 (grade D: tests/test_architecture_guards.py will fail)" in msg


def test_nearing_the_limit_is_a_warning_before_it_is_grade_d(repo):
    _setup(repo, 16)  # CC 17
    (repo / "m.py").write_text(_fn("near_limit", 17))  # CC 18
    _git(repo, "add", "m.py")
    _, msg = E.check_commit(repo)
    assert "CC 17 -> 18 (3 below grade D)" in msg


def test_an_untouched_or_simplified_function_is_not_named(repo):
    _setup(repo, 20)
    (repo / "m.py").write_text(_fn("near_limit", 19) + "\n" + _fn("other", 1))
    _git(repo, "add", "m.py")
    _, msg = E.check_commit(repo)
    assert "near_limit" not in msg and "complexity budget" not in msg


def test_the_knob_turns_it_off(repo):
    _setup(repo, 20, "complexity_warn_at = 0\n")
    (repo / "m.py").write_text(_fn("near_limit", 21))
    _git(repo, "add", "m.py")
    assert "complexity budget" not in E.check_commit(repo)[1]


def test_without_radon_nothing_is_claimed(monkeypatch):
    monkeypatch.setattr(CB, "cc_visit", None)
    assert CB.complexities(_fn("f", 30).encode()) == {}


def test_a_file_that_does_not_parse_is_skipped():
    assert CB.complexities(b"def (:\n") == {}
