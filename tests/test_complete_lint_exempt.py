"""Be5610bf732: `complete` takes `lint_exempt` like `bug fixed`, so a lint-only fix closes
through it without a forced approval."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli
from helpers import state_as_reader as state

from ddflow.api import lifecycle as LC
from ddflow.infra.log import EventLog

LINT = "merge CI check failed: ruff format --check (CI: Lint)"


def _seed(repo: Path, bug: str, title: str) -> str:
    run_cli(repo, "init")
    code, _o, err = run_cli(repo, "bug", "found", "--id", bug, "--summary", "red", "--title", title)
    assert code == 0, err
    pass_pipeline(repo, f"fix-{bug}")
    return f"fix-{bug}"


def test_complete_closes_a_lint_bug_with_lint_exempt(repo):
    item = _seed(repo, "Bci-ruff-1", LINT)
    out = LC.complete(repo, item, model="claude-opus-5", lint_exempt="formatted", agent="a")
    assert out.exit == 0, out.reason
    assert out.data["bugs_closed"] == ["Bci-ruff-1"]
    assert state(repo).bugs["Bci-ruff-1"].resolution == "fixed"
    (fixed,) = [e for e in EventLog(repo).read_all() if e.kind == "bug.fixed"]
    assert fixed.data["regression_verify"]["reason"] == "formatted"


def test_complete_refuses_lint_exempt_for_a_non_lint_bug(repo):
    item = _seed(repo, "Bplain", "an ordinary bug")
    out = LC.complete(repo, item, model="claude-opus-5", lint_exempt="x", agent="a")
    assert out.exit == 3 and "--lint-exempt" in out.reason, out.reason
    assert state(repo).bugs["Bplain"].open


def test_complete_cli_takes_lint_exempt(repo):
    item = _seed(repo, "Bci-ruff-2", LINT)
    code, _o, err = run_cli(
        repo, "complete", item, "--model", "claude-opus-5", "--lint-exempt", "formatted"
    )
    assert code == 0, err
    assert state(repo).bugs["Bci-ruff-2"].resolution == "fixed"
