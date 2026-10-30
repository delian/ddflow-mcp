"""SW-capacity: slots per endpoint or GPU pool, as a pure view plus flock slot hold."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from ddflow.services import slots as S
from ddflow.services.lanes import capacity as C
from ddflow.services.lanes.endpoint import Endpoint


def ep(route: str = "local", concurrency: int | None = 2) -> Endpoint:
    return Endpoint(route, "http://HOST:8000/v1", "m", concurrency=concurrency)


def test_limit_precedence() -> None:
    assert C.pools_for(ep(), 1, {"ep-local": 9})[0].limit == 2  # the route's fact
    assert C.pools_for(ep(concurrency=None), 1, {"ep-local": 5})[0].limit == 5  # resources
    assert C.pools_for(ep(concurrency=None), 3, {})[0].limit == 3  # [lanes] endpoint_slots


def test_gpu_resource_pool_and_order() -> None:
    pools = C.pools_for(ep(), 1, {"gpu": 4}, ["gpu:2", "vllm-fleet"])
    assert [(p.name, p.limit, p.need) for p in pools] == [
        ("ep-local", 2, 1),
        ("gpu", 4, 2),
        ("vllm-fleet", 1, 1),
    ]


def test_route_name_is_a_safe_stem() -> None:
    assert "/" not in C.endpoint_pool("a/b") and not C.endpoint_pool(".x").startswith(".")


def test_view_figures_and_refusals() -> None:
    pools = C.pools_for(ep(), 1, {})
    h = C.view(pools, {"ep-local": 1})
    assert C.lane_figures(h) == (1, 2) and C.refusals(h) == []
    full = C.view(pools, {"ep-local": 2})
    assert "full" in C.refusals(full)[0] and C.headroom_term(full) == 0.0
    assert C.headroom_term(C.view(pools, {})) == 1.0
    assert 0.0 < C.headroom_term(h) < 1.0


def test_need_above_limit_refused() -> None:
    h = C.view(C.pools_for(ep(), 1, {"gpu": 2}, ["gpu:4"]), {})
    assert any("needs 4" in r for r in C.refusals(h))
    assert C.lane_figures(h)[1] == 1  # never a cap of zero


def test_last_slot_goes_to_one_lane(tmp_path: Path) -> None:
    pools = (C.Pool("ep-local", 1),)
    with C.acquire(pools, 0, tmp_path) as held:
        assert held.slots == {"ep-local": (0,)}
        assert C.busy_counts(pools, tmp_path) == {"ep-local": 1}
        with pytest.raises(S.SlotsTimeout):
            with C.acquire(pools, 0, tmp_path):
                pytest.fail("two lanes took the last slot")
    assert C.busy_counts(pools, tmp_path) == {"ep-local": 0}


def test_failed_acquire_releases_earlier_pools(tmp_path: Path) -> None:
    a, b = C.Pool("a-pool", 1), C.Pool("b-pool", 1)
    with S.Slots(tmp_path, "b-pool").hold(1):
        with pytest.raises(S.SlotsTimeout):
            with C.acquire((b, a), 0, tmp_path):
                pass
        assert C.busy_counts((a,), tmp_path) == {"a-pool": 0}


def test_gpu_need_takes_that_many_slots(tmp_path: Path) -> None:
    pools = (C.Pool("gpu", 4, 3),)
    with C.acquire(pools, 0, tmp_path) as held:
        assert len(set(held.slots["gpu"])) == 3
        with pytest.raises(S.SlotsTimeout):
            with C.acquire(pools, 0, tmp_path):
                pass


def test_dead_holder_frees_its_slot(tmp_path: Path) -> None:
    code = (
        "import sys,time\n"
        "from ddflow.services.lanes import capacity as C\n"
        "with C.acquire((C.Pool('ep-local',1),),0,sys.argv[1]):\n"
        "    print('held',flush=True); time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "held"
        pools = (C.Pool("ep-local", 1),)
        assert C.busy_counts(pools, tmp_path) == {"ep-local": 1}
        proc.kill()
        proc.wait()
        with C.acquire(pools, 2, tmp_path):
            pass
    finally:
        proc.kill()


def test_wait_holds_no_lease_and_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ddflow.services.lanes.capacity as mod

    src = Path(mod.__file__).read_text()
    for banned in ("lease", "events", "subprocess"):
        assert banned not in src.split('"""', 2)[2].replace("# lease", "")
    pools = (C.Pool("ep-local", 1),)
    t0 = time.monotonic()
    with S.Slots(tmp_path, "ep-local").hold(1):
        with pytest.raises(S.SlotsTimeout):
            with C.acquire(pools, 0.3, tmp_path):
                pass
    assert time.monotonic() - t0 < 5
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ep-local.0.lock"]  # no other state
    assert C.busy_counts(pools, tmp_path) == {"ep-local": 0}  # and nothing leaked


def test_waited_is_reported(tmp_path: Path) -> None:
    with C.acquire((C.Pool("x", 1),), 0, tmp_path) as held:
        assert held.waited_s >= 0.0


def test_no_hold_while_waiting_two_gpu_lanes(tmp_path: Path) -> None:
    """Two lanes needing 2 of 2 slots: one wins whole, the other holds NOTHING meanwhile."""
    pools = (C.Pool("gpu", 2, 2),)
    with S.Slots(tmp_path, "gpu").hold(2):  # one slot taken by someone else
        with pytest.raises(S.SlotsTimeout):
            with C.acquire(pools, 0, tmp_path):
                pass
        assert C.busy_counts(pools, tmp_path) == {"gpu": 1}  # the failed lane kept none
    with C.acquire(pools, 0, tmp_path) as held:
        assert len(held.slots["gpu"]) == 2


def test_wait_succeeds_when_slot_frees(tmp_path: Path) -> None:
    pools = (C.Pool("ep-local", 1),)
    outer = S.Slots(tmp_path, "ep-local").hold(1)
    outer.__enter__()
    naps = []

    def sleep(s: float) -> None:
        naps.append(s)
        outer.__exit__(None, None, None)  # the holder finishes during the first nap

    with C.acquire(pools, 30, tmp_path, sleep=sleep, poll_s=0.01) as held:
        assert held.slots == {"ep-local": (0,)} and len(naps) == 1
