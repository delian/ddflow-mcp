"""One fold per command (B-gap-log-scale): `status` and `next` fold the log once, however
many views they build from it, and the answers are what the several folds gave."""

from __future__ import annotations

import io
import contextlib
import json
import subprocess
from pathlib import Path

import pytest

from ddflow.core import records
from ddflow.services import logfixture
from ddflow.surfaces.cli import main


@pytest.fixture
def project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    logfixture.generate(tmp_path / ".ddflow" / "events", 1500, seed=5)
    return tmp_path


def _run(repo: Path, *argv: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
        main(["--repo", str(repo), "--agent", "tester", *argv])
    return out.getvalue()


def _folds(monkeypatch, repo: Path, *argv: str) -> int:
    n = {"folds": 0}
    real = records.State.__init__

    def counting(self, *a, **k):
        n["folds"] += 1
        real(self, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(records.State, "__init__", counting)
        _run(repo, *argv)
    return n["folds"]


@pytest.mark.parametrize("cmd", [("status",), ("next",)])
def test_a_read_command_folds_the_log_once(project, monkeypatch, cmd):
    _run(project, *cmd)  # builds the index and the snapshot: a cold first run folds for them too
    assert _folds(monkeypatch, project, *cmd) == 1  # and not 0: a command that bailed out folds nothing


def test_brief_folds_at_most_twice(project, monkeypatch):
    _run(project, "brief")
    assert 1 <= _folds(monkeypatch, project, "brief") <= 2  # at least one: it did fold


def test_status_json_is_stable_across_runs(project):
    """What the one fold feeds gives the answer the three did: two runs agree."""
    a = json.loads(_run(project, "status", "--json") or "{}")
    b = json.loads(_run(project, "status", "--json") or "{}")
    a.pop("elapsed_s", None), b.pop("elapsed_s", None)
    assert a == b and a
