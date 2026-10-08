"""services.slots.Slots: one counting semaphore across processes (B-uni-local-worker.1-slots)."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from ddflow.services.slots import Slots, SlotsTimeout


class FakeTime:
    """A clock the sleeping advances: a wait of any length costs nothing."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _slots(tmp_path, ft: FakeTime | None = None, name: str = "gates") -> Slots:
    if ft is None:
        return Slots(tmp_path / "slots", name)
    return Slots(tmp_path / "slots", name, clock=ft.clock, sleep=ft.sleep, poll_s=0.5)


def test_up_to_limit_holders_at_once_then_the_next_times_out(tmp_path):
    ft = FakeTime()
    s = _slots(tmp_path, ft)
    with s.hold(2) as a, s.hold(2) as b:
        assert {a.index, b.index} == {0, 1}
        assert s.busy(2) == 2
        with pytest.raises(SlotsTimeout) as exc:
            with s.hold(2, timeout_s=3):
                pass
        assert "all 2 gates slot(s) are held after 3s" in str(exc.value)
        assert ft.now >= 103, "it waited the whole timeout on the fake clock"
    assert s.busy(2) == 0


def test_a_released_slot_is_taken_by_a_waiter(tmp_path):
    import threading

    s = _slots(tmp_path)
    got: list = []
    holding, release = threading.Event(), threading.Event()

    def holder():
        with s.hold(1):
            holding.set()
            release.wait(20)

    def waiter():
        with s.hold(1, timeout_s=20) as slot:
            got.append(slot)

    t1 = threading.Thread(target=holder)
    t1.start()
    assert holding.wait(10)
    t2 = threading.Thread(target=waiter)
    t2.start()
    time.sleep(0.3)
    assert not got, "the waiter must wait while the slot is held"
    release.set()
    t1.join(10)
    t2.join(10)
    assert len(got) == 1 and got[0].index == 0 and got[0].waited_s > 0


def test_a_zero_timeout_is_one_sweep(tmp_path):
    ft = FakeTime()
    s = _slots(tmp_path, ft)
    with s.hold(1):
        with pytest.raises(SlotsTimeout):
            with s.try_hold(1):
                pass
    assert ft.sleeps == [], "a zero timeout never sleeps"


def test_a_limit_below_one_is_one(tmp_path):
    s = _slots(tmp_path)
    with s.hold(0) as slot, pytest.raises(SlotsTimeout), s.try_hold(-3):
        assert slot.index == 0


def test_lowering_the_limit_never_evicts_a_holder_and_nobody_new_takes_its_slot(tmp_path):
    s = _slots(tmp_path)
    with s.hold(3) as a, s.hold(3) as b, s.hold(3) as c:
        assert {a.index, b.index, c.index} == {0, 1, 2}
        # the limit drops to 1: the three keep what they have; a newcomer sees only slot 0
        assert s.busy(1) == 1
        with pytest.raises(SlotsTimeout), s.try_hold(1):
            pass
    assert s.busy(3) == 0


def test_raising_the_limit_offers_the_new_slots_at_once(tmp_path):
    s = _slots(tmp_path)
    with s.hold(1) as a:
        assert a.index == 0
        with s.hold(2, timeout_s=0) as b:
            assert b.index == 1


def test_names_are_independent_semaphores(tmp_path):
    gates, queue = _slots(tmp_path, name="gates"), _slots(tmp_path, name="queue")
    with gates.hold(1), queue.hold(1):
        assert gates.busy(1) == queue.busy(1) == 1


def test_a_name_that_is_not_a_file_stem_is_refused(tmp_path):
    for bad in ("", "a/b", ".hidden"):
        with pytest.raises(ValueError):
            Slots(tmp_path, bad)


def test_the_exception_in_the_block_releases_the_slot(tmp_path):
    s = _slots(tmp_path)
    with pytest.raises(RuntimeError), s.hold(1):
        raise RuntimeError("boom")
    assert s.busy(1) == 0


def test_another_process_excludes_us_and_a_killed_one_gives_the_slot_back(tmp_path):
    if os.name != "posix":
        pytest.skip("flock")
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; from ddflow.services.slots import Slots;"
            f"s = Slots({str(tmp_path / 'slots')!r}, 'gates'); h = s.hold(1); h.__enter__();"
            "print('ready', flush=True); time.sleep(120)",
        ],
        stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        s = _slots(tmp_path)
        assert s.busy(1) == 1
        with pytest.raises(SlotsTimeout), s.try_hold(1):
            pass
        child.kill()  # no release, no cleanup: the OS closes its descriptor
        child.wait(10)
        with s.hold(1, timeout_s=10) as slot:
            assert slot.index == 0
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(10)
