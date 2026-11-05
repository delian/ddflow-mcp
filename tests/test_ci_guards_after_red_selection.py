"""B-ci-guards-unran-blocks-red-main: a failed selection no longer stops the always-run guards.

`_guards_unran` refuses a baseline-red ci whose guards never ran (B47a1dbeba0), and the runner
skipped them whenever the selection failed, so a selection that reached a test red on main could
never conclude. The guards now run after a selection that ran and failed. The two sides of that:
a red main does not fail an item that caused nothing, and a change that breaks something still
fails, whether the guards pass, fail, or end without a verdict."""

from __future__ import annotations

import os
import stat
from types import SimpleNamespace as NS

import pytest
from test_ci_selected_scope import _branch, _calls, _write, project  # noqa: F401

from ddflow.api import cirun as A
from ddflow.api import gate_baseline as GB
from ddflow.config import Config
from ddflow.services import ci as CI
from ddflow.services.cmdrunner import TIMEOUT, CommandRun, CommandRunner
from ddflow.services.gates import baseline as BL
from ddflow.services.gates import baseline_red as BR

SELECTED = "tests/test_mod.py::test_a"
GUARD_FILE = "tests/test_constants_ratchet.py"
GUARD = f"{GUARD_FILE}::test_guard"

#: A pytest that fails the selection unless `pass-selected` exists. The guard stage answers by
#: which of `guard-fail` (a failing test), `guard-crash` (no test listed) or `guard-interrupted`
#: (a collection error that stopped the stage) exists.
PYTEST = f"""\
echo "$@" >> "$LOG/pytest"
case "$*" in
  *{GUARD_FILE}*)
    [ -e "$LOG/guard-crash" ] && {{ echo 'INTERNALERROR> boom'; exit 3; }}
    [ -e "$LOG/guard-interrupted" ] && {{ echo 'ERROR {GUARD_FILE} - boom'; exit 2; }}
    [ -e "$LOG/guard-fail" ] && {{ echo 'FAILED {GUARD} - new'; echo '1 failed'; exit 1; }}
    echo '1 passed'; exit 0;;
esac
[ -e "$LOG/pass-selected" ] && {{ echo '1 passed'; exit 0; }}
echo 'FAILED {SELECTED} - red on main'; echo '1 failed'; exit 1
"""


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    bin_dir, log = tmp_path / "bin", tmp_path / "log"
    bin_dir.mkdir()
    log.mkdir()
    hooks = f"[ -e {log}/fail-hooks ] && {{ echo 'ruff check....Failed'; exit 1; }}\n"
    for name, body in (
        ("pre-commit", hooks + "echo 'ruff check....Passed'\n"),
        ("pytest", f"LOG={log}\n{PYTEST}"),
    ):
        f = bin_dir / name
        f.write_text("#!/bin/sh\n" + body)
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv(A.IN_GATE_ENV, raising=False)
    return log


@pytest.fixture
def work(project):  # noqa: F811
    _write(project, {GUARD_FILE: "def test_guard():\n    assert True\n"})
    _branch(project, {"pkg/mod.py": "A = 2\n"})
    return project


def _ci(repo):
    return CI.run(repo, Config.load(repo), base="main", for_item=True)


def _gate(repo, res, monkeypatch, red_on_base):
    """`gate run`'s settling of ``res``, with ``red_on_base`` the ids the base fails too."""
    ids = res.failures["failed_tests"]
    red = set(ids) <= set(red_on_base)
    found = {"red": red, "still_failing": list(red_on_base), "base": "main", "base_sha": "a" * 40}
    monkeypatch.setattr(GB, "_ci_ids", lambda *a: ids)
    monkeypatch.setattr(GB, "_unit_tests_def", lambda *a: NS(command="pytest"))
    monkeypatch.setattr(GB, "_linked", lambda *a: "B1")
    monkeypatch.setattr(BR, "on_base", lambda *a: found)
    monkeypatch.setattr(BL, "auto_decision", lambda *a: {})
    ev = {"ci_scope": res.scope, **res.failures}
    it, gdef = NS(id="T1", base="main"), NS(command="ddflow ci run")
    return GB.apply(repo, Config.load(repo), "a", it, "ci", gdef, res.status, ev, None, False)


def test_a_baseline_red_selection_concludes_once_its_guards_pass(work, stubs, monkeypatch):
    """The bug: every selected failure is the base's and the guards are clean, so the run is
    baseline-red; it used to be failed, whatever the change, for guards that were never run."""
    res = _ci(work)
    assert res.scope["ran"] == {"hooks": True, "selected": False, "always": True}
    outcome, why = _gate(work, res, monkeypatch, [SELECTED])
    assert (outcome, "always-run guards" in why) == ("partial", False), why


def test_passing_guards_do_not_turn_a_failed_selection_into_a_pass(work, stubs):
    res = _ci(work)
    assert res.status == "failed" and not res.ok
    assert res.failures["failed_tests"] == [SELECTED]
    assert "exited 1" in res.reason
    assert "selected tests failed" in res.scope["why"]
    assert "always-run guards passed" in res.scope["why"]
    selected, guards = _calls(stubs, "pytest")
    assert "test_mod.py" in selected and GUARD_FILE in guards


def test_a_new_selected_failure_still_fails_the_gate(work, stubs, monkeypatch):
    outcome, why = _gate(work, _ci(work), monkeypatch, [])
    assert outcome == "failed" and "new failure" in why


def test_a_new_guard_failure_behind_a_baseline_red_selection_fails_the_gate(
    work, stubs, monkeypatch
):
    (stubs / "guard-fail").write_text("")
    res = _ci(work)
    assert res.status == "failed" and res.scope["ran"]["always"] is False
    assert res.failures["failed_tests"] == [SELECTED, GUARD]
    assert "always-run guards failed" in res.scope["why"]
    outcome, why = _gate(work, res, monkeypatch, [SELECTED])
    assert outcome == "failed" and GUARD in why


def test_guards_red_on_the_base_too_are_baseline_red(work, stubs, monkeypatch):
    (stubs / "guard-fail").write_text("")
    outcome, _why = _gate(work, _ci(work), monkeypatch, [SELECTED, GUARD])
    assert outcome == "partial"


def _not_baseline_red(repo, res, monkeypatch):
    """The guards gave no verdict: recorded as such, and the run keeps its failure."""
    assert res.status == "failed" and not res.ok
    assert res.scope["ran"]["always"] == "unavailable"
    assert "always-run guards gave no verdict" in res.scope["why"]
    outcome, why = _gate(repo, res, monkeypatch, [SELECTED])
    assert outcome == "failed" and "always-run guards never ran" in why


def test_guards_that_time_out_leave_the_failed_selection_failed(work, stubs, monkeypatch):
    """Not `unavailable` for the whole run: the selection's failure is a verdict, and the guards'
    missing one is recorded as missing, never as passed."""
    real = CommandRunner.run

    def run(self, declared, **kw):
        if GUARD_FILE in str(declared):
            return CommandRun("unavailable", str(declared), TIMEOUT, "timed out after 60s")
        return real(self, declared, **kw)

    monkeypatch.setattr(CommandRunner, "run", run)
    _not_baseline_red(work, _ci(work), monkeypatch)


def test_guards_that_crash_listing_no_test_are_no_verdict(work, stubs, monkeypatch):
    """`_guards_unran` trusts that a guard which ran red lists its failure among the ids. A crash
    lists none, so with the selection red too it would pass for 'nothing new'."""
    (stubs / "guard-crash").write_text("")
    _not_baseline_red(work, _ci(work), monkeypatch)


def test_guards_stopped_by_a_collection_error_are_no_verdict(work, stubs, monkeypatch):
    """pytest stops at a collection error, so the listed id says nothing of the guards behind it,
    even when the base shares it."""
    (stubs / "guard-interrupted").write_text("")
    res = _ci(work)
    assert res.failures["failed_tests"] == [SELECTED, GUARD_FILE]
    assert res.scope["ran"]["always"] == "unavailable"
    outcome, why = _gate(work, res, monkeypatch, [SELECTED, GUARD_FILE])
    assert outcome == "failed" and "always-run guards never ran" in why


def test_guards_that_crash_after_a_green_selection_stay_a_plain_failure(work, stubs):
    (stubs / "guard-crash").write_text("")
    (stubs / "pass-selected").write_text("")
    res = _ci(work)
    assert res.status == "failed"
    assert res.scope["ran"] == {"hooks": True, "selected": True, "always": False}


def test_failed_hooks_still_stop_every_test_stage(work, stubs):
    (stubs / "fail-hooks").write_text("")
    res = _ci(work)
    assert res.status == "failed" and not _calls(stubs, "pytest")
    assert res.scope["ran"] == {"hooks": False}


def test_a_selection_that_could_not_start_stays_unavailable_and_runs_no_guards(
    work, stubs, monkeypatch
):
    real, seen = CommandRunner.run, []

    def run(self, declared, **kw):
        if "test_mod.py" in str(declared):
            return CommandRun("unavailable", str(declared), TIMEOUT, "timed out after 60s")
        seen.append(str(declared))
        return real(self, declared, **kw)

    monkeypatch.setattr(CommandRunner, "run", run)
    res = _ci(work)
    assert res.status == "unavailable"
    assert not any(GUARD_FILE in c for c in seen)
