"""B-uni-local-worker.4-ticks: the tick registry (`services.ticks`).

One opportunistic call site (`api._base._load`) runs whatever is due, each tick budgeted:
due per machine, claimed before it runs, cut when it overruns, deferred when the pass is out
of time, never raising. A fake clock stands in for time everywhere a test reads it.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import run_cli

from ddflow.infra import fsio
from ddflow.services import ticks as TK


class Clock:
    """A wall clock and a monotonic clock a test moves by hand."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now
        self.mono = 0.0

    def __call__(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self.mono += seconds


@pytest.fixture
def proj(repo: Path) -> Path:
    assert run_cli(repo, "init")[0] == 0
    return repo


def ctx(repo: Path) -> TK.TickCtx:
    return TK.TickCtx(repo, SimpleNamespace(), None)


def run(repo: Path, ticks: list[TK.Tick], clock: Clock, **kw) -> list[TK.TickResult]:
    return TK.run_due(ctx(repo), ticks=ticks, clock=clock, mono=clock.monotonic, **kw)


def counting(log: list[str], name: str = "a", note: str | None = None):
    def hook(c: TK.TickCtx) -> str | None:
        log.append(name)
        return note

    return hook


def state_rows(repo: Path) -> dict:
    return json.loads((repo / ".ddflow/local/ticks.json").read_text())["ticks"]


# -- the registry ----------------------------------------------------------------------------


def test_registering_the_same_definition_twice_is_a_noop_and_another_one_is_an_error() -> None:
    t = TK.Tick("zz.test", every_s=5, budget_s=1, target=lambda c: None)
    try:
        assert TK.register(t) is t and TK.register(t) is t
        with pytest.raises(ValueError, match="already registered"):
            TK.register(TK.Tick("zz.test", every_s=9, budget_s=1, target=lambda c: None))
    finally:
        TK.unregister("zz.test")
    assert "zz.test" not in TK.REGISTRY


def test_a_tick_needs_a_name_and_a_positive_budget() -> None:
    with pytest.raises(ValueError):
        TK.Tick("", every_s=1, budget_s=1, target=lambda c: None)
    with pytest.raises(ValueError):
        TK.Tick("x", every_s=-1, budget_s=1, target=lambda c: None)
    with pytest.raises(ValueError):
        TK.Tick("x", every_s=1, budget_s=0, target=lambda c: None)


def test_a_target_given_by_name_is_imported_only_when_it_runs(proj: Path) -> None:
    t = TK.Tick("zz.named", every_s=0, budget_s=1, target="ddflow.services.ticks:flow_sample")
    assert callable(t.resolve())
    bad = TK.Tick("zz.bad", every_s=0, budget_s=1, target="no.such.module:fn")
    clock = Clock()

    (res,) = run(proj, [bad], clock)

    assert res.status == TK.FAILED and "ModuleNotFoundError" in res.detail


def test_the_flow_sample_is_the_one_tick_ddflow_ships() -> None:
    assert [t.name for t in TK.registered()] == ["flow.sample"]


# -- due, claimed, recorded ----------------------------------------------------------------


def test_a_tick_runs_when_due_and_not_again_until_every_s_has_passed(proj: Path) -> None:
    clock, log = Clock(), []
    t = TK.Tick("a", every_s=60, budget_s=1, target=counting(log, "a", "did it"))

    first = run(proj, [t], clock)
    clock.advance(59)
    again = run(proj, [t], clock)
    clock.advance(2)
    later = run(proj, [t], clock)

    assert [r.status for r in first] == [TK.RAN] and first[0].detail == "did it"
    assert again == []
    assert [r.status for r in later] == [TK.RAN]
    assert log == ["a", "a"]
    assert state_rows(proj)["a"]["status"] == TK.RAN


def test_a_tick_another_command_already_claimed_is_not_run_twice(proj: Path) -> None:
    clock, log = Clock(), []
    t = TK.Tick("a", every_s=60, budget_s=1, target=counting(log))
    fsio.ensure_ignored_dir(proj / ".ddflow/local")
    (proj / ".ddflow/local/ticks.json").write_text(
        json.dumps({"format": 1, "ticks": {"a": {"last_at": clock.now - 5, "status": "running"}}})
    )

    assert run(proj, [t], clock) == [] and log == []


def test_two_passes_racing_for_one_tick_run_it_once(proj: Path) -> None:
    clock, log = Clock(), []
    gate = threading.Barrier(2)

    def hook(c: TK.TickCtx) -> None:
        log.append("ran")

    t = TK.Tick("a", every_s=60, budget_s=2, target=hook)

    def one() -> None:
        gate.wait()
        run(proj, [t], clock)

    threads = [threading.Thread(target=one) for _ in range(2)]
    [th.start() for th in threads]
    [th.join() for th in threads]

    assert log == ["ran"]


def test_the_clock_going_back_never_runs_a_tick_twice(proj: Path) -> None:
    clock, log = Clock(), []
    t = TK.Tick("a", every_s=60, budget_s=1, target=counting(log))
    run(proj, [t], clock)
    clock.now -= 3600

    assert run(proj, [t], clock) == [] and log == ["a"]


def test_ticks_that_do_not_apply_are_left_out(proj: Path) -> None:
    clock, log = Clock(), []
    off = TK.Tick(
        "off", every_s=0, budget_s=1, target=counting(log, "off"), enabled=lambda c: False
    )
    boom = TK.Tick(
        "boom", every_s=0, budget_s=1, target=counting(log, "boom"), enabled=lambda c: 1 / 0
    )

    assert run(proj, [off, boom], clock) == [] and log == []


def test_a_zero_interval_tick_is_not_recorded_while_it_succeeds(proj: Path) -> None:
    clock, log = Clock(), []
    t = TK.Tick("a", every_s=0, budget_s=1, target=counting(log))

    for _ in range(3):
        run(proj, [t], clock)

    assert log == ["a", "a", "a"]
    assert not (proj / ".ddflow/local/ticks.json").exists(), "no state write per command"


def test_status_lists_every_registered_tick_with_what_it_last_did(proj: Path) -> None:
    clock = Clock()
    t = TK.Tick("a", every_s=60, budget_s=1, target=lambda c: "noted")
    TK.register(t)
    try:
        before = {r["name"]: r for r in TK.status(proj)}
        run(proj, [t], clock)
        after = {r["name"]: r for r in TK.status(proj)}
    finally:
        TK.unregister("a")

    assert before["a"]["status"] == "never"
    assert after["a"]["status"] == TK.RAN and after["a"]["detail"] == "noted"
    assert after["a"]["last_at"] == clock.now


# -- budgets -------------------------------------------------------------------------------


def test_a_tick_that_overruns_is_cut_and_noted_and_the_pass_goes_on(proj: Path) -> None:
    clock, log = Clock(), []
    release = threading.Event()

    def stuck(c: TK.TickCtx) -> None:
        release.wait(5)

    slow = TK.Tick("slow", every_s=60, budget_s=0.05, target=stuck)
    fast = TK.Tick("fast", every_s=60, budget_s=1, target=counting(log, "fast"))
    started = time.monotonic()
    try:
        results = run(proj, [slow, fast], clock)
    finally:
        release.set()

    assert time.monotonic() - started < 2.0, "the command did not wait for the stuck tick"
    assert [(r.name, r.status) for r in results] == [("slow", TK.CUT), ("fast", TK.RAN)]
    assert "went on without it" in results[0].detail
    rows = state_rows(proj)
    assert rows["slow"]["status"] == TK.CUT and rows["slow"]["cut"] == 1
    assert log == ["fast"]
    # cut once, it is still due only after its interval: no retry storm
    clock.advance(10)
    assert run(proj, [slow, fast], clock) == []


def test_a_cooperative_tick_sees_its_deadline(proj: Path) -> None:
    clock, seen = Clock(), []

    def polite(c: TK.TickCtx) -> None:
        seen.append((c.remaining(), c.expired()))
        clock.mono += 100  # time passes inside the tick
        seen.append((c.remaining() < 0, c.expired()))

    t = TK.Tick("p", every_s=0, budget_s=3, target=polite)
    run(proj, [t], clock)

    assert seen[0][0] == pytest.approx(3.0) and seen[0][1] is False
    assert seen[1] == (True, True)


def test_ticks_not_reached_before_the_pass_budget_is_spent_are_deferred_not_lost(
    proj: Path,
) -> None:
    clock, log = Clock(), []

    def hog(c: TK.TickCtx) -> None:
        clock.mono += 20  # the fake clock says this tick took twenty seconds
        log.append("hog")

    first = TK.Tick("a-hog", every_s=60, budget_s=30, target=hog)
    second = TK.Tick("b-late", every_s=60, budget_s=30, target=counting(log, "late"))

    results = run(proj, [first, second], clock, pass_budget_s=10)

    assert [(r.name, r.status) for r in results] == [("a-hog", TK.RAN), ("b-late", TK.DEFERRED)]
    assert log == ["hog"]
    # the deferred tick was released: the very next pass runs it
    again = run(proj, [first, second], clock, pass_budget_s=10)
    assert [(r.name, r.status) for r in again] == [("b-late", TK.RAN)] and log == ["hog", "late"]


# -- never in the way -----------------------------------------------------------------------


def test_a_failing_tick_is_recorded_and_the_others_still_run(proj: Path) -> None:
    clock, log = Clock(), []

    def broken(c: TK.TickCtx) -> None:
        raise RuntimeError("no network")

    bad = TK.Tick("a-bad", every_s=60, budget_s=1, target=broken)
    good = TK.Tick("b-good", every_s=60, budget_s=1, target=counting(log))

    results = run(proj, [bad, good], clock)

    assert [(r.name, r.status) for r in results] == [("a-bad", TK.FAILED), ("b-good", TK.RAN)]
    assert "RuntimeError: no network" in results[0].detail
    assert state_rows(proj)["a-bad"]["detail"].startswith("RuntimeError")


def test_a_failing_zero_interval_tick_is_still_recorded(proj: Path) -> None:
    clock = Clock()

    def broken(c: TK.TickCtx) -> None:
        raise OSError("disk")

    run(proj, [TK.Tick("z", every_s=0, budget_s=1, target=broken)], clock)

    assert state_rows(proj)["z"]["status"] == TK.FAILED


def test_an_unwritable_state_directory_is_unavailable_not_an_error(proj: Path) -> None:
    clock, log = Clock(), []
    (proj / ".ddflow/local").mkdir(exist_ok=True)
    (proj / ".ddflow/local/ticks.lock").mkdir()  # a directory where the lock file goes
    t = TK.Tick("a", every_s=60, budget_s=1, target=counting(log))

    (res,) = run(proj, [t], clock)

    assert res.status == TK.UNAVAILABLE and log == []


def test_a_held_state_lock_is_reported_locked_and_nothing_runs(
    proj: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, log = Clock(), []
    monkeypatch.setattr(TK, "LOCK_WAIT_S", 0.05)
    t = TK.Tick("a", every_s=60, budget_s=1, target=counting(log))
    fsio.ensure_ignored_dir(proj / ".ddflow/local")

    with fsio.file_lock(proj / ".ddflow/local" / TK.LOCK):
        (res,) = run(proj, [t], clock)

    assert res.status == TK.LOCKED and log == []
    assert [r.status for r in run(proj, [t], clock)] == [TK.RAN], "free again, it runs"


def test_a_corrupt_state_file_reads_as_empty(proj: Path) -> None:
    clock, log = Clock(), []
    fsio.ensure_ignored_dir(proj / ".ddflow/local")
    (proj / ".ddflow/local/ticks.json").write_text("{not json")
    t = TK.Tick("a", every_s=60, budget_s=1, target=counting(log))

    assert [r.status for r in run(proj, [t], clock)] == [TK.RAN]
    assert state_rows(proj)["a"]["status"] == TK.RAN


def test_a_directory_that_is_not_a_ddflow_project_gets_nothing(tmp_path: Path) -> None:
    clock, log = Clock(), []
    t = TK.Tick("a", every_s=0, budget_s=1, target=counting(log))

    assert TK.run_due(ctx(tmp_path), ticks=[t], clock=clock, mono=clock.monotonic) == []
    assert log == [] and not (tmp_path / ".ddflow").exists()


# -- the call site --------------------------------------------------------------------------


def test_load_runs_the_ticks_and_the_flow_sample_is_taken_once_per_interval(proj: Path) -> None:
    from ddflow.api._base import _load

    assert run_cli(proj, "config", "--set", "schedule.parallel", "auto")[0] == 0
    ring = proj / ".ddflow/local/flow"

    _load(proj)
    first = sorted(p.name for p in ring.glob("*")) if ring.is_dir() else []
    _load(proj)
    second = sorted(p.name for p in ring.glob("*")) if ring.is_dir() else []

    assert first, "the first load took a sample"
    assert second == first, "the second, inside signal_interval_s, did not take another"


def test_load_does_not_sample_when_parallel_is_not_auto(proj: Path) -> None:
    from ddflow.api._base import _load

    assert run_cli(proj, "config", "--set", "schedule.parallel", "fixed")[0] == 0
    shutil.rmtree(proj / ".ddflow/local", ignore_errors=True)  # the set itself sampled, under auto

    _load(proj)

    assert not (proj / ".ddflow/local/flow").exists()


def test_load_survives_a_registry_that_raises(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ddflow.api._base import _load

    def boom(*a, **k):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(TK, "run_due", boom)

    log, cfg, st = _load(proj)

    assert log is not None and cfg is not None and st is not None
