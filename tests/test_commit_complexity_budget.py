"""B85bb921feb: the commit hook names a staged function whose complexity rose towards radon
grade D (the architecture guard's limit), so a one-line `if` in a function already at the
limit is seen at commit time, not in the 15-minute ci gate. Real git repository."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import complexity_budget as CB
from ddflow.services import enforce as E

#: the no-radon tests must run where radon is missing, so only the others skip
needs_radon = pytest.mark.skipif(
    importlib.util.find_spec("radon") is None, reason="radon (a dev dependency) is not installed"
)


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


@needs_radon
def test_a_branch_added_to_a_function_at_the_limit_is_named_before_the_ci_gate(repo):
    _setup(repo, 20)  # CC 21, already grade D
    (repo / "m.py").write_text(_fn("near_limit", 21))
    _git(repo, "add", "m.py")
    code, msg = E.check_commit(repo)
    assert code == 0, "advisory only: it must not refuse the commit"
    assert "m.py:1 near_limit CC 21 -> 22 (already grade D, getting worse)" in msg
    assert "counted by" not in msg, "the guard counts functions over 20: this one was counted"


@needs_radon
def test_a_function_crossing_into_grade_d_says_the_guard_counts_it(repo):
    _setup(repo, 19)  # CC 20
    (repo / "m.py").write_text(_fn("near_limit", 20))
    _git(repo, "add", "m.py")
    _, msg = E.check_commit(repo)
    assert (
        "CC 20 -> 21 (grade D: counted by tests/test_architecture_guards.py, which fails when a module's count rises)"
        in msg
    )


@needs_radon
def test_nearing_the_limit_is_a_warning_before_it_is_grade_d(repo):
    _setup(repo, 16)  # CC 17
    (repo / "m.py").write_text(_fn("near_limit", 17))  # CC 18
    _git(repo, "add", "m.py")
    _, msg = E.check_commit(repo)
    assert "CC 17 -> 18 (3 below grade D)" in msg


@needs_radon
def test_an_untouched_or_simplified_function_is_not_named(repo):
    _setup(repo, 20)
    (repo / "m.py").write_text(_fn("near_limit", 19) + "\n" + _fn("other", 1))
    _git(repo, "add", "m.py")
    _, msg = E.check_commit(repo)
    assert "near_limit" not in msg and "complexity budget" not in msg


@needs_radon
def test_the_knob_turns_it_off(repo):
    _setup(repo, 20, "complexity_warn_at = 0\n")
    (repo / "m.py").write_text(_fn("near_limit", 21))
    _git(repo, "add", "m.py")
    assert "complexity budget" not in E.check_commit(repo)[1]


def test_without_radon_nothing_is_claimed(monkeypatch):
    monkeypatch.setattr(CB, "cc_visit", None)
    assert CB.complexities(_fn("f", 30).encode()) == {}


@needs_radon
def test_a_file_that_does_not_parse_is_skipped():
    assert CB.complexities(b"def (:\n") == {}


@needs_radon
def test_a_method_is_measured_like_a_function():
    body = "".join(f"        if x == {i}:\n            return {i}\n" for i in range(17))
    src = ("class C:\n    def m(self, x):\n" + body + "        return -1\n").encode()
    assert CB.complexities(src) == {"C.m": (2, 18)}
    lines = CB.risen(["c.py"], lambda p: src, lambda p: None, 18)
    assert lines and "C.m CC new -> 18" in lines[0]


def test_the_hook_says_nothing_without_radon(repo, monkeypatch):
    """The whole commit-hook path, with radon absent: allowed, and no complexity claim."""
    _setup(repo, 20)
    (repo / "m.py").write_text(_fn("near_limit", 21))
    _git(repo, "add", "m.py")
    monkeypatch.setattr(CB, "cc_visit", None)
    code, msg = E.check_commit(repo)
    assert code == 0 and "complexity budget" not in msg


@needs_radon
def test_a_name_defined_twice_keeps_the_worse_definition():
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(17))
    src = ("def setup(x):\n" + body + "    return 0\n\n\ndef setup(x):\n    return 1\n").encode()
    assert CB.complexities(src)["setup"] == (1, 18)


@needs_radon
def test_a_declared_non_utf8_source_is_still_measured():
    src = "# -*- coding: latin-1 -*-\ndef caf\xe9(x):\n    return x\n".encode("latin-1")
    assert list(CB.complexities(src)) == ["caf\xe9"]


def test_the_tree_the_hook_reads_head_in_is_a_directory_not_an_id(repo):
    """`_complexity_note` runs `git show HEAD:<path>` in `_index_tree(repo)`."""
    _setup(repo, 3)
    assert E._index_tree(repo).is_dir()
