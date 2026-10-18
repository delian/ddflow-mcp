"""Bf394add175: an item's ci scope says what actually ran, and a red selected run is not
widened to the whole suite by the unit_tests gate (D-gate-economy-r3, [ci].tests_scope)."""

from __future__ import annotations

import os
import stat
from types import SimpleNamespace as NS

import pytest
from conftest import run_cli
from helpers import git as _git

from ddflow.api import cirun as A
from ddflow.config import Config
from ddflow.services import ci as CI
from ddflow.services import testscope as TSC

HOOKS = """\
repos:
  - repo: local
    hooks:
      - id: tests
        name: tests
        entry: pytest tests/ -m "not slow" -q
        language: system
"""


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    """`pre-commit` and `pytest` on PATH; a file named `fail-<tool>` in the log dir makes it exit 1."""
    bin_dir, log = tmp_path / "bin", tmp_path / "log"
    bin_dir.mkdir()
    log.mkdir()
    for name, body in (
        (
            "pre-commit",
            f"echo run >> {log}/precommit\n"
            f"[ -e {log}/fail-pre-commit ] && {{ echo 'ruff check....Failed'; exit 1; }}\n"
            "echo 'ruff check....Passed'\n",
        ),
        ("pytest", f'echo "$@" >> {log}/pytest\n[ -e {log}/fail-pytest ] && exit 1\nexit 0\n'),
    ):
        f = bin_dir / name
        f.write_text("#!/bin/sh\n" + body)
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv(A.IN_GATE_ENV, raising=False)
    return log


def _write(repo, files):
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fixture")


@pytest.fixture
def branch(repo):
    run_cli(repo, "init")
    _write(
        repo,
        {
            ".pre-commit-config.yaml": HOOKS,
            "pkg/__init__.py": "",
            "pkg/mod.py": "A = 1\n",
            "tests/conftest.py": "",
            "tests/test_mod.py": "from pkg import mod\n\ndef test_a():\n    assert mod.A\n",
        },
    )
    _git(repo, "switch", "-q", "-c", "work")
    _write(repo, {"pkg/mod.py": "A = 2\n"})
    return repo


def _calls(log, name):
    f = log / name
    return f.read_text().splitlines() if f.exists() else []


def test_a_failed_hook_stage_says_no_test_ran(branch, stubs):
    (stubs / "fail-pre-commit").write_text("")
    res = CI.run(branch, Config.load(branch), base="main", for_item=True)
    assert res.status == "failed" and not _calls(stubs, "pytest")
    assert res.scope["scope"] == "selected"
    assert res.scope["ran"] == {"hooks": False}
    assert "non-test hooks failed" in res.scope["why"] and "did not run" in res.scope["why"]


def test_a_failed_selected_stage_says_the_guards_did_not_run(branch, stubs):
    (stubs / "fail-pytest").write_text("")
    res = CI.run(branch, Config.load(branch), base="main", for_item=True)
    assert res.status == "failed"
    assert res.scope["ran"] == {"hooks": True, "selected": False}
    assert "selected tests failed" in res.scope["why"]
    assert len(_calls(stubs, "pytest")) == 1  # one selected run, no second one


def test_a_green_run_reports_every_stage_and_keeps_its_why(branch, stubs):
    res = CI.run(branch, Config.load(branch), base="main", for_item=True)
    assert res.ok and res.scope["ran"] == {"hooks": True, "selected": True}
    assert "failed" not in res.scope["why"]


def _item(outcome, scope):
    ci = NS(outcome=outcome, evidence={"ci_scope": {"scope": scope}} if scope else {})
    return NS(kind="task", promote_to="", fixes=["B1"], base="main", gates={"ci": ci})


def test_unit_tests_does_not_widen_a_failed_selected_ci_to_the_whole_suite(monkeypatch, tmp_path):
    picked = NS(scope="selected", why="x", command="pytest t.py", tests=[], changed_lines=3)
    monkeypatch.setattr(TSC, "changed_lines", lambda tree, base: 3)
    monkeypatch.setattr(TSC, "_selected", lambda *a, **kw: (picked, kw)[0])
    monkeypatch.setattr(TSC.W, "repo_root", lambda t: tmp_path)
    monkeypatch.setattr(TSC.W, "default_branch", lambda r: "main")
    monkeypatch.setattr(TSC.G, "pipeline_for", lambda it, cfg: ["ci"])
    cfg = Config()
    cfg.gates.unit_tests_scope = "selected"
    got = TSC.unit_tests_scope(cfg, NS(bugs={}), _item("failed", "selected"), "pytest", tmp_path)
    assert got is picked  # its own selection, not Scope("full", "the ci gate is failed ...")
    full = TSC.unit_tests_scope(cfg, NS(bugs={}), _item("failed", "full"), "pytest", tmp_path)
    assert full.scope == "full" and "ci gate is failed" in full.why
