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


def _has(d: Path, who: str) -> bool:
    return any(who in p.name for p in (d / "queue").glob("*.ticket"))


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
    assert all("you are #" in s for s in said)
    assert order == ["first", "second"]


def test_a_killed_waiter_drops_out_of_the_queue(tmp_path: Path):
    code = (
        "import sys; from types import SimpleNamespace as NS; from pathlib import Path\n"
        "from ddflow.services import suitelock as SL\n"
        "c = NS(ci=NS(max_concurrent_suites=1, suite_wait_s=600, suite_queue_stale_s=120, suite_queue_grace_s=120))\n"
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
        assert not _has(tmp_path, "early")  # parked, not a live ticket
        late = _waiter(tmp_path, "late", order)
        _until(lambda: _has(tmp_path, "late"))
        early = _waiter(tmp_path, "early", order)  # the retry
        _until(lambda: _has(tmp_path, "early"))
    early.join(30)
    late.join(30)
    assert order == ["early", "late"]


def test_a_ticket_not_yet_locked_by_its_owner_still_holds_its_place(tmp_path: Path):
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "00000000000000000001-early.ticket").write_text("")  # created, not locked yet
    live = SL._live(queue, 120, 120)
    assert live == ["00000000000000000001-early.ticket"]


def test_a_scan_holding_a_new_tickets_lock_for_an_instant_does_not_abort_its_owner(tmp_path: Path):
    from ddflow.infra import fsio

    queue = tmp_path / "queue"
    queue.mkdir()
    ticket = SL._Ticket(queue, "agent-a", 120)
    gate = threading.Event()

    def scanner():
        with fsio.file_lock(ticket.path, 0):  # what `_live` does to an unlocked ticket
            gate.set()
            time.sleep(0.4)

    threading.Thread(target=scanner, daemon=True).start()
    assert gate.wait(5)
    with ticket:  # waits out the scan instead of raising LockTimeout
        assert ticket.path.exists()


def test_two_retries_racing_for_one_parked_ticket_get_it_once(tmp_path: Path, monkeypatch):
    queue = tmp_path / "queue"
    queue.mkdir()
    parked = queue / "00000000000000000001-agent_a.parked"
    parked.write_text("")
    # Both retries LOOK at the parked ticket before either renames it: the interleaving.
    monkeypatch.setattr(SL, "_parked_for", lambda *_a: parked)
    first = SL._Ticket(queue, "agent-a", 120)
    second = SL._Ticket(queue, "agent-a", 120)  # its rename finds the file gone
    assert first.name == "00000000000000000001-agent_a.ticket"
    assert second.name != first.name  # the loser queues anew, it does not share or clobber


def test_a_ticket_removed_between_the_stat_and_the_lock_is_not_brought_back(tmp_path, monkeypatch):
    from ddflow.infra import fsio

    queue = tmp_path / "queue"
    queue.mkdir()
    ticket = queue / "00000000000000000001-gone.ticket"
    ticket.write_text("")
    real = fsio.file_lock

    def owner_leaves_first(path, *a, **kw):
        Path(path).unlink(missing_ok=True)  # the owner finishes right after the scan's stat
        return real(path, *a, **kw)

    monkeypatch.setattr(SL.fsio, "file_lock", owner_leaves_first)
    assert SL._live(queue, 120, 120) == []
    assert list(queue.iterdir()) == []  # no ghost file


def _race_once(queue: Path) -> list[str]:
    queue.mkdir()
    (queue / "00000000000000000001-agent_a.parked").write_text("")
    barrier = threading.Barrier(2)
    names: list[str] = []

    def retry() -> None:
        barrier.wait()
        names.append(SL._Ticket(queue, "agent-a", 120).name)

    threads = [threading.Thread(target=retry) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return names


def test_two_retries_racing_in_threads_take_a_parked_ticket_once(tmp_path):
    for round_ in range(40):
        names = _race_once(tmp_path / f"q{round_}")
        kept = [n for n in names if n == "00000000000000000001-agent_a.ticket"]
        assert len(names) == 2 and len(kept) == 1, names  # one wins the place, one queues anew


def test_a_ticket_unlinked_after_the_open_is_not_counted(tmp_path, monkeypatch):
    import contextlib

    queue = tmp_path / "queue"
    queue.mkdir()
    ticket = queue / "00000000000000000001-gone.ticket"
    ticket.write_text("")
    real = SL.fsio.file_lock

    @contextlib.contextmanager
    def unlink_after_open(path, *a, **kw):
        with real(path, *a, **kw):
            Path(path).unlink()  # the lock is now on an inode with no name
            yield

    monkeypatch.setattr(SL.fsio, "file_lock", unlink_after_open)
    assert SL._live(queue, 120, 120) == []


def test_an_expired_parked_ticket_removed_between_the_stat_and_the_lock_leaves_no_ghost(
    tmp_path, monkeypatch
):
    import os
    import time

    queue = tmp_path / "queue"
    queue.mkdir()
    parked = queue / "00000000000000000001-gone.parked"
    parked.write_text("")
    old = time.time() - 9999
    os.utime(parked, (old, old))
    real = SL.fsio.file_lock

    def owner_took_it(path, *a, **kw):
        Path(path).unlink(missing_ok=True)
        return real(path, *a, **kw)

    monkeypatch.setattr(SL.fsio, "file_lock", owner_took_it)
    assert SL._live(queue, 120, 120) == []
    assert list(queue.iterdir()) == []


def test_a_held_ticket_unlinked_after_the_open_is_not_counted(tmp_path, monkeypatch):
    from ddflow.infra import fsio

    queue = tmp_path / "queue"
    queue.mkdir()
    ticket = queue / "00000000000000000001-held.ticket"
    with fsio.file_lock(ticket, 0):  # the owner holds it
        real = SL.fsio.file_lock

        def owner_unlinks_while_holding(path, *a, **kw):
            try:
                return real(path, *a, **kw)  # opens the name, then finds it held
            finally:
                Path(path).unlink(missing_ok=True)

        monkeypatch.setattr(SL.fsio, "file_lock", owner_unlinks_while_holding)
        assert SL._live(queue, 120, 120) == []
