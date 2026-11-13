"""Runtime lane-budget stop: the lane supervisor interrupts a lane once its usage reaches budget.

The falsifier from the item body: a fake lane emitting usage events totalling 1.2x the template
budget must trigger ONE interrupt and record ``lane.budget_stopped``. It fails on today's code
(there is no ``ddflow.services.lanes.budgetstop`` and nothing calls the adapter's ``interrupt``).

The fake here is a scripted ``Lane`` (like ``ddflow.adapters.fakes.lanes.FakeLane``) with an
``interrupt`` method that counts its calls, so the enforcement is exercised the way the real
``process-headless`` transport is -- no process, no agent, no key.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ddflow.adapters import lanes as L
from ddflow.services.lanes import budgetstop as B


@dataclass
class FakeHandle:
    id: str = "lane-1"
    capabilities: frozenset[str] = frozenset({"interrupt", "usage", "tool_events"})
    interrupts: int = 0
    stopped: bool = False
    events: tuple[L.LaneEvent, ...] = ()

    @property
    def spec(self):
        return L.LaunchSpec(name="lane-1", argv=("x",))


class MeteredLane:
    """A ``Lane`` that yields a scripted transcript of ``usage`` events and counts interrupts."""

    def __init__(self, costs, *, capabilities=("interrupt", "usage", "tool_events")):
        self.costs = list(costs)
        self.capabilities = frozenset(capabilities)
        self.handle = None

    def start(self, spec):
        script = tuple(
            L.LaneEvent(i, spec.name, "usage", {"cost": c}) for i, c in enumerate(self.costs, 1)
        )
        self.handle = FakeHandle(capabilities=self.capabilities, events=tuple(script))
        return self.handle

    def events(self, h):
        yield from h.events

    def interrupt(self, h):
        h.interrupts += 1
        return L.ok("SIGINT sent")

    def stop(self, h, grace_s=None):
        h.stopped = True
        return L.ok("stopped")

    def status(self, h):
        return L.LaneStatus(state="exited" if (h.stopped or not h.events) else "running")


def _lane(costs, **kw):
    return MeteredLane(costs, **kw)


def test_usage_that_crosses_the_budget_interrupts_once_and_records_budget_stopped():
    # 1.2x a 1.0 USD budget: three 0.4 USD steps cross it on the third step.
    lane = _lane([0.4, 0.4, 0.4])
    h = lane.start(L.LaunchSpec(name="lane-1", argv=("x",)))
    records: list[tuple[str, dict]] = []

    verdict = B.enforce(
        lane, h, budget_usd=1.0, warn_fraction=0.5, stop_grace_s=2.0, record=records.append
    )

    assert h.interrupts == 1
    assert verdict.outcome == B.STOPPED
    assert verdict.spent == pytest.approx(1.2)
    stopped = [r for r in records if r[0] == B.RECORD_STOPPED]
    assert len(stopped) == 1
    assert stopped[0][1]["spent"] == pytest.approx(1.2)
    assert stopped[0][1]["cap"] == pytest.approx(1.0)
    assert stopped[0][1]["source"]


def test_usage_that_stays_under_budget_does_not_interrupt():
    lane = _lane([0.3, 0.3])
    h = lane.start(L.LaunchSpec(name="lane-1", argv=("x",)))
    records: list[tuple[str, dict]] = []

    verdict = B.enforce(lane, h, budget_usd=1.0, record=records.append)

    assert h.interrupts == 0
    assert verdict.outcome == B.UNDER
    assert verdict.spent == pytest.approx(0.6)
    assert not [r for r in records if r[0] == B.RECORD_STOPPED]


def test_the_soft_warn_fires_once_at_the_fraction():
    lane = _lane([0.6, 0.6])
    h = lane.start(L.LaunchSpec(name="lane-1", argv=("x",)))
    records: list[tuple[str, dict]] = []

    B.enforce(lane, h, budget_usd=1.0, warn_fraction=0.5, record=records.append)

    warns = [r for r in records if r[0] == B.RECORD_WARNED]
    assert len(warns) == 1
    assert h.interrupts == 1  # the second 0.6 crosses the 1.0 budget too


def test_a_lane_whose_usage_cannot_be_read_is_cap_unenforced_not_stopped():
    # No `cost` on a usage event: the spend cannot be summed, so the cap cannot be enforced.
    class UnreadableLane(MeteredLane):
        def events(self, h):
            yield L.LaneEvent(1, "lane-1", "usage", {})

    lane = UnreadableLane([])
    h = lane.start(L.LaunchSpec(name="lane-1", argv=("x",)))
    records: list[tuple[str, dict]] = []

    verdict = B.enforce(lane, h, budget_usd=1.0, record=records.append)

    assert h.interrupts == 0
    assert verdict.outcome == B.CAP_UNENFORCED
    unenforced = [r for r in records if r[0] == B.RECORD_UNENFORCED]
    assert len(unenforced) == 1
    assert unenforced[0][1]["lane"]


def test_not_advertising_interrupt_falls_back_to_stop():
    lane = _lane([0.4, 0.4, 0.4], capabilities=("usage", "tool_events"))
    h = lane.start(L.LaunchSpec(name="lane-1", argv=("x",)))

    verdict = B.enforce(lane, h, budget_usd=1.0)

    assert verdict.outcome == B.STOPPED
    assert h.interrupts == 0  # no interrupt capability: stop is the graceful signal
    assert h.stopped is True
