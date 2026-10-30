"""core/govern.py: the governor's pure decision (D-quotas-governor) and its [govern] config."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddflow.config import KNOB_STRICTEST
from ddflow.config_sections.govern import GovernConfig
from ddflow.core import govern as g
from ddflow.core.flowcontrol import Sample

P = g.Params()
FRAC = st.floats(0, 1)
TIER = st.sampled_from(g.TIERS)
EFFORT = st.sampled_from(g.EFFORTS)


def d(quota=None, context=None, load=None, req=None, last=None, now=0.0, params=P):
    return g.decide(quota or {}, context, load or {}, req or g.Request(), params, last, now)


def test_below_every_mark_continues():
    r = d({"a": 0.1}, 0.1)
    assert r.action == g.CONTINUE and (r.tier, r.effort) == ("balanced", "high")


@pytest.mark.parametrize(
    "q,tier,effort,action",
    [(0.4, "balanced", "medium", g.DOWNSHIFT), (0.6, "fast", "low", g.DOWNSHIFT)],
)
def test_quota_ladder_caps(q, tier, effort, action):
    r = d(
        {"a": q},
        req=g.Request(tier="deep", mechanical=True, effort="high"),
        params=g.Params(deep_floor=False),
    )
    assert (r.tier, r.effort, r.action) == (tier, effort, action)


def test_nonmechanical_at_second_mark_keeps_tier_cuts_effort():
    r = d({"a": 0.65}, req=g.Request(tier="balanced", effort="high"))
    assert (r.tier, r.effort) == ("balanced", "low")


def test_eighty_percent_pauses_and_asks():
    assert d({"a": 0.8}).action == g.PAUSE_AND_ASK
    assert d({"a": 0.79}).action != g.PAUSE_AND_ASK


@pytest.mark.parametrize(
    "c,action",
    [
        (0.59, g.CONTINUE),
        (0.6, g.CHECKPOINT),
        (0.75, g.HANDOFF),
        (0.85, g.COMPACT),
        (0.95, g.EMERGENCY),
    ],
)
def test_context_ladder(c, action):
    assert d(context=c).action == action


def test_unavailable_reading_is_never_zero():
    hi = d({"a": 0.9}, 0.9)
    r = d({"a": None}, None, last=hi, now=10_000)
    assert r.quota.level == hi.quota.level and r.context.level == hi.context.level


def test_worst_subject_rules():
    assert d({"a": 0.1, "b": 0.85}).action == g.PAUSE_AND_ASK


def test_load_over_high_caps_tier_and_blind_load_holds():
    r = d(
        load={"load_per_core": 0.9}, req=g.Request(tier="deep"), params=g.Params(deep_floor=False)
    )
    assert r.tier == "balanced" and r.load.level == 1
    held = d(load={"load_per_core": None}, last=r, now=1e6)
    assert held.load.level == 1
    assert d(load={"memory_pressure": 0.96}, params=g.Params(load_governs=False)).load.level == 0


def test_reviewer_floor_and_deep_floor():
    r = d({"a": 0.7}, req=g.Request(role="reviewer", tier="fast", effort="low"))
    assert r.tier == "balanced"
    r = d({"a": 0.7}, req=g.Request(tier="deep", mechanical=True))
    assert r.tier == "deep"


def test_hysteresis_and_cooldown_hold_the_step():
    up = d({"a": 0.45}, now=0)
    assert up.quota.level == 1
    hover = d({"a": 0.38}, last=up, now=10_000)  # inside the band: stays
    assert hover.quota.level == 1
    early = d({"a": 0.1}, last=up, now=100)  # cooldown: stays
    assert early.quota.level == 1
    late = d({"a": 0.1}, last=up, now=1000)
    assert late.quota.level == 0


def test_replay_matches_stepwise_and_none_when_empty():
    assert g.replay([], g.Request(), P) is None
    samples = [
        Sample(0, {"quota:a": 0.45, "context": 0.1}),
        Sample(60, {"quota:a": 0.65, "context": 0.7}),
        Sample(120, {"quota:a": 0.1, "context": 0.7}),
    ]
    r = g.replay(samples, g.Request(), P)
    s = None
    for x in samples:
        s = d({"a": x.signals["quota:a"]}, x.signals["context"], last=s, now=x.at)
    assert r == s and r.quota.level == 2 and r.action == g.CHECKPOINT
    assert g.replay(samples, g.Request(), P, now=10).quota.level == 1


@settings(max_examples=200, deadline=None)
@given(FRAC, FRAC, FRAC, FRAC, TIER, EFFORT, st.booleans(), st.integers(0, 3), st.integers(0, 4))
def test_monotone_more_use_never_raises_the_cap(a, b, c1, c2, tier, effort, mech, ql, cl):
    lo, hi = sorted((a, b))
    last = g.Decision("continue", "balanced", "high", "", g.Ladder(ql, 0), g.Ladder(cl, 0))
    req = g.Request(tier=tier, effort=effort, mechanical=mech)
    r_lo = d({"x": lo}, min(c1, c2), req=req, last=last, now=1.0)
    r_hi = d({"x": hi}, max(c1, c2), req=req, last=last, now=1.0)
    assert g.TIERS.index(r_hi.tier) <= g.TIERS.index(r_lo.tier)
    assert g.EFFORTS.index(r_hi.effort) <= g.EFFORTS.index(r_lo.effort)
    assert r_hi.quota.level >= r_lo.quota.level and r_hi.context.level >= r_lo.context.level


@settings(max_examples=200, deadline=None)
@given(
    st.lists(FRAC, min_size=1, max_size=12),
    TIER,
    EFFORT,
    st.sampled_from(["worker", "reviewer"]),
    st.booleans(),
)
def test_floors_always_hold(readings, tier, effort, role, mech):
    req = g.Request(role=role, tier=tier, effort=effort, mechanical=mech)
    last = None
    for i, v in enumerate(readings):
        last = d({"x": v}, v, {"load_per_core": v}, req, last, now=i * 100.0)
        floor_t, floor_e = g._floors(req, P)
        assert g.TIERS.index(last.tier) >= floor_t and g.EFFORTS.index(last.effort) >= floor_e
        if tier == "deep":
            assert last.tier == "deep"


@settings(max_examples=200, deadline=None)
@given(st.integers(0, 2), st.lists(st.floats(-0.04, 0.04), min_size=1, max_size=20))
def test_hysteresis_does_not_flap(mark_index, wobble):
    mark = P.quota()[mark_index]
    last = d({"x": mark}, now=0.0)
    start = last.quota.level
    for i, w in enumerate(wobble):
        # within the band below the mark and anywhere above it: never steps down
        v = max(0.0, min(1.0, mark + abs(w) if w >= 0 else mark - min(-w, P.hysteresis - 1e-9)))
        last = d({"x": v}, last=last, now=1000.0 * (i + 1))
        assert last.quota.level >= start


def test_govern_config_defaults_match_params_and_fallbacks_are_strict():
    cfg = SimpleNamespace(
        govern=GovernConfig()
    )  # not Config.load(): ambient config must not matter
    assert g.params_from_config(cfg) == g.Params(load_thresholds=g.Params().load_thresholds)
    assert KNOB_STRICTEST["govern.tier_cap_1"][0] == "fast"
    assert KNOB_STRICTEST["govern.effort_cap_2"][0] == "low"


def test_one_unavailable_subject_blocks_a_step_down():
    hi = d({"a": 0.9, "b": 0.9}, now=0)
    assert hi.action == g.PAUSE_AND_ASK
    r = d({"a": None, "b": 0.0}, last=hi, now=10_000)
    assert r.quota.level == 3 and r.action == g.PAUSE_AND_ASK
    assert d({"a": 0.0, "b": 0.0}, last=hi, now=10_000).quota.level == 0


def test_one_unavailable_load_signal_blocks_a_step_down():
    hi = d(load={"memory_pressure": 0.96}, now=0)
    assert hi.load.level == 2
    r = d(load={"memory_pressure": None, "disk_pressure": 0.0}, last=hi, now=10_000)
    assert r.load.level == 2
    assert d(load={}, last=hi, now=10_000).load.level == 2  # nothing sampled holds


def test_deep_task_is_never_downshifted_in_effort_either():
    r = d({"a": 0.65}, req=g.Request(tier="deep", effort="high"))
    assert (r.tier, r.effort, r.action) == ("deep", "high", g.CONTINUE)
    r = d({"a": 0.65}, req=g.Request(tier="deep", effort="high"), params=g.Params(deep_floor=False))
    assert r.action == g.DOWNSHIFT


def test_floor_knobs_fall_back_to_the_highest_value():
    assert KNOB_STRICTEST["govern.reviewer_floor_tier"][0] == "deep"
    assert KNOB_STRICTEST["govern.effort_floor"][0] == "high"
