"""The pure flow controller: AIMD with hysteresis over injected samples and clock.

Hand-built sample lists, no host, no clock reads, no I/O: everything the controller
knows arrives as arguments.
"""

from __future__ import annotations

import random

from ddflow.core.flowcontrol import (
    Decision,
    Params,
    Sample,
    Threshold,
    decide_admission,
    fold_limit,
)

STEP = 60.0
LOAD = "load_per_core"
GOOD = {LOAD: 0.05}
BAD = {LOAD: 0.9}
MID = {LOAD: 0.4}


def params(**kw) -> Params:
    base = {"start": 4, "floor": 2, "ceiling": 8}
    base.update(kw)
    return Params(**base)


def series(values, *, t0=0.0, step=STEP):
    return [Sample(t0 + i * step, dict(v)) for i, v in enumerate(values)]


def fold(samples, p=None, binding=True, now=None):
    p = p or params()
    now = samples[-1].at if now is None else now
    hist = [(0.0, 99)] if binding else [(0.0, 0)]
    return fold_limit(samples, p, hist, now)


def test_defaults_match_the_accepted_decision():
    p = Params()
    assert (p.start, p.floor, p.ceiling) == (4, 2, 8)
    assert p.adapt_up_after_s == 600 and p.decrease_factor == 0.75
    assert p.cooldown_s == 300 and p.quiet_after_decrease_s == 1200
    assert (p.window, p.need_bad) == (4, 3)
    t = p.thresholds[LOAD]
    assert (t.low, t.high) == (0.15, 0.75)


def test_no_samples_is_start():
    d = fold_limit([], params(), [], 100.0)
    assert d.limit == 4 and not d.admit_paused
    assert d.limited_by == "start (no signals)"


def test_single_spike_changes_nothing():
    d = fold(series([GOOD, GOOD, BAD, GOOD, GOOD]))
    assert d.limit == 4


def test_three_of_four_bad_decreases_and_names_the_signal():
    d = fold(series([GOOD, BAD, BAD, GOOD, BAD]))
    assert d.limit == 3
    assert d.limited_by == LOAD
    assert d.mode == "decrease"
    assert LOAD in d.reason


def test_two_of_four_bad_changes_nothing():
    assert fold(series([BAD, GOOD, BAD, GOOD])).limit == 4


def test_cooldown_blocks_a_second_decrease():
    # 3 bad -> decrease at sample 2 (t=120); still bad within the 5 min cooldown
    bad_run = series([BAD] * 6)  # t = 0..300
    d = fold(bad_run[:3], params(floor=1))
    assert d.limit == 3
    d = fold(bad_run[:6], params(floor=1))  # t=300: 180 s after the decrease
    assert d.limit == 3
    long_run = series([BAD] * 12)  # decrease again once the cooldown has passed
    assert fold(long_run, params(floor=1)).limit < 3


def test_decrease_is_multiplicative_and_floored():
    p = params(start=8, floor=2)
    seen = []
    for n in range(3, 40):
        seen.append(fold(series([BAD] * n), p).limit)
    assert seen[0] == 6  # floor(8 * 0.75)
    assert min(seen) == 2 and all(x >= 2 for x in seen)
    assert fold(series([BAD] * 40), params(start=3, floor=1)).limit >= 1


def test_floor_of_one_and_floor_above_start():
    assert fold(series([BAD] * 60), params(start=4, floor=1)).limit == 1
    assert fold(series([BAD] * 60), params(start=4, floor=3)).limit == 3
    # a floor above the start lifts the start
    assert fold(series([GOOD] * 2), params(start=1, floor=2)).limit == 2


def test_healthy_ten_minutes_while_binding_grows_by_one():
    d = fold(series([GOOD] * 11), binding=True)  # 600 s of healthy samples
    assert d.limit == 5
    assert d.mode == "increase"
    assert fold(series([GOOD] * 10), binding=True).limit == 4  # only 540 s


def test_growth_is_one_step_per_ten_minutes():
    assert fold(series([GOOD] * 22)).limit == 6


def test_healthy_but_never_binding_does_not_grow():
    assert fold(series([GOOD] * 60), binding=False).limit == 4


def test_binding_follows_in_flight_history():
    s = series([GOOD] * 21)
    below = [(0.0, 1)]
    at_limit = [(0.0, 1), (0.0 + 600, 4)]  # reaches the limit at t=600 only
    assert fold_limit(s, params(), below, s[-1].at).limit == 4
    # binding for the second half only: 10 min of binding -> exactly one step
    d = fold_limit(s, params(), at_limit, s[-1].at)
    assert d.limit == 5


def test_middle_zone_is_neither_good_nor_bad():
    assert fold(series([MID] * 60)).limit == 4


def test_no_growth_during_quiet_period_after_a_decrease():
    run = series([BAD] * 3 + [GOOD] * 40)  # decrease at t=120; quiet until t=1320
    d = fold(run[:24])  # t=1380 is sample 23
    assert d.limit == 3
    # growth credit only starts after the quiet period (t=1320): +1 needs 600 s more
    assert fold(run[:32]).limit == 3
    assert fold(run[:33]).limit == 4  # t=1980: ten healthy samples after the quiet end


def test_unavailable_signal_is_neutral_for_decreases():
    p = params(
        thresholds={
            LOAD: Threshold(low=0.15, high=0.75),
            "queue": Threshold(low=1, high=3),
        }
    )
    run = [Sample(i * STEP, {LOAD: 0.05, "queue": None}) for i in range(6)]
    assert fold(run, p).limit == 4
    run = [Sample(i * STEP, {LOAD: 0.4, "queue": None}) for i in range(6)]
    assert fold(run, p).limit == 4


def test_unavailable_signal_cannot_justify_an_increase():
    p = params(
        host_signals=("mem",),
        thresholds={"mem": Threshold(low=0.5, high=0.8), LOAD: Threshold(0.15, 0.75)},
    )
    # only a non-host signal is available: nothing healthy can be established
    run = [Sample(i * STEP, {"mem": None, LOAD: None, "x": 0.0}) for i in range(30)]
    assert fold(run, p).limit == 4
    # an available but unhealthy-neutral signal blocks growth
    run = [Sample(i * STEP, {"mem": 0.6, LOAD: 0.05}) for i in range(30)]
    assert fold(run, p).limit == 4


def test_non_host_signal_over_high_counts_as_bad_and_is_named():
    p = params(thresholds={LOAD: Threshold(0.15, 0.75), "reviewers": Threshold(1, 3)})
    run = [Sample(i * STEP, {LOAD: 0.05, "reviewers": 5}) for i in range(4)]
    d = fold(run, p)
    assert d.limit == 3 and d.limited_by == "reviewers"


def test_non_host_signal_unhealthy_blocks_growth():
    p = params(thresholds={LOAD: Threshold(0.15, 0.75), "q": Threshold(1, 3)})
    run = [Sample(i * STEP, {LOAD: 0.05, "q": 2}) for i in range(30)]
    assert fold(run, p).limit == 4


def test_no_host_signal_available_stays_at_start():
    run = [Sample(i * STEP, {LOAD: None, "memory_pressure": None}) for i in range(30)]
    d = fold(run)
    assert d.limit == 4 and d.limited_by == "start (no signals)"
    run = [Sample(i * STEP, {}) for i in range(30)]
    assert fold(run).limit == 4


def test_critical_pauses_admission_without_lowering_the_limit():
    p = params(
        thresholds={LOAD: Threshold(0.15, 0.75, critical=1.5)},
    )
    run = series([GOOD] * 5 + [{LOAD: 2.0}])
    d = fold(run, p)
    assert d.admit_paused is True
    assert d.limit == 4
    # the very next healthy sample lifts the pause: it is per evaluation
    run2 = run + series([GOOD], t0=run[-1].at + STEP)
    d2 = fold(run2, p)
    assert d2.admit_paused is False and d2.limit == 4


def test_ceiling_is_never_exceeded_property():
    rng = random.Random(20261003)
    names = [LOAD, "q"]
    p = params(
        start=3,
        floor=1,
        ceiling=6,
        thresholds={LOAD: Threshold(0.15, 0.75, 1.5), "q": Threshold(1, 3)},
    )
    for _ in range(200):
        n = rng.randint(0, 80)
        t, run, hist = 0.0, [], []
        for _i in range(n):
            t += rng.choice([30, 60, 60, 60, 120, 400, 1500])
            sig = {k: rng.choice([None, 0.0, 0.1, 0.4, 0.9, 2.0, 0.05, 5.0]) for k in names}
            run.append(Sample(t, sig))
            hist.append((t, rng.randint(0, 12)))
        d = fold_limit(run, p, hist, t + rng.choice([0, 30, 5000]))
        assert 1 <= d.limit <= 6
        assert isinstance(d, Decision)


def test_all_good_forever_stops_at_the_ceiling():
    d = fold(series([GOOD] * 600), params(ceiling=6))
    assert d.limit == 6
    assert d.limited_by == "ceiling"


def test_determinism_same_samples_same_decision():
    run = series([GOOD, BAD, BAD, BAD, GOOD, GOOD, MID, GOOD] * 5)
    a = fold(run)
    b = fold(list(run))
    assert a == b


def test_input_order_does_not_matter():
    run = series([GOOD, BAD, BAD, BAD, GOOD, GOOD])
    assert fold(run) == fold(list(reversed(run)), now=run[-1].at)


def test_no_oscillation_under_a_30_minute_square_wave():
    # 4 bad minutes every 30 minutes: at most one decrease per period, never a rise
    p = params(start=8, floor=1)
    period, bad_len = 30, 4
    values = [BAD if (m % period) < bad_len else GOOD for m in range(period * 8)]
    run = series(values)
    limits = [fold(run[: i + 1], p).limit for i in range(len(run))]
    drops = [i for i in range(1, len(limits)) if limits[i] < limits[i - 1]]
    for per in range(8):
        in_period = [i for i in drops if per * period <= i < (per + 1) * period]
        assert len(in_period) <= 1


def test_no_oscillation_with_an_even_duty_cycle():
    # 15 minutes bad / 15 minutes healthy. The first bad half walks the limit down to
    # the floor; afterwards the controller may probe upward but, with the quiet period
    # and the 3-of-4 rule, lowers at most once per 30-minute period.
    p = params()
    values = [BAD if (m % 30) < 15 else GOOD for m in range(30 * 8)]
    run = series(values)
    limits = [fold(run[: i + 1], p).limit for i in range(len(run))]
    drops = [i for i in range(1, len(limits)) if limits[i] < limits[i - 1]]
    for per in range(1, 8):
        assert len([i for i in drops if per * 30 <= i < (per + 1) * 30]) <= 1
    assert all(2 <= x <= 4 for x in limits)


def test_a_long_gap_resets_to_start():
    run = series([BAD] * 4)
    assert fold(run).limit == 3
    late = [*run, Sample(run[-1].at + 1300, dict(GOOD))]
    assert fold(late).limit == 4
    # and a stale ring evaluated long after its last sample is start too
    d = fold(run, now=run[-1].at + 1300)
    assert d.limit == 4 and d.mode == "start"


def test_a_critical_sample_that_is_stale_does_not_pause():
    p = params(thresholds={LOAD: Threshold(0.15, 0.75, critical=1.5)})
    run = [Sample(0.0, {LOAD: 3.0})]
    assert fold_limit(run, p, [], 0.0).admit_paused is True
    assert fold_limit(run, p, [], 5000.0).admit_paused is False


def test_limited_by_names_the_closest_signal_when_nothing_decreased():
    p = params(
        thresholds={LOAD: Threshold(0.15, 0.75), "q": Threshold(1, 4)},
    )
    run = [Sample(i * STEP, {LOAD: 0.3, "q": 3}) for i in range(3)]
    d = fold(run, p)
    assert d.limit == 4 and d.limited_by == "q"


def test_params_are_bounded_sanely():
    d = fold(series([GOOD] * 3), Params(start=0, floor=0, ceiling=0))
    assert d.limit == 1
    d = fold(series([GOOD] * 3), Params(start=20, floor=2, ceiling=8))
    assert d.limit == 8


def test_admission_is_limit_in_flight_plus_independent_clamped():
    d = Decision(limit=4, mode="hold", limited_by="ceiling", reason="", admit_paused=False)
    assert decide_admission(d, 1, 10) == 4
    assert decide_admission(d, 1, 1) == 2
    assert decide_admission(d, 3, 0) == 3
    assert decide_admission(d, 0, 0) == 1
    assert decide_admission(d, 9, 9) == 4
    assert decide_admission(d, 9, 9, ceiling=3) == 3


def test_need_bad_zero_cannot_make_decreases_vacuous():
    # a healthy host must never lower the limit, whatever the evidence parameters say
    run = series([GOOD] * 30)
    assert fold(run, params(need_bad=0)).limit >= 4
    assert fold(run, params(need_bad=-2, window=0)).limit >= 4
    # and need_bad above the window is clamped so a decrease stays reachable
    assert fold(series([BAD] * 6), params(window=2, need_bad=9)).limit == 3


def test_limited_by_stops_naming_a_signal_that_recovered():
    p = params(thresholds={LOAD: Threshold(0.15, 0.75), "q": Threshold(1, 3)})
    run = [Sample(i * STEP, {LOAD: 0.05, "q": 5}) for i in range(3)]
    run += [Sample((3 + i) * STEP, {LOAD: 0.1, "q": 0}) for i in range(40)]
    d = fold(run, p)
    assert d.mode == "increase" and d.limited_by == LOAD


def test_a_blind_sample_breaks_the_healthy_streak():
    run = [Sample(i * STEP, dict(GOOD)) for i in range(10)]  # 540 s healthy
    run.append(Sample(600.0, {}))
    run += [Sample(660.0 + i * STEP, dict(GOOD)) for i in range(2)]
    assert fold(run).limit == 4


def test_one_sample_never_moves_the_limit_whatever_the_evidence_params():
    for kw in ({"window": 1, "need_bad": 3}, {"need_bad": 1}, {"window": 0, "need_bad": 0}):
        assert fold([Sample(0.0, dict(BAD))], params(**kw)).limit == 4
