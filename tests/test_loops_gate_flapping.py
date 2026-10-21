"""gate_flapping counts rounds that END differently, not a failed round and its own settlement."""

from __future__ import annotations

from test_progress_loops import detect, ev, kinds


def _rounds(finals, settle=True):
    """Each round: started, failed, then (settle) a passing triage settlement."""
    evs = [ev(1, "task.added", "T", {"parent": "P"})]
    n = 2
    for final in finals:
        evs.append(ev(n, "gate.started", "T", {"gate": "critic"}))
        n += 1
        if final == "passed" and settle:
            evs.append(ev(n, "gate.failed", "T", {"gate": "critic", "reason": "finding"}))
            n += 1
        evs.append(ev(n, f"gate.{final}", "T", {"gate": "critic", "reason": "x"}))
        n += 1
    return evs


def test_failed_rounds_each_settled_by_triage_are_not_flapping():
    evs = _rounds(["passed"] * 6)
    assert "gate_flapping" not in kinds(detect(evs))


def test_unsettled_failed_round_then_passing_round_still_flaps():
    evs = _rounds(["failed", "passed"] * 5, settle=False)
    found = [f for f in detect(evs) if f.kind == "gate_flapping"]
    assert found and found[0].count == 9
