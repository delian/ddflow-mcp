"""`ddflow metrics batch`: the size-bucket tables, on a seeded log with known sizes and runs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log
from metrics_fixture import frozen

from ddflow.services import metrics as M

R = "rubber_duck"


def seeded() -> Log:
    """Two finite items of very different size: A (~30 lines, one confirmed finding) and B
    (~1300 lines, two findings, one reference), plus a gate-lock wait on A's review."""
    log = Log()
    log.add("phase.added", "P1", "lead", T0 - 2000, title="p1", body="")
    log.add("task.added", "A", "lead", T0 - 1000, title="A", body="", parent="P1")
    log.add("task.added", "B", "lead", T0 - 1000, title="B", body="", parent="P1")

    def size(item: str) -> dict:
        return {
            "A": {"insertions": 20, "deletions": 10},
            "B": {"insertions": 800, "deletions": 500},
        }[item]

    for item in ("A", "B"):
        log.claim("a1", item, T0 + 600)
        log.add("gate.started", item, "a1", T0 + 900, gate=R)
        log.add(
            "gate.passed",
            item,
            "a1",
            T0 + 1100,
            gate=R,
            by="r1",
            evidence={
                "model": "m-alpha",
                "elapsed_s": 200,
                "waited_s": 30,
                "diff_stat": size(item),
            },
        )
        log.complete("a1", item, T0 + 1200)
        log.release("a1", item, T0 + 1201)
    log.add("review.triaged", "A", "a1", T0 + 1150, gate=R, severity="MEDIUM", verdict="confirmed")
    log.add("review.triaged", "B", "a1", T0 + 1500, gate=R, severity="LOW", verdict="confirmed")
    log.add("review.triaged", "B", "a1", T0 + 1501, gate=R, severity="LOW", verdict="refuted")
    return log


def run(log: Log, **kw):
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    return M.run(log.events, st, cfg, now, view="batch", **kw)


def bucket(ans, name: str) -> dict:
    (b,) = [b for b in ans.metrics["buckets"] if b["bucket"] == name]
    return b


def test_items_are_bucketed_by_their_recorded_changed_lines():
    ans = run(seeded())
    assert bucket(ans, "< 50")["items"] == 1
    assert bucket(ans, "1000+")["items"] == 1


def test_review_runs_are_counted_per_bucket():
    ans = run(seeded())
    small = bucket(ans, "< 50")
    assert small["runs"] == 1
    assert small["elapsed"]["p50_s"] == pytest.approx(200)
    assert small["lock_wait"]["p50_s"] == pytest.approx(30)


def test_findings_per_bucket_split_confirmed_and_refuted():
    ans = run(seeded())
    big = bucket(ans, "1000+")
    assert big["confirmed"] == 1
    assert big["refuted"] == 1
    assert big["findings"] == 2
    assert big["findings_per_run"] == pytest.approx(2.0)  # two findings over one run
    assert bucket(ans, "< 50")["runs_with_finding"] == pytest.approx(1.0)


def test_an_unknown_size_is_not_counted_as_small():
    """An item whose review evidence never named a changed-line figure is unknown size."""
    log = Log()
    log.add("task.added", "U", "lead", T0 - 1000, title="u", body="")
    log.claim("a1", "U", T0 + 600)
    log.add("gate.started", "U", "a1", T0 + 900, gate=R)
    log.add("gate.passed", "U", "a1", T0 + 1000, gate=R, by="r1", evidence={"elapsed_s": 100})
    log.complete("a1", "U", T0 + 1100)
    log.release("a1", "U", T0 + 1101)
    ans = run(log)
    assert bucket(ans, "(unknown size)")["items"] == 1
    assert bucket(ans, "< 50")["items"] == 0


def test_a_non_review_gate_finding_is_not_a_review_finding():
    log = seeded()
    log.add("review.triaged", "A", "a1", T0 + 1160, gate="ci", severity="LOW", verdict="confirmed")
    small = bucket(run(log), "< 50")
    assert small["findings"] == 1  # only the rubber_duck one


def test_a_finding_before_the_window_is_not_counted():
    log = seeded()
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    ans = M.run(log.events, st, cfg, now, view="batch", since="99h")  # after every event
    assert all(b["findings"] == 0 and b["runs"] == 0 for b in ans.metrics["buckets"])


def test_lock_wait_is_rendered_and_counts_as_a_figure():
    from ddflow.services.metrics import batch as B

    ans = run(seeded())
    assert "lock wait p50 30s" in B.text(ans.metrics)
    only_wait = {
        "buckets": [
            {
                "bucket": "x",
                "elapsed": {"p50_s": None, "n": 0},
                "lock_wait": {"p50_s": 30, "n": 3},
                "findings_per_run": None,
                "runs_with_finding": None,
                "confirmed_rate": None,
            }
        ]
    }
    assert B.has_figures(only_wait)


def overlap_log(sizes: dict[str, int] | None = None) -> Log:
    """X [0,100] on a/**, Y [50,150] on b/**, Z [60,200] on a/x.py, W [500,600] on c/**.
    X-Y and Y-Z overlap in time and are path-disjoint; X-Z overlap in time AND in paths; W is
    alone. Sizes default to small."""
    sizes = sizes or {}
    log = Log()
    rows = (("X", 0, 100, "a/**"), ("Y", 50, 150, "b/**"), ("Z", 60, 200, "a/x.py"))
    rows += (("W", 500, 600, "c/**"),)
    for item, s, e, globs in rows:
        log.add("task.added", item, "lead", T0 - 1000, title=item, body="")
        log.claim("a1", item, T0 + s, globs=globs)
        n = sizes.get(item, 10)
        log.add("gate.started", item, "a1", T0 + s + 1, gate=R)
        log.add(
            "gate.passed",
            item,
            "a1",
            T0 + s + 2,
            gate=R,
            by="r1",
            evidence={"elapsed_s": 5, "diff_stat": {"insertions": n, "deletions": 0}},
        )
        log.complete("a1", item, T0 + e - 1)
        log.release("a1", item, T0 + e)
    return log


def test_overlap_table_counts_time_overlapping_and_path_disjoint_pairs():
    ov = run(overlap_log()).metrics["overlap"]
    assert ov["items"] == 4
    assert ov["pairs_overlapping"] == 3  # X-Y, X-Z, Y-Z
    assert ov["pairs_path_disjoint"] == 2  # X-Y, Y-Z


def test_batch_eligible_share_needs_a_disjoint_partner_and_a_small_size():
    ov = run(overlap_log()).metrics["overlap"]
    assert ov["eligible_items"] == 3  # X, Y, Z each have a disjoint overlapping partner
    assert ov["eligible_share"] == pytest.approx(0.75)
    big = run(overlap_log({"Y": 5000})).metrics["overlap"]
    assert big["eligible_items"] == 0  # X and Z only pair with the oversized Y


def test_overlap_is_none_not_zero_below_min_samples():
    st, cfg, now = frozen(overlap_log())
    cfg.metrics.min_samples = 10
    ov = M.run(overlap_log().events, st, cfg, now, view="batch").metrics["overlap"]
    assert ov["eligible_share"] is None


def test_overlap_text_names_the_table():
    from ddflow.services.metrics import batch as B

    assert "batch-eligible" in B.text(run(overlap_log()).metrics)


def test_an_item_with_one_glob_less_lease_is_never_disjoint():
    log = overlap_log()
    log.claim("a1", "Z", T0 + 70)  # a re-claim of Z that names no globs
    log.release("a1", "Z", T0 + 190)
    ov = run(log).metrics["overlap"]
    assert ov["pairs_path_disjoint"] == 1  # only X-Y; Z has an unknown-globs lease


def test_the_pair_cap_counts_exactly_the_cap(monkeypatch):
    from ddflow.services.metrics import overlap as OV

    monkeypatch.setattr(OV, "MAX_PAIRS", 2)
    ov = run(overlap_log()).metrics["overlap"]
    assert ov["truncated"] is True
    assert ov["pairs_overlapping"] == 2


def test_overlap_figures_alone_count_as_data():
    from ddflow.services.metrics import batch as B

    empty = {"buckets": [], "overlap": {"items": 0}}
    assert not B.has_figures(empty)
    assert B.has_figures({"buckets": [], "overlap": {"items": 3}})


def test_a_truncated_overlap_scan_gives_no_share(monkeypatch):
    from ddflow.services.metrics import overlap as OV

    monkeypatch.setattr(OV, "MAX_PAIRS", 2)
    assert run(overlap_log()).metrics["overlap"]["eligible_share"] is None
