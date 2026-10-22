"""The host-wide suite lock serves waiters first in, first out (B1fa77a6832): a bare race for
the flock let late arrivals starve one that had waited."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ddflow.services import suitelock as SL


def cfg(wait=10.0, stale=120, grace=120):
    return NS(
        ci=NS(
            max_concurrent_suites=1,
            suite_wait_s=wait,
            suite_queue_stale_s=stale,
            suite_queue_grace_s=grace,
        )
    )


def _tickets(d: Path) -> int:
    return len(list((d / "queue").glob("*.ticket")))


def _until(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)


def _waiter(d, name, order, wait=30.0, hold_s=0.0, **kw):
    def go():
        with SL.hold(cfg(wait, **kw), name, d, say=lambda _t: None):
            order.append(name)
            time.sleep(hold_s)

    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


def test_waiters_get_the_lock_in_arrival_order(tmp_path: Path):
    order: list[str] = []
    names = [f"agent-{i}" for i in range(5)]
    threads = []
    with SL.hold(cfg(), "holder", tmp_path, say=lambda _t: None):
        for i, n in enumerate(names):
            threads.append(_waiter(tmp_path, n, order, hold_s=0.05))
            _until(lambda i=i: _tickets(tmp_path) == i + 1)
    for t in threads:
        t.join(30)
    assert order == names


def test_the_refusal_names_the_holder_pid_and_the_place_in_the_queue(tmp_path: Path):
    with SL.hold(cfg(), "item-x of repo", tmp_path, say=lambda _t: None):
        with pytest.raises(SL.SuiteBusy) as e:
            with SL.hold(cfg(wait=0.3), "agent-b", tmp_path, say=lambda _t: None):
                pass
    text = str(e.value)
    assert "blocked on pid " in text and "item-x of repo" in text
    assert "you are #1 in the queue" in text


def test_the_place_in_the_queue_is_said_when_it_changes(tmp_path: Path):
    said: list[str] = []
    order: list[str] = []
    with SL.hold(cfg(), "holder", tmp_path, say=lambda _t: None):
        t1 = _waiter(tmp_path, "first", order)
        _until(lambda: _tickets(tmp_path) == 1)

        def second():
            with SL.hold(cfg(30), "second", tmp_path, say=said.append):
                order.append("second")

        t2 = threading.Thread(target=second, daemon=True)
        t2.start()
        _until(lambda: bool(said))
    t1.join(30)
    t2.join(30)
    assert any("#2 in the queue" in s for s in said)
    assert order == ["first", "second"]


def test_a_killed_waiter_drops_out_of_the_queue(tmp_path: Path):
    code = (
        "import sys; from types import SimpleNamespace as NS; from pathlib import Path\n"
        "from ddflow.services import suitelock as SL\n"
        "c = NS(ci=NS(max_concurrent_suites=1, suite_wait_s=600))\n"
        "with SL.hold(c, 'doomed', Path(sys.argv[1]), say=lambda _t: None): pass\n"
    )
    order: list[str] = []
    with SL.hold(cfg(), "holder", tmp_path, say=lambda _t: None):
        child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)])
        try:
            _until(lambda: _tickets(tmp_path) == 1)
            t = _waiter(tmp_path, "alive", order)
            _until(lambda: _tickets(tmp_path) == 2)
        finally:
            child.kill()
            child.wait()
    t.join(15)
    assert order == ["alive"]  # not held up behind the dead ticket


def test_a_waiter_that_stopped_beating_is_passed_over(tmp_path: Path):
    import os

    from ddflow.infra import fsio

    order: list[str] = []
    queue = tmp_path / "queue"
    queue.mkdir()
    frozen = queue / "00000000000000000001-frozen.ticket"
    with fsio.file_lock(frozen, 0):  # its owner is alive (holds the lock) but beats no more
        old = time.time() - 600
        os.utime(frozen, (old, old))
        with SL.hold(cfg(), "holder", tmp_path, say=lambda _t: None):
            t = _waiter(tmp_path, "alive", order, stale=60)
            _until(lambda: _tickets(tmp_path) == 2)
        t.join(15)
    assert order == ["alive"]


def test_a_retry_after_giving_up_keeps_its_place(tmp_path: Path):
    order: list[str] = []
    with SL.hold(cfg(), "holder", tmp_path, say=lambda _t: None):
        with pytest.raises(SL.SuiteBusy):
            with SL.hold(cfg(wait=0.3), "early", tmp_path, say=lambda _t: None):
                pass
        late = _waiter(tmp_path, "late", order)
        _until(lambda: _tickets(tmp_path) == 1)
        early = _waiter(tmp_path, "early", order)  # the retry
        _until(lambda: _tickets(tmp_path) == 2)
    early.join(30)
    late.join(30)
    assert order == ["early", "late"]
