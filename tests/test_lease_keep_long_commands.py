"""A long command renews its caller's lease while it runs (DX-lease-renew-in-long-commands),
and that renewal is liveness, never progress (D-lease-hold-limit)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _wait import scaled, wait_until
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import leasebreak as LB
from ddflow.services import leasekeep as LK
from ddflow.services import pins as PN


def _held(repo: Path, agent: str = "me"):
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": ["src/a.py"]})
    assert LC.claim(repo, "T1", agent=agent, no_worktree=True).exit == 0
    mine = EventLog(repo, agent)
    return mine, Config.load(repo)


def test_a_pins_recipe_ticks_while_it_runs(repo, monkeypatch):
    """Fails on the unfixed code: `regenerate` had no tick and ran its commands blind."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    seen: list[int] = []
    rec = PN.Recipe("p", ("x",), ("sleep", "2.4"), ("true",), ())
    rep = PN.regenerate(repo, Config.load(repo), recipes=[rec], tick=(lambda: seen.append(1), 1.0))
    assert rep.problem == "" and not rep.pins[0].stale
    assert len(seen) >= 2, seen


def test_a_pins_recipe_still_reports_its_failure_with_a_tick(repo, monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    rec = PN.Recipe("p", ("x",), ("false",), ("false",), ())
    rep = PN.regenerate(repo, Config.load(repo), recipes=[rec], tick=(lambda: None, 1.0))
    assert rep.pins[0].stale and rep.pins[0].error


def test_for_holder_renews_the_callers_live_leases_and_nobody_elses(repo):
    log, cfg = _held(repo)
    st = fold(log.read_all())
    tick, every = LK.for_holder(log, cfg, st)
    assert tick is not None and every >= 1
    other = EventLog(repo, "someone-else")
    assert LK.for_holder(other, cfg, st) == (None, 0.0)


def test_the_knob_turns_the_renewal_off(repo):
    log, cfg = _held(repo)
    it = fold(log.read_all()).items["T1"]
    assert LK.keeper(log, cfg, it) is not None
    cfg.lease.renew_long_commands = False
    assert LK.keeper(log, cfg, it) is None
    assert LK.for_holder(log, cfg, fold(log.read_all())) == (None, 0.0)


def test_a_renewing_long_command_is_not_progress(repo):
    """The stalled holder's watermark does not move however often the command renews."""
    log, cfg = _held(repo)
    st = fold(log.read_all())
    lz = st.items["T1"].lease
    before = LB.progress_at(log.read_all(), "T1", lz, lambda _l: 0.0)
    keep = LK.keeper(log, cfg, st.items["T1"])
    for _ in range(3):
        keep()
        time.sleep(0.01)
    events = log.read_all()
    assert LB.progress_at(events, "T1", lz, lambda _l: 0.0) == before


def _regenerate_until_renewed(repo, tmp_path, monkeypatch, renewals: int, first_renewal_delay=0.0):
    """Run `pins_regenerate` with a recipe that ends only when the test has SEEN the wanted
    renewals in the log (a file it writes), not after a guessed 2.4 s: under load the
    keeper ticked once in that time (0, 1) in the ci gate. ``first_renewal_delay`` injects a
    slow keeper."""
    import threading

    from ddflow.api import pin_files as PF

    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    _log, cfg = _held(repo)
    done = tmp_path / "enough-renewals"
    ceiling = int(scaled(30) / 0.05)
    wait = f"n=0; while [ ! -e {done} ] && [ $n -lt {ceiling} ]; do sleep 0.05; n=$((n+1)); done"
    recipes = repo / cfg.pins.recipes_file
    recipes.parent.mkdir(parents=True, exist_ok=True)
    recipes.write_text(
        f'[[pin]]\nname = "slow"\nfiles = ["x"]\ncheck = ["sh", "-c", "{wait}"]\nregen = ["true"]\n'
    )
    assert run_cli(repo, "config", "lease.heartbeat_s", "1")[0] == 0
    assert run_cli(repo, "config", "lease.renew_log_min_s", "0")[0] == 0
    count = lambda: sum(  # noqa: E731
        1 for e in EventLog(repo, "me").read_all() if e.kind == "lease.renewed"
    )
    before = count()
    if first_renewal_delay:
        real = LK.keeper

        def slow_keeper(*a, **k):
            keep = real(*a, **k)
            state = {"first": True}

            def delayed():
                if state.pop("first", False):
                    time.sleep(first_renewal_delay)
                keep()

            return delayed

        monkeypatch.setattr(LK, "keeper", slow_keeper)

    def watch():
        try:
            wait_until(lambda: count() - before >= renewals, 30, what="the renewals")
        except AssertionError:
            pass  # the recipe's own ceiling ends the run; the assertion below reports it
        done.write_text("x")

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    out = PF.pins_regenerate(repo, agent="me")
    watcher.join(scaled(5))
    assert out.exit == 0, out.reason
    assert count() - before >= renewals, (before, count())


def test_pins_regenerate_renews_the_callers_lease_while_a_recipe_runs(repo, tmp_path, monkeypatch):
    """End to end: the API hands the recipe loop the caller's lease keeper. The recipe runs
    until the renewals are in the log, so a loaded machine only makes it longer."""
    _regenerate_until_renewed(repo, tmp_path, monkeypatch, 2)


def test_a_keeper_that_ticks_late_still_renews_during_a_long_recipe(repo, tmp_path, monkeypatch):
    """The slow path forced: the first renewal is 3 s late. The old fixed 2.4 s recipe ended
    before it and counted (0, 1)."""
    _regenerate_until_renewed(repo, tmp_path, monkeypatch, 2, first_renewal_delay=3.0)
