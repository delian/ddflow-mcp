"""A long command renews its caller's lease while it runs (DX-lease-renew-in-long-commands),
and that renewal is liveness, never progress (D-lease-hold-limit)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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


def test_a_pins_recipe_ticks_while_it_runs(repo):
    """Fails on the unfixed code: `regenerate` had no tick and ran its commands blind."""
    seen: list[int] = []
    rec = PN.Recipe("p", ("x",), ("sleep", "2.4"), ("true",), ())
    rep = PN.regenerate(repo, Config.load(repo), recipes=[rec], tick=(lambda: seen.append(1), 1.0))
    assert rep.problem == "" and not rep.pins[0].stale
    assert len(seen) >= 2, seen


def test_a_pins_recipe_still_reports_its_failure_with_a_tick(repo):
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


def test_pins_regenerate_renews_the_callers_lease_while_a_recipe_runs(repo):
    """End to end: the API hands the recipe loop the caller's lease keeper."""
    from ddflow.api import pin_files as PF

    log, cfg = _held(repo)
    recipes = repo / cfg.pins.recipes_file
    recipes.parent.mkdir(parents=True, exist_ok=True)
    recipes.write_text(
        '[[pin]]\nname = "slow"\nfiles = ["x"]\ncheck = ["sleep", "2.4"]\nregen = ["true"]\n'
    )
    assert run_cli(repo, "config", "lease.heartbeat_s", "1")[0] == 0
    assert run_cli(repo, "config", "lease.renew_log_min_s", "0")[0] == 0
    before = sum(1 for e in log.read_all() if e.kind == "lease.renewed")
    out = PF.pins_regenerate(repo, agent="me")
    assert out.exit == 0, out.reason
    after = sum(1 for e in EventLog(repo, "me").read_all() if e.kind == "lease.renewed")
    assert after - before >= 2, (before, after)
