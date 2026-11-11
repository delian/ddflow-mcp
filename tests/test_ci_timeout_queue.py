"""DX-ci-timeout-excludes-queue: the ci run's ``timeout_s`` budgets the checks ALONE, and the
seconds a run queued for the host-wide suite lock are reported apart, never as part of the
timeout or the run. A run that queued past its timeout still gets the WHOLE timeout once the
lock is taken (B4f35548c0a separated the queue wait from the run budget; this pins it)."""

from __future__ import annotations

import time
from types import SimpleNamespace as NS

import pytest

from ddflow.config import Config
from ddflow.services.ci import runner


def test_a_run_queued_past_timeout_keeps_the_whole_timeout_once_it_holds_the_lock(monkeypatch):
    """The queue wait never comes off the run budget: however long ``timeout_s`` is, it budgets
    the checks that run AFTER the slot is held, unchanged by the seconds spent queued."""
    captured: dict[str, object] = {}

    monkeypatch.setattr(runner, "resolve_command", lambda *a: ("hooks", ""))
    monkeypatch.setattr(runner, "tool_missing", lambda c: "")
    monkeypatch.setattr(runner.W, "rev", lambda *a: "abc")
    monkeypatch.setattr(runner, "_merge_base", lambda *a: ("main", None))
    monkeypatch.setattr(runner, "_needs_suite_slot", lambda *a: True)  # a full-suite run
    monkeypatch.setattr(runner, "_stale_pins", lambda *a, **k: "")

    class Hold:
        def __enter__(self):
            time.sleep(0.15)  # the lock stays taken past the run's own timeout budget
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(runner.SL, "hold", lambda *a, **k: Hold())

    def fake_execute(cfg, tree, cmd, scope, budget, tick, **kw):
        captured["budget"] = budget
        return (NS(kind="", ran=True, code=0, output=""), "", 0.0)

    monkeypatch.setattr(runner, "_execute", fake_execute)

    class Tree:
        def __enter__(self):
            return NS(), None

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(runner, "merge_tree", lambda *a: Tree())

    timeout_s = 3  # a run budget FAR below the simulated queue wait: the wait must not eat it
    res = runner.run("/repo", Config(), command="", base="main", timeout_s=timeout_s)

    assert captured["budget"] == timeout_s
    assert res.status == "passed"
    assert res.elapsed_s == 0.0  # the checks ran for 0 s; the queue wait is NOT part of it
    assert res.waited_s is not None and res.waited_s >= 0.15


def test_the_queue_wait_is_recorded_apart_and_never_folds_into_the_run(monkeypatch):
    """The wait the run spent queued is carried as ``waited_s`` separately from ``elapsed_s``
    (the run itself), so a busy host reports how long it queued instead of reading that time as
    run time (B1d51fb9da1) and never as part of the timeout."""
    captured: dict[str, object] = {}

    monkeypatch.setattr(runner, "resolve_command", lambda *a: ("hooks", ""))
    monkeypatch.setattr(runner, "tool_missing", lambda c: "")
    monkeypatch.setattr(runner.W, "rev", lambda *a: "abc")
    monkeypatch.setattr(runner, "_merge_base", lambda *a: ("main", None))
    monkeypatch.setattr(runner, "_needs_suite_slot", lambda *a: True)
    monkeypatch.setattr(runner, "_stale_pins", lambda *a, **k: "")

    class Hold:
        def __enter__(self):
            time.sleep(0.2)
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(runner.SL, "hold", lambda *a, **k: Hold())

    def fake_execute(cfg, tree, cmd, scope, budget, tick, **kw):
        captured["budget"] = budget
        return (NS(kind="", ran=True, code=0, output=""), "", 7.0)  # the checks ran 7 s

    monkeypatch.setattr(runner, "_execute", fake_execute)

    class Tree:
        def __enter__(self):
            return NS(), None

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(runner, "merge_tree", lambda *a: Tree())

    res = runner.run("/repo", Config(), command="", base="main", timeout_s=30)

    assert captured["budget"] == 30        # the wait did not reduce the run's timeout
    assert res.elapsed_s == pytest.approx(7.0)  # the run, not the wait
    assert res.waited_s is not None and res.waited_s >= 0.2
