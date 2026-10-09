"""Gate history is folded once (`Item.gate_history`) instead of scanned out of the log.

The review budget, the per-gate fire rates and the repeated-failure detector each used to
re-read the whole event log for it. They now read the fold, so these pin that the fold
agrees with the log it came from.
"""

from __future__ import annotations

import dataclasses

from ddflow.api import review as RV
from ddflow.core import progress as PR
from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.core.plain import plain
from ddflow.services import rates as RT


def _ev(n: int, kind: str, subject: str, data: dict) -> Event:
    return Event(
        id=f"e{n:03d}", ts=f"2026-10-0{1 + n // 60}T00:{n % 60:02d}:00.000000Z",
        lamport=n, agent="a", kind=kind, subject=subject, data=data,
    )  # fmt: skip


def _log() -> list[Event]:
    full = {"review_kind": "full", "status": "REVIEWED", "reviewed_head": "h1"}
    return [
        _ev(1, "phase.added", "P", {}),
        _ev(2, "task.added", "T", {"parent": "P"}),
        _ev(3, "gate.started", "T", {"gate": "critic"}),
        _ev(4, "gate.failed", "T", {"gate": "critic", "evidence": full}),
        _ev(5, "gate.out_of_order", "T", {"gate": "unit_tests"}),
        _ev(6, "gate.passed", "T", {"gate": "critic", "evidence": {**full, "reviewed_head": "h2"}}),
        _ev(7, "gate.skipped", "T", {"gate": "critic", "reason": "later"}),
        _ev(8, "gate.passed", "T", {"gate": "critic", "evidence": {**full, "reviewed_head": "h3", "passed_on_refutation": 1}}),
        _ev(9, "gate.partial", "T", {"gate": "critic", "evidence": {"review_kind": "delta", "status": "PARTIAL"}}),
        _ev(10, "gate.failed", "T", {"gate": "unit_tests", "evidence": {"output_digest": "d", "tree_sha": "t"}}),
    ]  # fmt: skip


def test_history_keeps_every_outcome_and_none_of_the_non_outcomes():
    st = fold(_log(), strict=False)
    hist = [(r.gate, r.outcome) for r in st.items["T"].gate_history]
    assert hist == [
        ("critic", "failed"),
        ("critic", "passed"),
        ("critic", "skipped"),
        ("critic", "passed"),
        ("critic", "partial"),
        ("unit_tests", "failed"),
    ]
    assert st.items["T"].gates["critic"].outcome == "partial", "gates keeps only the last"


def test_review_rounds_read_the_history_like_the_old_log_scan():
    it = fold(_log(), strict=False).items["T"]
    # Two full rounds count; the settled pass (passed_on_refutation) and the skip do not.
    assert RV._full_rounds(it, "critic") == 2
    assert RV._delta_rounds(it, "critic") == 1
    assert RV._rounds_used(it, "critic") == 3
    assert RV._last_head(it, "critic") == "h3"
    assert RV._full_rounds(None, "critic") == 0 and RV._last_head(None, "critic") == ""


def test_rates_count_the_history_and_agree_with_the_raw_events():
    evs = _log()
    by_state = RT.gate_rates(fold(evs, strict=False))
    assert by_state["critic"].outcomes == {"failed": 1, "passed": 2, "skipped": 1, "partial": 1}
    assert by_state["unit_tests"].outcomes == {"failed": 1}
    assert RT.gate_rates(evs).keys() == by_state.keys()
    assert {g: r.outcomes for g, r in RT.gate_rates(evs).items()} == {
        g: r.outcomes for g, r in by_state.items()
    }


def test_both_rates_paths_name_a_gate_less_outcome_after_its_item():
    """An outcome event with no `gate` is counted under the item's id on both paths."""
    evs = [
        _ev(1, "task.added", "T", {}),
        _ev(2, "gate.failed", "T", {}),
        _ev(3, "gate.passed", "T", {"gate": ""}),
    ]
    want = {"T": {"failed": 1, "passed": 1}}
    assert {g: r.outcomes for g, r in RT.gate_rates(evs).items()} == want
    assert {g: r.outcomes for g, r in RT.gate_rates(fold(evs, strict=False)).items()} == want


def test_progress_reads_gate_runs_from_the_history():
    evs = _log()
    w = PR.work(evs, fold(evs, strict=False))["T"]
    assert w.gate_outcomes["critic"] == ["failed", "passed", "skipped", "passed", "partial"]
    assert w.gate_runs_evidence["unit_tests"] == [("failed", "d", "t")]
    assert w.gate_runs == 6


def test_the_history_is_not_part_of_an_items_wire_form():
    it = fold(_log(), strict=False).items["T"]
    assert "gate_history" not in plain(it)
    assert "gates" in plain(it)
    assert "gate_history" in {f.name for f in dataclasses.fields(it)}


def _scan_full_rounds(events, item, gate):
    """The log scan this fold replaced, kept as the oracle."""
    return sum(
        1
        for e in events
        if e.subject == item
        and e.kind.startswith("gate.")
        and e.data.get("gate") == gate
        and (e.data.get("evidence") or {}).get("review_kind") == "full"
        and not (e.data.get("evidence") or {}).get("passed_on_refutation")
    )


def _scan_last_head(events, item, gate):
    for e in reversed(events):
        if e.subject == item and e.kind.startswith("gate.") and e.data.get("gate") == gate:
            head = (e.data.get("evidence") or {}).get("reviewed_head")
            if head:
                return str(head)
    return ""


def test_the_fold_agrees_with_the_scan_it_replaced_on_every_prefix():
    evs = _log()
    for n in range(1, len(evs) + 1):
        it = fold(evs[:n], strict=False).items.get("T")
        for gate in ("critic", "unit_tests", "rubber_duck"):
            assert RV._full_rounds(it, gate) == _scan_full_rounds(evs[:n], "T", gate), (n, gate)
            assert RV._last_head(it, gate) == _scan_last_head(evs[:n], "T", gate), (n, gate)
