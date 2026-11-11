"""DX-ci-timeout-excludes-queue: the ci run's ``timeout_s`` budgets the checks ALONE, and the
seconds a run queued for the host-wide suite lock are reported apart, never off the run budget.
A run that queued LONGER than its timeout still gets the WHOLE timeout once the lock is taken
(B4f35548c0a separated the queue wait from the run budget; this pins it)."""

from __future__ import annotations

from types import SimpleNamespace as NS

from ddflow.config import Config
from ddflow.services.ci import runner


class _Clock:
    """A clock whose ``monotonic`` the test drives: ``_run_under_slot`` reads ``queued_at`` at 0,
    ``_Hold`` advances it by the simulated queue wait, and the runner reads the difference."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t


class _Hold:
    """A suite slot already claimed for ``wait_s`` more seconds before this waiter takes it."""

    def __init__(self, clock: _Clock, wait_s: float) -> None:
        self._clock, self._wait_s = clock, wait_s

    def __enter__(self) -> _Hold:
        self._clock.t += self._wait_s  # the seconds spent queued before the slot frees
        return self

    def __exit__(self, *a: object) -> None:
        return None


class _Tree:
    def __enter__(self) -> tuple[NS, None]:
        return NS(), None

    def __exit__(self, *a: object) -> None:
        return None


def _drive(monkeypatch, *, timeout_s: float, wait_s: float, run_s: float):
    """Drive ``runner.run`` for a full-suite run whose lock is held past ``timeout_s``; return
    (result, budget the checks received)."""
    captured: dict[str, float] = {}
    clock = _Clock()

    monkeypatch.setattr(runner, "resolve_command", lambda *a: ("hooks", ""))
    monkeypatch.setattr(runner, "tool_missing", lambda c: "")
    monkeypatch.setattr(runner, "_merge_base", lambda *a: ("main", None))
    monkeypatch.setattr(runner, "_needs_suite_slot", lambda *a: True)  # a full-suite run
    monkeypatch.setattr(runner, "_stale_pins", lambda *a, **k: "")

    monkeypatch.setattr(runner, "CLOCK", clock)
    monkeypatch.setattr(runner.W, "rev", lambda *a: "abc")
    monkeypatch.setattr(runner, "merge_tree", lambda *a: _Tree())
    monkeypatch.setattr(runner.SL, "hold", lambda cfg, holder, *a, **k: _Hold(clock, wait_s))

    def fake_execute(cfg, tree, cmd, scope, budget, tick, **kw):
        captured["budget"] = budget
        return (NS(kind="", ran=True, code=0, output=""), "", run_s)

    monkeypatch.setattr(runner, "_execute", fake_execute)

    res = runner.run("/repo", Config(), command="", base="main", timeout_s=timeout_s)
    return res, captured.get("budget")


def test_a_run_queued_past_timeout_keeps_the_whole_timeout_once_it_holds_the_lock(monkeypatch):
    """wait_s (52 s) exceeds timeout_s (7 s): a budget the queue wait came off would hand
    ``_execute`` less than 7 s here -- the run keeps all of it anyway."""
    res, budget = _drive(monkeypatch, timeout_s=7, wait_s=52, run_s=3.0)
    assert budget == 7
    assert res.status == "passed"


def test_the_queue_wait_is_recorded_apart_and_never_counts_against_the_timeout(monkeypatch):
    """The queue wait is carried as the dedicated ``waited_s`` (B1d51fb9da1), the run's own
    ``elapsed_s`` is the check time alone, and none of the wait came off the run budget."""
    res, budget = _drive(monkeypatch, timeout_s=30, wait_s=12.5, run_s=8.0)
    assert budget == 30  # the whole timeout survived the 12.5 s queue wait
    assert res.waited_s == 12.5  # recorded separately (never as run time)
    assert res.elapsed_s == 8.0  # the run, not wait + run
