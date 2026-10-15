"""Log-derived metrics: figures on a frozen log, never a zero for a missing number."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli
from helpers import rpc_call, tool_json
from lanes_fixture import T0, Log, multi_lane
from metrics_fixture import NOW, H, finished, frozen, twelve

from ddflow.services import metrics as M
from ddflow.services.metrics import facts as F
from ddflow.services.metrics import forecast as FC
from ddflow.services.metrics import summary as S
from ddflow.services.metrics.stats import percentile
from ddflow.surfaces.mcp import Server


def run(log: Log, **kw):
    st, cfg, now = frozen(log)
    for k, v in kw.pop("knobs", {}).items():
        setattr(cfg.metrics, k, v)
    return M.run(log.events, st, cfg, kw.pop("now", now), **kw)


# -- the summary ---------------------------------------------------------------------


def test_cycle_time_is_first_claim_to_first_completion():
    ans = run(twelve())
    cyc = ans.metrics["cycle_time"]
    assert cyc["n"] == 12
    assert cyc["p50_s"] == pytest.approx(6.5 * H) and cyc["max_s"] == 12 * H


def test_review_rounds_count_each_verdict_of_a_review_gate():
    rounds = run(twelve()).metrics["review_rounds"]
    assert rounds["n"] == 12 and rounds["p50_rounds"] == 2 and rounds["max_rounds"] == 2


def test_gate_time_runs_from_started_to_verdict_else_the_reviews_own_time():
    rows = {r["gate"]: r for r in run(twelve()).metrics["gate_time"]}
    assert rows["research"]["p50_s"] == 300  # started +60, verdict +360
    assert rows["rubber_duck"]["n"] == 24 and rows["rubber_duck"]["p50_s"] == 250  # elapsed_s


def test_outcome_rates_by_gate_and_by_reviewer():
    by_gate = {r["group"]: r for r in run(twelve()).metrics["outcomes"]}
    assert by_gate["rubber_duck"]["failed"] == 12 and by_gate["rubber_duck"]["failure_rate"] == 0.5
    assert by_gate["docs"]["skip_rate"] == 1.0
    by_rev = run(twelve(), group_by="reviewer").metrics["outcomes"]
    assert [r["group"] for r in by_rev] == ["m-alpha"]  # a gate with no reviewer is left out


def test_unknown_group_by_is_a_failure_naming_the_choices():
    ans = run(twelve(), group_by="colour")
    assert ans.failed and "gate" in ans.reason and "reviewer" in ans.reason


def test_rework_is_a_bug_filed_after_the_completion():
    log = twelve()
    log.add("bug.found", "T03", "a2", T0 + 200 * H, item="T03", summary="late")
    log.add("bug.found", "T04", "a2", T0 + 1.0, item="T04", summary="before it was done")
    rw = run(log, now=T0 + 300 * H).metrics["rework"]
    assert (rw["completed"], rw["reworked"]) == (12, 1)


def test_queue_wait_is_ready_to_claimed_and_glob_wait_is_the_conflicting_lease():
    log = Log()
    # a holder keeps f.py for 10 h; B (same file) is ready at T0-1000 but is claimed at +10 h
    log.task("A", "f.py")
    log.task("B", "f.py")
    log.claim("a1", "A", T0, "f.py")
    log.complete("a1", "A", T0 + 10 * H)
    log.release("a1", "A", T0 + 10 * H + 1)
    log.claim("a2", "B", T0 + 10 * H + 5, "f.py")
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    facts = F.collect(log.events, st, now)
    ctx = M.Context(facts, cfg, now)
    assert sorted(S.queue_waits(ctx)) == pytest.approx([1000, 10 * H + 5 + 1000])
    # A and B are both claimable from the start; B waited on A's lease for 10 h of it
    (glob_wait,) = [w for w in S.lease_waits(ctx) if w > 0]
    assert glob_wait == pytest.approx(10 * H)


# -- never a zero for a number that cannot be computed -------------------------------


def test_sparse_log_is_not_enough_data_not_zeros():
    log = Log()
    finished(log, "T01", T0, 1.0)
    ans = run(log)
    assert not ans.ok and not ans.failed and "not enough data" in ans.reason
    m = ans.metrics
    assert m["cycle_time"]["p50_s"] is None and m["cycle_time"]["n"] == 1
    assert m["rework"]["rate"] is None
    assert "n/a" in ans.text and "cycle_time" in m["unavailable"]


def test_min_samples_knob_decides_what_is_enough():
    log = Log()
    for n in range(3):
        finished(log, f"T{n}", T0 + n * 3 * H, 1.0)
    assert not run(log).ok
    ans = run(log, knobs={"min_samples": 3})
    assert ans.ok and ans.metrics["cycle_time"]["p50_s"] == H


def test_since_limits_the_window_by_duration_and_date():
    log = twelve()
    all_n = run(log).metrics["cycle_time"]["n"]
    last_two_days = run(log, since="2d", knobs={"min_samples": 1}, now=NOW).metrics["cycle_time"][
        "n"
    ]
    assert 0 < last_two_days < all_n
    assert run(log, since="not-a-date").failed
    assert run(log, since="2026-01-01", knobs={"min_samples": 1}).ok


# -- forecast --------------------------------------------------------------------------


def chain_log(depth: int, history: int = 12) -> Log:
    log = twelve() if history else Log()
    prev = ""
    for n in range(depth):
        log.task(f"C{n}", f"c{n}.py", needs=prev)
        prev = f"C{n}"
    return log


def test_forecast_bounds_the_critical_path_and_is_seeded():
    log = chain_log(3)
    a = run(log, view="forecast").metrics
    b = run(log, view="forecast").metrics
    assert a["available"] and a["chain"] == ["C0", "C1", "C2"] and a["estimate"] is True
    assert (a["low_s"], a["high_s"]) == (b["low_s"], b["high_s"]) and a["low_s"] < a["high_s"]
    pool = [[1.0, 2.0, 3.0, 4.0]] * 3
    assert FC.draw_totals(pool, [0.0] * 3, 50, 0) != FC.draw_totals(pool, [0.0] * 3, 50, 7)


def test_forecast_percentile_knobs_move_the_range():
    log = chain_log(3)
    wide = run(log, view="forecast", knobs={"forecast_low_pct": 5, "forecast_high_pct": 99}).metrics
    base = run(log, view="forecast").metrics
    assert wide["low_s"] < base["low_s"] and wide["high_s"] > base["high_s"]


def test_forecast_under_min_samples_gives_no_range():
    ans = run(chain_log(3, history=0), view="forecast")
    assert not ans.ok and not ans.failed
    assert ans.metrics["low_s"] is None and "not enough data" in ans.reason


def test_forecast_subtracts_what_a_link_in_flight_has_already_run():
    log = chain_log(1)
    cold = run(log, view="forecast").metrics
    log.claim("a9", "C0", NOW - 5 * H, "c0.py")
    warm = run(log, view="forecast").metrics
    assert warm["high_s"] < cold["high_s"]


def test_forecast_names_a_cycle_instead_of_a_number():
    log = twelve()
    log.task("X1", "x1.py", needs="X2")
    log.task("X2", "x2.py", needs="X1")
    ans = run(log, view="forecast")
    assert not ans.ok and "cycle" in ans.reason and ans.metrics["low_s"] is None


def test_forecast_calibration_over_seeded_trials():
    """The truth of a 3-link chain drawn from the same history lands under P90 about 90% of
    the time and under P50 about half of it (1000 seeded trials)."""
    rng = random.Random(42)
    pool = [h * H for h in range(1, 13)]
    totals = FC.draw_totals([pool] * 3, [0.0] * 3, 1000, 1)
    p50, p90 = percentile(totals, 50), percentile(totals, 90)
    truth = [sum(rng.choice(pool) for _ in range(3)) for _ in range(1000)]
    assert 0.40 <= sum(t <= p50 for t in truth) / 1000 <= 0.60
    assert 0.85 <= sum(t <= p90 for t in truth) / 1000 <= 0.95


def test_forecast_runs_knob_sets_the_number_of_draws():
    assert len(FC.draw_totals([[1.0, 2.0]], [0.0], 37, 0)) == 37


# -- retro -----------------------------------------------------------------------------


def test_retro_names_the_injected_slow_gap():
    log = Log()
    log.task("S", "s.py")
    log.claim("a1", "S", T0, "s.py")
    log.add("gate.passed", "S", "a1", T0 + 600, gate="research", by="a1", evidence={})
    log.add("gate.failed", "S", "a1", T0 + 900, gate="critic", by="r", evidence={"model": "m"})
    log.add(
        "gate.passed", "S", "a1", T0 + 900 + 9 * H, gate="critic", by="r", evidence={"model": "m"}
    )
    log.complete("a1", "S", T0 + 9 * H + 960)
    t = run(log, view="retro", item="S").metrics
    big = t["biggest_gap"]
    assert big["from"] == "critic round 1 failed" and big["to"] == "critic round 2 passed"
    assert big["seconds"] == 9 * H and t["entries"][0]["what"] == "task.added"


def test_retro_on_an_unknown_item_fails_and_on_a_bare_one_is_nothing():
    log = twelve()
    log.task("BARE", "b.py")
    assert run(log, view="retro", item="NOPE").failed
    bare = run(log, view="retro", item="BARE")
    assert not bare.ok and not bare.failed and bare.metrics["biggest_gap"] is None


# -- the skip audit --------------------------------------------------------------------


def test_skip_audit_counts_reasons_and_skips_nothing_made_good():
    log = twelve()
    log.add("gate.passed", "T01", "a1", NOW - 1, gate="docs", by="a1", evidence={})  # T01 made good
    log.add("gate.skipped", "T02", "a7", NOW - 2, gate="research", by="a7", reason="trivial")
    a = run(log, view="skips").metrics
    docs = next(g for g in a["gates"] if g["gate"] == "docs")
    assert docs["skipped"] == 12 and docs["passed"] == 1 and docs["never_passed_after"] == 11
    assert docs["reasons"] == [{"reason": "no user change", "n": 12}]
    assert a["by_agent"][0] == {"agent": "a1", "skipped": 12}
    assert a["total_skipped"] == 13


def test_top_knob_limits_the_rows_listed():
    log = twelve()
    for i, who in enumerate(("b1", "b2", "b3")):
        log.add("gate.skipped", "T01", who, NOW - i, gate="docs", by=who, reason=f"r{i}")
    assert len(run(log, view="skips", knobs={"top": 2}).metrics["by_agent"]) == 2
    assert len(run(log, view="skips", knobs={"top": 9}).metrics["by_agent"]) == 4


# -- the provider hook and group-by dimensions -----------------------------------------


@pytest.fixture
def clean_registry():
    yield
    M.unregister("x")
    M.unregister_dimension("goal")


def test_a_provider_adds_rows_and_the_minimum_sample_rule_covers_them(clean_registry):
    M.register(
        "x",
        lambda ctx: [
            {"metric": "m1", "group": "g", "n": 50, "value": 0.25},
            {"metric": "m2", "group": "g", "n": 2, "value": 0.99},
        ],
    )
    rows = run(twelve()).metrics["providers"]
    assert [(r["metric"], r["value"]) for r in rows] == [("m1", 0.25), ("m2", None)]
    assert "need 10" in rows[1]["unavailable"]
    with pytest.raises(ValueError):
        M.register("x", lambda ctx: [])


def test_a_failing_provider_is_a_finding_not_a_crash(clean_registry):
    def boom(ctx):
        raise RuntimeError("no data source")

    M.register("x", boom)
    ans = run(twelve())
    assert ans.ok and "RuntimeError" in ans.metrics["unavailable"]["provider:x"]


def test_a_registered_dimension_is_a_valid_group_by(clean_registry):
    M.register_dimension("goal", lambda o, ctx: "G1" if o.item < "T05" else "G2")
    rows = run(twelve(), group_by="goal").metrics["outcomes"]
    assert [r["group"] for r in rows] == ["G1", "G2"]
    assert "goal" in M.dimension_names()


# -- the hourly grid and lane utilisation ----------------------------------------------


def test_grid_samples_the_plan_at_past_instants_and_says_when_it_was_capped():
    log = multi_lane()
    _, cfg, _ = frozen(log)
    now = T0 + 5 * H
    g = M.sample_grid(log.events, cfg, now)
    assert g["folds"] == len(g["rows"]) and g["rows"][-1]["at"] == now
    cfg.metrics.grid_max_folds = 2
    cut = M.sample_grid(log.events, cfg, now)
    assert len(cut["rows"]) == 2 and cut["capped"] is True
    cfg.metrics.grid_max_folds = 24
    cfg.metrics.grid_step_s = 5 * H
    assert len(M.sample_grid(log.events, cfg, now)["rows"]) < len(g["rows"])


def test_lane_utilisation_is_working_over_working_plus_idle_beside_ready():
    ans = run(multi_lane(), view="lanes", now=T0 + 2 * H)
    assert ans.ok and 0 < ans.metrics["utilisation"] <= 1
    assert ans.metrics["working"] > 0


def test_no_lane_activity_has_no_utilisation_not_zero():
    log = Log()
    log.task("Q", "q.py")
    ans = run(log, view="lanes", now=T0)
    assert not ans.ok and ans.metrics["utilisation"] is None


# -- the surfaces ----------------------------------------------------------------------


@pytest.fixture
def worked(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "X", "--globs", "f.py")
    assert run_cli(repo, "claim", "X", "--no-worktree", agent="holder")[0] == 0
    return repo


def test_cli_and_mcp_are_one_declaration(worked):
    code, out, _ = run_cli(worked, "metrics", "--json", agent="reader")
    assert code == 2  # one item, nothing finished: not enough data, said as exit 2
    cli = json.loads(out)
    mcp = tool_json(rpc_call(Server(worked), "ddflow_metrics"))
    assert set(cli) == set(mcp) and cli["metrics"]["cycle_time"]["p50_s"] is None


def test_cli_retro_and_skips_run(worked):
    code, out, _ = run_cli(worked, "metrics", "retro", "X", agent="reader")
    assert code == 0 and "lease.acquired" in out
    assert run_cli(worked, "metrics", "retro", "NOPE", agent="reader")[0] == 1
    assert run_cli(worked, "metrics", "skips", agent="reader")[0] == 0
