"""Every long operation renews the caller's lease while it runs (DX-long-op-renew):
a gate command, a review's wait, and the CI run (`ci run`, the after-merge check)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _wait import scaled
from conftest import run_cli

from ddflow.api import ci as API_CI
from ddflow.api import cirun
from ddflow.api import lifecycle as LC
from ddflow.config import Config
from ddflow.infra.log import EventLog
from ddflow.services import ci as CI
from ddflow.services import review as RV
from ddflow.services.gates import runner as GR
from ddflow.services.gates.defs import GateDef


def _renewals(repo: Path, agent: str = "me") -> int:
    return sum(1 for e in EventLog(repo, agent).read_all() if e.kind == "lease.renewed")


def _held(repo: Path, monkeypatch, agent: str = "me") -> None:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": ["src/a.py"]})
    assert LC.claim(repo, "T1", agent=agent).exit == 0
    assert run_cli(repo, "config", "lease.heartbeat_s", "1")[0] == 0
    assert run_cli(repo, "config", "lease.renew_log_min_s", "0")[0] == 0


def _until_ticked(tmp_path: Path, want: int = 2):
    """(a shell command that ends only when the test has seen ``want`` ticks, the tick,
    the count): no guessed sleep, so a loaded machine cannot fail a correct tick."""
    done = tmp_path / "ticked"
    seen: list[int] = []

    def tick() -> None:
        seen.append(1)
        if len(seen) >= want:
            done.write_text("x")

    ceiling = int(scaled(30) / 0.05)
    cmd = f"n=0; while [ ! -e {done} ] && [ $n -lt {ceiling} ]; do sleep 0.05; n=$((n+1)); done"
    return cmd, tick, seen


def test_a_ci_run_ticks_while_the_command_runs(repo, monkeypatch, tmp_path):
    """Fails on the unfixed code: `CI.run` had no tick and ran its command blind."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    cmd, tick, seen = _until_ticked(tmp_path)
    CI.run(repo, Config.load(repo), command=cmd, tick=(tick, 0.2))
    assert len(seen) >= 2, seen


def test_an_item_ci_run_renews_the_holders_lease(repo, monkeypatch):
    """Fails on the unfixed code: `ddflow ci run` for a claimed item never renewed."""
    _held(repo, monkeypatch)
    before = _renewals(repo)
    out = cirun.run(repo, ref="T1", command="sleep 2.6", agent="me")
    assert out.data.get("status") == "passed", out
    assert _renewals(repo) > before


def test_no_tick_for_a_lease_the_caller_does_not_hold(repo, monkeypatch):
    _held(repo, monkeypatch)
    log, cfg, st = API_CI._load(repo, "someone-else")
    assert API_CI.lease_tick(log, cfg, st, "T1") is None
    assert API_CI.lease_tick(log, cfg, st, "") is None


def test_a_command_gate_ticks_while_it_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    cmd, tick, seen = _until_ticked(tmp_path)
    outcome, _ev = GR.run_command_gate(
        GateDef(id="t", command=cmd), tmp_path, on_tick=tick, tick_s=0.2
    )
    assert outcome == "passed" and len(seen) >= 2, seen


def test_a_gate_run_renews_the_holders_lease(repo, monkeypatch):
    """The wiring, not just the runner: `gate run` hands the runner the holder's keeper.
    The gate command ends only once a renewal is in the log."""
    _held(repo, monkeypatch)
    ceiling = int(scaled(30) / 0.05)
    events = repo / ".ddflow" / "events"
    before = _renewals(repo)
    count = f"$(cat {events}/* | grep -o lease.renewed | wc -l)"
    wait = (
        f"n=0; while [ {count} -le {before} ] && [ $n -lt {ceiling} ]; "
        "do sleep 0.05; n=$((n+1)); done"
    )
    assert run_cli(repo, "config", "gate.unit_tests.command", wait)[0] == 0
    run_cli(repo, "gate", "run", "T1", "unit_tests", "--agent", "me")
    assert _renewals(repo) > before


def test_a_review_ticker_renews_the_holders_lease(repo, monkeypatch):
    """The wiring of `ddflow review`: its tick is the holder's throttled keeper."""
    import time

    from ddflow.api import review as API_RV

    _held(repo, monkeypatch)
    log, cfg, st = API_CI._load(repo, "me")
    tick = API_RV._lease_ticker(log, cfg, st.items["T1"], 0.2)
    assert tick is not None
    before = _renewals(repo)
    time.sleep(1.1)
    tick()
    assert _renewals(repo) > before
    other, cfg, st = API_CI._load(repo, "someone-else")
    assert API_RV._lease_ticker(other, cfg, st.items["T1"], 0.2) is None


def test_a_review_waiting_on_a_slow_reviewer_ticks(monkeypatch, tmp_path):
    import time

    seen: list[int] = []

    def slow(rev, system, user, timeout_s, tree=None):
        end = time.time() + scaled(30)
        while len(seen) < 2 and time.time() < end:
            time.sleep(0.05)
        return "STATUS: NO FINDINGS", ""

    monkeypatch.setattr(RV, "_chat", slow)
    rev = RV.Reviewer(name="r", base_url="http://127.0.0.1:9/v1", model="m")
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n"
    RV.review(rev, diff, "i", on_tick=lambda: seen.append(1), tick_s=0.2)
    assert len(seen) >= 2, seen
