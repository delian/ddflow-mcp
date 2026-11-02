"""A command gate's verdict, when the tool's exit code does not simply mean pass/fail.

The cross-family critic both projects rely on exits 2 when its endpoint is down and 3
when it reviewed only part of the diff; ddflow recorded both as FAILED, which sends the
author off to fix code nobody reviewed. roborev exits 0 while reporting findings, and a
critic can exit 0 having degenerated into nothing -- both recorded as PASSED. And a
25-minute suite outlived the 30-minute lease budget's heartbeat, because nothing renewed
the lease while the gate ran.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services.gates import GateDef, classify_exit

OK, FAIL, NOTHING = 0, 1, 2


def _gate(**kw) -> GateDef:
    return GateDef(id="critic_cmd", command="x", **kw)


def test_declared_exit_codes_are_unavailable_and_partial_not_failed():
    g = _gate(unavailable_exits=[2, 143], partial_exits=[3])
    assert classify_exit(g, 2, "")[0] == "unavailable"
    assert classify_exit(g, 143, "")[0] == "unavailable", "a SIGTERM'd critic reviewed nothing"
    assert classify_exit(g, 3, "")[0] == "partial"
    assert classify_exit(g, 1, "") == ("failed", "")
    assert classify_exit(g, 0, "") == ("passed", "")
    assert classify_exit(_gate(), 2, "")[0] == "failed", "undeclared codes keep their meaning"


def test_a_signal_killed_command_is_unavailable_not_failed():
    # Python reports a signalled direct child as -N: that is the trustworthy form (a shell's
    # 128+N is ambiguous with a command that exits that code itself). Either way a cut-off
    # run must read as unavailable, not as "queued ... elapsed" (B8664a1e0c6).
    for code, sig in ((-9, "SIGKILL"), (-15, "SIGTERM")):
        outcome, why = classify_exit(_gate(), code, "")
        assert outcome == "unavailable", (code, why)
        assert sig in why, (code, why)
    assert classify_exit(_gate(), 1, "") == ("failed", "")


def test_a_positive_shell_exit_is_not_read_as_a_signal_death():
    # `sh -c 'exit 137'` ran to a verdict and exited 137; no signal was delivered. It must
    # read as FAILED, not as "killed by SIGKILL" (a shell's 128+N is ambiguous). 129 is
    # SIGHUP's shell form (git exits 129 for a usage error): it is a cut-off signal, so no
    # positive cut-off shell exit may be read as a signal death either.
    assert classify_exit(_gate(), 129, "")[0] == "failed"
    assert classify_exit(_gate(), 137, "")[0] == "failed"
    assert classify_exit(_gate(), 143, "")[0] == "failed"


def test_an_ordinal_high_exit_is_not_read_as_a_signal_death():
    # 128 (no signal 0) and the far tail (255 = 128+127, no such signal) are a program's
    # own exit status, not a cut-off: misreading them would downgrade a real failure.
    assert classify_exit(_gate(), 128, "")[0] == "failed"
    assert classify_exit(_gate(), 255, "")[0] == "failed"


def test_a_crash_signal_is_a_failure_not_a_cut_off():
    # SIGSEGV (139) and SIGABRT (134) are the command's own process crashing, not a
    # harness cutting it off: they must read as FAILED, not UNAVAILABLE.
    assert classify_exit(_gate(), -11, "")[0] == "failed"  # SIGSEGV
    assert classify_exit(_gate(), 139, "")[0] == "failed"
    assert classify_exit(_gate(), 134, "")[0] == "failed"  # SIGABRT


def test_a_signal_killed_ci_run_is_unavailable_and_never_a_queued_note(tmp_path):
    from ddflow.services import ci_marker as CM
    from ddflow.services.gates import runner as R

    # `ddflow ci run` printed its suite-lock wait, then was killed mid-suite: 11m queued
    # of the run, no verdict. It must read as a kill, never as "queued ... elapsed".
    gdef = GateDef(id="ci", command=f"echo {CM.wait_line(660)}; kill -KILL $$")
    outcome, ev = R.run_command_gate(gdef, tmp_path)
    assert outcome == "unavailable", outcome
    assert "killed by SIGKILL" in ev.get("reason", ""), ev
    assert ev.get("waited_s") == 660.0


def test_output_patterns_decide_an_exit_0():
    need = _gate(require_output=r"^STATUS:")
    assert classify_exit(need, 0, "STATUS: reviewed 4 files\n")[0] == "passed"
    outcome, why = classify_exit(need, 0, "…\n")
    assert outcome == "unavailable" and "NOT a pass" in why
    findings = _gate(fail_output=r"^\s*- \[(HIGH|MEDIUM)\]")
    assert classify_exit(findings, 0, "  - [HIGH] null deref\n")[0] == "failed"
    assert classify_exit(findings, 0, "no findings\n")[0] == "passed"
    assert classify_exit(findings, 1, "no findings\n")[0] == "failed", "a real failure stays one"


def test_a_pattern_that_does_not_compile_decides_nothing():
    outcome, why = classify_exit(_gate(require_output="(unclosed"), 0, "anything")
    assert outcome == "unavailable" and "invalid output pattern" in why


def test_the_mapping_reaches_the_recorded_outcome_end_to_end(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "echo endpoint down; exit 2"\ncwd = "repo"\n'
        "unavailable_exits = [2]\n"
    )
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree")
    code, out, _err = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests")
    assert code == NOTHING, "unavailable is exit 2, not a failure"
    assert json.loads(out)["outcome"] == "unavailable"
    st = fold(EventLog(repo).read_all(), strict=False)
    assert st.items["T1"].gates["unit_tests"].outcome == "unavailable"


def test_a_long_gate_keeps_its_lease_alive(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text("[lease]\nheartbeat_s = 1\n")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "sleep 3.5"\ncwd = "repo"\n'
    )
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree", agent="worker")
    before = sum(1 for e in EventLog(repo).read_all() if e.kind == "lease.renewed")
    code, _out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    assert code == OK, err
    renewed = [e for e in EventLog(repo).read_all() if e.kind == "lease.renewed"]
    assert len(renewed) - before >= 2, "nothing renewed the lease while the gate ran"
    assert {e.data.get("holder") for e in renewed} == {"worker"}


def test_a_gate_run_by_someone_else_does_not_renew_a_lease_it_does_not_hold(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text("[lease]\nheartbeat_s = 1\n")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "sleep 2.5"\ncwd = "repo"\n'
    )
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree", agent="worker")
    run_cli(repo, "gate", "run", "T1", "unit_tests", agent="bystander")
    renewed = [e for e in EventLog(repo).read_all() if e.kind == "lease.renewed"]
    assert not renewed, "a bystander's gate run renewed someone else's lease"


def test_the_lease_is_renewed_on_the_CALLERS_thread(repo, monkeypatch):
    """roborev 827: a background heartbeat thread raced the event log's process-global,
    unlocked parse cache. Renewal now happens between polls, on the calling thread."""
    import threading

    from ddflow import api
    from ddflow.services import leases as L

    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text("[lease]\nheartbeat_s = 1\n")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "sleep 2.5"\ncwd = "repo"\n'
    )
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree", agent="worker")
    threads = []
    real = L.renew
    monkeypatch.setattr(
        L, "renew", lambda *a, **k: (threads.append(threading.current_thread()), real(*a, **k))[1]
    )
    out = api.gate_run(repo, "T1", "unit_tests", agent="worker")
    assert out.data["outcome"] == "passed"
    assert threads, "nothing renewed the lease"
    assert set(threads) == {threading.main_thread()}, "renewed from another thread"
