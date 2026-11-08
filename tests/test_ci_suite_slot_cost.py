"""Bc07bf8feb4: every selected ci run queued for the one host-wide suite slot. The always-run
guards alone (22 files) were counted against `[ci].selected_lock_tests`, and a file count says
nothing about what a run costs. A selected run now takes the slot on the seconds it is predicted
to run on this host now; with too few measured runs, on the count of the files its change reaches."""

from __future__ import annotations

from types import SimpleNamespace as NS

from ddflow.api import cirun as AR
from ddflow.config import Config
from ddflow.services import ci as CI
from ddflow.services import workers as WK
from ddflow.services.ci import runner
from ddflow.services.ci_scope import Plan

HOOKS = "SKIP=tests pre-commit run --hook-stage pre-push --all-files"
GUARDS = 22  #: the always-run files of this repository when the bug was found


def _scope(n_tests: int, always: int = 0):
    return NS(scope="selected", tests=[f"t{i}" for i in range(n_tests)], always=["a"] * always)


def _host(monkeypatch, load: float | None = 0.0, cores: int = 192):
    monkeypatch.setattr(
        WK.S.HostSignals, "sample", lambda self: {"load": load, "memory_free_frac": 0.9}
    )
    monkeypatch.setattr(WK.os, "cpu_count", lambda: cores)


def _rates(events, cfg):
    from ddflow.services.ci import admission

    return admission.measured_rates(events, cfg)


def _gate(kind="gate.passed", gate="ci", **evidence):
    return NS(kind=kind, data={"gate": gate, "evidence": evidence})


def _run(kind="gate.passed", elapsed=300.0, n=10, files=10, waited=None, always_s=0.0, **over):
    """A recorded selected ci run: ``elapsed`` seconds on ``n`` workers over ``files`` files."""
    scope = {
        "scope": "selected",
        "selected": files,
        "always_s": always_s,
        "ran": {"hooks": True, "selected": True},
        "workers": {"selected": {"n": n}},
    }
    ev = {"exit": 0, "elapsed_s": elapsed, "ci_scope": scope}
    if waited is not None:
        ev["waited_s"] = waited
    ev.update(over)
    return _gate(kind, **ev)


def test_a_few_files_plus_every_guard_do_not_need_the_suite_slot():
    """THE regression: the guards cost the same for every item and are not the change's load."""
    assert CI._needs_suite_slot(Config(), HOOKS, _scope(3, always=GUARDS)) is False


def test_the_guards_never_count_however_many_there_are():
    cfg = Config()
    limit = cfg.ci.selected_lock_tests
    assert CI._needs_suite_slot(cfg, HOOKS, _scope(limit, always=500)) is False
    assert CI._needs_suite_slot(cfg, HOOKS, _scope(limit + 1)) is True


def test_a_run_predicted_heavy_takes_the_slot(monkeypatch):
    _host(monkeypatch)
    cfg = Config()
    cfg.ci.max_workers = 48
    scope = _scope(200, always=GUARDS)  # 200 s a file over 48 workers: 833 s
    assert CI._needs_suite_slot(cfg, HOOKS, scope, [200.0] * 10) is True
    assert scope.admission["basis"] == "measured"
    assert scope.admission["predicted_s"] == 833.3
    assert scope.admission["workers"] == 48


def test_many_files_predicted_short_stay_off_the_slot(monkeypatch):
    """Above the file limit, and still cheap: the count no longer decides once time is known."""
    _host(monkeypatch)
    cfg = Config()
    scope = _scope(cfg.ci.selected_lock_tests * 3)
    assert CI._needs_suite_slot(cfg, HOOKS, scope, [20.0] * 10) is False
    assert scope.admission["basis"] == "measured"


def test_the_guards_add_nothing_to_the_prediction(monkeypatch):
    _host(monkeypatch)
    alone, guarded = _scope(40), _scope(40, always=500)
    CI._needs_suite_slot(Config(), HOOKS, alone, [100.0] * 10)
    CI._needs_suite_slot(Config(), HOOKS, guarded, [100.0] * 10)
    assert alone.admission["predicted_s"] == guarded.admission["predicted_s"]


def test_a_loaded_host_makes_the_same_selection_heavy(monkeypatch):
    """The load reaches admission through the worker count the run would get."""
    rates, cfg = [200.0] * 10, Config()
    _host(monkeypatch, load=0.0)
    assert CI._needs_suite_slot(cfg, HOOKS, _scope(60), rates) is False
    _host(monkeypatch, load=5.0)  # far over [schedule.signals.load_per_core].high
    scope = _scope(60)
    assert CI._needs_suite_slot(cfg, HOOKS, scope, rates) is True
    assert scope.admission["workers"] == 1 and scope.admission["load_per_core"] == 5.0


def test_a_load_that_cannot_be_read_is_not_reported_as_read(monkeypatch):
    _host(monkeypatch, load=None)
    scope = _scope(60)
    CI._needs_suite_slot(Config(), HOOKS, scope, [200.0] * 10)
    assert scope.admission["load_per_core"] is None
    assert "load unavailable" in scope.admission["workers_rule"]
    assert scope.admission["workers"] == WK.UNKNOWN_LOAD_WORKERS


def test_unknown_durations_fall_back_to_the_file_count():
    cfg = Config()
    limit = cfg.ci.selected_lock_tests
    few, many = _scope(limit), _scope(limit + 1)
    assert CI._needs_suite_slot(cfg, HOOKS, few, []) is False
    assert CI._needs_suite_slot(cfg, HOOKS, many, []) is True
    assert many.admission["basis"] == "file_count"
    assert "predicted_s" not in many.admission and "rate_s" not in many.admission


def test_too_few_measured_runs_are_not_a_measurement(monkeypatch):
    _host(monkeypatch)
    cfg = Config()
    cfg.ci.selected_lock_min_runs = 3
    limit = cfg.ci.selected_lock_tests
    scope = _scope(limit + 1)
    assert CI._needs_suite_slot(cfg, HOOKS, scope, [1.0, 1.0]) is True
    assert scope.admission["basis"] == "file_count"
    assert "2 measured run(s), 3 needed" in scope.admission["why"]
    assert CI._needs_suite_slot(cfg, HOOKS, _scope(limit + 1), [1.0] * 3) is False


def test_the_time_knob_at_zero_is_the_file_count_alone(monkeypatch):
    _host(monkeypatch)
    cfg = Config()
    cfg.ci.selected_lock_s = 0
    scope = _scope(cfg.ci.selected_lock_tests + 1)
    assert CI._needs_suite_slot(cfg, HOOKS, scope, [1.0] * 10) is True
    assert scope.admission["basis"] == "file_count"


def test_the_file_knob_at_zero_still_means_never(monkeypatch):
    _host(monkeypatch)
    cfg = Config()
    cfg.ci.selected_lock_tests = 0
    scope = _scope(5000)
    assert CI._needs_suite_slot(cfg, HOOKS, scope, [9999.0] * 10) is False
    assert scope.admission["basis"] == "off"


def test_the_full_suite_always_takes_the_slot_whatever_is_measured():
    cfg = Config()
    assert CI._needs_suite_slot(cfg, CI.DEFAULT_COMMAND, None, [0.001] * 50) is True
    full = NS(scope="full", tests=[], always=[])
    assert CI._needs_suite_slot(cfg, CI.DEFAULT_COMMAND, full, [0.001] * 50) is True


def test_a_rate_is_the_runs_own_seconds_in_worker_seconds_a_file():
    # 300 s on the clock, 100 of it queueing, 50 of it the guards: 150 s on 6 workers, 3 files
    run = _run(elapsed=300.0, waited=100.0, always_s=50.0, n=6, files=3)
    assert _rates([run], Config()) == [300.0]


def test_what_did_not_measure_a_run_gives_no_rate():
    never_started = _run()
    never_started.data["evidence"]["ci_scope"]["ran"]["selected"] = "unavailable"
    hooks_failed = _run()
    del hooks_failed.data["evidence"]["ci_scope"]["ran"]["selected"]
    full = _run()
    full.data["evidence"]["ci_scope"]["scope"] = "full"
    no_count = _run()
    no_count.data["evidence"]["ci_scope"]["workers"] = {}
    queue_unknown = _run()  # held the slot, and the seconds it queued were not recorded
    queue_unknown.data["evidence"]["ci_scope"]["workers"]["selected"]["suite_floor"] = 8
    no_clock = _run()
    del no_clock.data["evidence"]["elapsed_s"]
    garbled = _run(elapsed="soon")
    no_guard_seconds = _run()
    del no_guard_seconds.data["evidence"]["ci_scope"]["always_s"]
    collection_error = _run(  # red with a `failed_tests` row, and no test ever ran
        kind="gate.failed", exit=1, failed_tests=["tests/test_a.py"], summary=["1 error in 3.10s"]
    )
    collection_error.data["evidence"]["ci_scope"]["ran"]["selected"] = False
    events = [
        _run(kind="gate.unavailable"),  # timed out: its clock stopped at the limit
        _run(exit=2),
        _gate(gate="unit_tests", exit=0, elapsed_s=300.0),
        _run(elapsed=10.0, waited=20.0),  # nothing left once the queue is taken out
        NS(kind="gate.passed", data={"gate": "ci"}),
        never_started, hooks_failed, full, no_count, queue_unknown, no_clock, garbled,
        no_guard_seconds, collection_error,
    ]  # fmt: skip
    assert _rates(events, Config()) == []


def test_a_failed_test_run_still_measured_the_run():
    """Main is often red: pytest ran the selection to its end, so the seconds are real."""
    run = _run(kind="gate.partial", exit=1, summary=["3 failed, 97 passed in 280.00s"])
    run.data["evidence"]["ci_scope"]["ran"]["selected"] = False
    assert _rates([run], Config()) == [300.0]


def test_the_window_keeps_the_newest_runs_only():
    cfg = Config()
    cfg.ci.selected_lock_runs = 2
    old, new = _run(elapsed=900.0), _run(elapsed=100.0)
    assert _rates([old, new, new], cfg) == [100.0, 100.0]


def test_the_decision_is_in_the_gate_evidence():
    plan = Plan("selected", "x", tests=["t"])
    plan.admission = {"slot": False, "basis": "file_count"}
    assert runner._scope_evidence(plan, "abc")["admission"] == plan.admission


def test_a_measured_decision_reaches_the_run(monkeypatch):
    """Through `run` itself: the rates the caller passed decide the hold."""
    calls: list[tuple] = []
    monkeypatch.setattr(runner, "_needs_suite_slot", lambda *a: calls.append(a) or False)
    monkeypatch.setattr(
        runner,
        "_run_locked",
        lambda *a, **k: (NS(kind="", ran=True, code=0, output="", elapsed_s=0.0), "", None, 0.0),
    )
    monkeypatch.setattr(runner, "_merge_base", lambda *a: ("main", None))
    monkeypatch.setattr(runner, "_stale_pins", lambda *a, **k: "")
    monkeypatch.setattr(runner, "tool_missing", lambda c: "")
    monkeypatch.setattr(runner, "resolve_command", lambda *a: ("hooks", ""))
    monkeypatch.setattr(runner.W, "rev", lambda *a: "abc")

    class Tree:
        def __enter__(self):
            return "/tree", None

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(runner, "merge_tree", lambda *a: Tree())
    runner.run("/repo", Config(), command="", rates=[5.0], base="main")
    assert calls and calls[0][-1] == [5.0]


def _hold(monkeypatch, *reads: float):
    """Fake the suite lock so ``_run_under_slot``/``_run_locked`` queue ``reads[-1]`` seconds
    without touching the host-wide lock, and capture the budget ``_execute`` was handed plus
    the `wait_s` the run passed the lock (the run timeout must never become the queue wait)."""

    class Hold:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    got: list = []
    waits: list = []
    seq = list(reads) or [0.0]
    at = [0]

    def monotonic():  # return each canned sample in turn, then clamp at the last: no StopIteration
        at[0] += 1
        return seq[min(at[0] - 1, len(seq) - 1)]

    def fake_hold(*a, **k):
        waits.append(k.get("wait_s"))
        return Hold()

    def fake_execute(*a, **k):
        got.append(a[4])
        return NS(kind="ran", ran=True, code=0, output="", elapsed_s=0), "", 0.0

    monkeypatch.setattr(runner.time, "monotonic", monotonic)
    monkeypatch.setattr(runner.SL, "hold", fake_hold)
    monkeypatch.setattr(runner, "_execute", fake_execute)
    return got, waits


def test_a_queued_suite_run_keeps_its_full_run_budget(monkeypatch):
    """B4f35548c0a: the queue wait consumed the run timeout, so under fleet load a run that
    queued near its `--timeout` limit was left ~1 s to run the actual checks and timed out
    UNAVAILABLE. `[ci].suite_wait_s` budgets the queue alone, `--timeout` budgets the RUN alone:
    a run that queued 20 s still gets its whole 7200 s budget behind the slot, and `run()` hands
    the lock no `wait_s` (the timeout is not spent on queueing)."""
    got, waits = _hold(monkeypatch, 0.0, 20.0)

    class Tree:
        def __enter__(self):
            return "/tree", None

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(runner, "merge_tree", lambda *a, **k: Tree())
    monkeypatch.setattr(runner, "_stale_pins", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_plan", lambda *a, **k: ("c", None))
    monkeypatch.setattr(runner, "_needs_suite_slot", lambda *a, **k: True)
    monkeypatch.setattr(runner, "tool_missing", lambda c: "")
    monkeypatch.setattr(runner, "resolve_command", lambda *a: ("hooks", ""))
    monkeypatch.setattr(runner.W, "rev", lambda *a: "abc")
    monkeypatch.setattr(runner, "_merge_base", lambda *a: ("main", None))

    runner.run("/repo", Config(), base="main", timeout_s=7200)
    assert got == [7200], f"the 20 s queue wait came off the run budget: {got}"
    assert waits == [None], f"the run timeout became the queue wait cap: {waits}"


def test_a_wait_that_used_the_whole_timeout_leaves_the_run_budget_intact(monkeypatch):
    """The wait consumes the whole `--timeout` (and more, the queue budget is `suite_wait_s`);
    the run budget stays the full timeout, never a 1 s floor."""
    got, waits = _hold(monkeypatch, 0.0, 7300.0)
    runner._run_locked(Config(), "h", runner.Path("."), "c", None, 7200, None, {}, True)
    assert got == [7200], f"the queue wait reduced the run budget: {got}"
    assert waits == [None], f"the run timeout became the queue wait cap: {waits}"


def test_the_item_run_reads_the_log_and_a_named_or_full_run_does_not(monkeypatch, tmp_path):
    got: list = []
    log = NS(read_all=lambda: [_run()] * 3)
    monkeypatch.setattr(AR, "_load", lambda *a: (log, Config(), NS(items={})))
    monkeypatch.setattr(AR, "lease_tick", lambda *a: None)
    monkeypatch.setattr(
        AR.CI, "run", lambda *a, **k: got.append(k["rates"]) or AR.CI.Result("unavailable")
    )
    AR.run(tmp_path, ref="abc")
    AR.run(tmp_path, ref="abc", full=True)
    AR.run(tmp_path, ref="abc", command="true")
    assert got == [[300.0] * 3, [], []]
