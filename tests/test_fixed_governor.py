"""Bd07c080a0c: `schedule.parallel = fixed` still lets the signals govern the ceiling.

Before the fix, ``fixed`` folded a range of one -- ``(max_parallel_tasks,
max_parallel_tasks, max_parallel_tasks)`` -- and ``flowstate.current_limit``
short-circuited to that number, so the load/memory/disk/reviewer-latency/gate-failure
signals that ``[schedule.signals]`` defines were advisory only and the lane fill
oversubscribed the host under a load spike.

After it, the fixed knob is the CEILING the signals may lower -- down to
``max_parallel_min`` -- and restore, never exceed. A fixed cap is still a fixed cap: a
calm host returns exactly ``max_parallel_tasks``.
"""

from __future__ import annotations

from ddflow.config import Config
from ddflow.core import flowcontrol as FC
from ddflow.core import flowparams as FP
from ddflow.core.model import State
from ddflow.infra import signals as SIG
from ddflow.services import flowstate as F

T0 = 1_800_000_000.0

#: Higher is always worse for the controller; load 2.0 is over the high mark (0.75).
SPIKED = {"load": 2.0, "memory_free_frac": 0.9, "disk_free_frac": 0.9}
#: A calm, healthy host: load per core 0.05, 90% memory and disk free.
CALM = {"load": 0.05, "memory_free_frac": 0.9, "disk_free_frac": 0.9}


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _fixed_cfg(tasks: int = 16, min_: int = 3, max_: int = 20) -> Config:
    cfg = Config()
    cfg.schedule.parallel = "fixed"
    cfg.schedule.max_parallel_tasks = tasks
    cfg.schedule.max_parallel_min = min_
    cfg.schedule.max_parallel_max = max_
    return cfg


def test_fixed_params_are_a_range_from_min_to_tasks_not_a_single_value():
    # The fixed knob is the ceiling the signals may lower, never raise: floor = min,
    # ceiling = tasks. With the old "range of one" the controller could not move, so the
    # signals were advisory only.
    assert FP.params(_fixed_cfg()).bounds() == (3, 16, 16)


def test_fixed_mode_spiked_load_lowers_the_tended_limit(repo):
    cfg = _fixed_cfg()
    clock = Clock()
    ctx = F.FlowCtx(repo=repo, cfg=cfg, state=State())
    src = SIG.FakeSource(SPIKED)
    for _ in range(12):
        F.sample_if_due(ctx, src, clock)
        clock.t += 60
    d = F.current_limit(ctx, None, clock)
    assert d.mode == "fixed", d
    assert d.limit < cfg.schedule.max_parallel_tasks, d
    assert d.limited_by == "load_per_core", d
    assert d.limit >= cfg.schedule.max_parallel_min, d


def test_fixed_mode_calm_load_restores_the_cap_but_never_exceeds_it():
    # A calm, binding host walks the tended limit back up to the fixed cap (the ceiling),
    # never above it. Folded with the same fixed params the sampler uses.
    p = FP.params(_fixed_cfg())
    step = 60.0
    bad = [FC.Sample(T0 + i * step, {"load_per_core": 2.0}) for i in range(12)]
    lowered = FC.fold_limit(bad, p, [(0.0, 99)], bad[-1].at).limit
    assert lowered < 16
    calm = bad + [
        FC.Sample(bad[-1].at + (i + 1) * step, {"load_per_core": 0.05}) for i in range(200)
    ]
    d = FC.fold_limit(calm, p, [(0.0, 99)], calm[-1].at)
    assert d.limit == 16, d
    assert d.limited_by == "ceiling", d


def test_fixed_mode_below_ceiling_names_restoring_not_demand():
    # B69323fd23e: the limit lowered under load, then calm, with the in-flight count below
    # the lowered limit names "restoring" -- in-flight counts RUNNING work, not the work
    # that is WAITING -- never "demand (N in flight)".
    p = FP.params(_fixed_cfg())
    step = 60.0
    bad = [FC.Sample(T0 + i * step, {"load_per_core": 2.0}) for i in range(12)]
    lowered = FC.fold_limit(bad, p, [(0.0, 1)], bad[-1].at).limit
    assert lowered < 16
    calm = bad + [FC.Sample(bad[-1].at + (i + 1) * step, {"load_per_core": 0.05}) for i in range(3)]
    d = FC.fold_limit(calm, p, [(0.0, 1)], calm[-1].at)
    assert d.limit < 16, d
    assert d.limited_by == "restoring (1 in flight)", d
