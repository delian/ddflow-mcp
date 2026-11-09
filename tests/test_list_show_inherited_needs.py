"""B8981fa81bd: `ddflow list` and `ddflow show` report the phase needs a task inherits.

A task's effective dependencies are its own `needs` plus every phase ancestor's — the
graph readiness already walks (`core.schedule.inherited_deps`). `list` and `show` used to
print only the task's own `needs`, so a dependency closure built from either undercounts
(68 reported against 158 tasks plus 6 phases for P-swarm). These tests declare the
phase-level dependency once, on the phase, and assert the task reports it — they never
re-declare it on the task, so the assertion proves inheritance and not a restatement of
the input.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _project(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P0", "--title", "base", "--globs", "base/**")
    run_cli(
        repo,
        "phase",
        "add",
        "P1",
        "--title",
        "api",
        "--needs",
        "P0",
        "--globs",
        "api/**",
    )
    # The dependency is declared ONCE, on the phase; the task's own needs stay empty.
    run_cli(
        repo,
        "task",
        "add",
        "P1.T1",
        "--title",
        "api task",
        "--phase",
        "P1",
        "--globs",
        "api/t1.py",
    )


def _task_row(repo):
    code, out, err = run_cli(repo, "--json", "task", "list")
    assert code == 0, err
    rows = json.loads(out)["rows"]
    return next(r for r in rows if r["id"] == "P1.T1")


def test_task_list_reports_the_phase_needs_a_task_inherits(repo):
    _project(repo)
    assert _task_row(repo)["needs"] == ["P0"]


def test_show_reports_the_phase_needs_a_task_inherits(repo):
    _project(repo)
    code, out, err = run_cli(repo, "--json", "show", "P1.T1")
    assert code == 0, err
    assert json.loads(out)["item"]["needs"] == ["P0"]
