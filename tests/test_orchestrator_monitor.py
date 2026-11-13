"""The orchestrator's native monitor: what a wave should do next (Bd43790a326).

`fill_order` (B4e2a112a97) answered *which ready item to claim next*. The loop around
it — resume an interrupted item, record a merged one, fill the next ready — was still
prose in the orchestrator prompt while the harness cron did the coordination. The
monitor is that loop's answer as a pure, testable function: one ordered action list,
so the coordinator is a derivation, not a comment (and no daemon: the trigger that
calls it stays pluggable, decision D-sched-no-daemon-r2).

The actions, most valuable first:

  1. ``recover``  -- an INTERRUPTED item (RUNNING with no live lease): resume it, so a
     crash's uncommitted work is salvaged before anything starts from scratch;
  2. ``complete`` -- an item that merged but was never completed: record it;
  3. ``fill``     -- a ready item in `fill_order` order, one claim each.

Refill stays the harness wave-driver's call (D-autofill-is-harness-pattern).
"""

from __future__ import annotations

from ddflow.core.model import fold
from ddflow.services.orchestrator import monitor


def _task(log, tid, *, parent, priority, tags=(), globs=None):
    log.append(
        "task.added",
        tid,
        {
            "parent": parent,
            "priority": priority,
            "tags": list(tags),
            "globs": globs if globs is not None else [f"{tid}/*"],
        },
    )


def _state(log):
    """One of each thing the monitor sees: interrupted, merged-not-completed, ready."""
    log.append("phase.added", "P1", {"title": "phase"})
    _task(log, "FEAT", parent="P1", priority=5)  # generic ready
    _task(log, "FIXBUG", parent="P1", priority=60, tags=["bugfix"])  # ready, a bug fix
    _task(log, "MERGE", parent="P1", priority=70)  # merged, never completed
    _task(log, "STALL", parent="P1", priority=50)  # interrupted: RUNNING, no lease
    log.append("item.started", "STALL", {})
    log.append("worktree.merged", "MERGE", {"sha": "a1b2c3d4e5f67890abcdef12"})
    return fold(log.read_all())


def test_monitor_recovers_then_completes_then_fills(log, cfg):
    st = _state(log)
    acts = monitor(cfg, st)
    assert [(a.verb, a.item) for a in acts] == [
        ("recover", "STALL"),
        ("complete", "MERGE"),
        ("fill", "FIXBUG"),
        ("fill", "FEAT"),
    ]


def test_monitor_recover_names_the_reason(log, cfg):
    st = _state(log)
    (first,) = [a for a in monitor(cfg, st) if a.item == "STALL"]
    assert first.verb == "recover"
    assert "no live lease" in first.why


def test_monitor_complete_keeps_the_merged_sha(log, cfg):
    st = _state(log)
    (rec,) = [a for a in monitor(cfg, st) if a.item == "MERGE"]
    assert rec.verb == "complete"
    assert "a1b2c3d4e5f6" in rec.why


def test_monitor_fill_offers_the_bug_before_the_generic_item(log, cfg):
    """Ready fills reuse `fill_order`: the bug fix leads the generic feature."""
    st = _state(log)
    fills = [a.item for a in monitor(cfg, st) if a.verb == "fill"]
    assert fills == ["FIXBUG", "FEAT"]


def test_merged_interrupted_item_is_completed_not_recovered(log, cfg):
    """A merged item is landed work: it is `complete`, never `recover`, even when it was
    started and never finished (roborev: recover is salvage, and a merged item has none).
    """
    log.append("phase.added", "P1", {"title": "phase"})
    _task(log, "MIXED", parent="P1", priority=10)
    log.append("item.started", "MIXED", {})
    log.append("worktree.merged", "MIXED", {"sha": "a1b2c3d4e5f67890abcdef12"})
    st = fold(log.read_all())
    verbs = [(a.verb, a.item) for a in monitor(cfg, st)]
    assert ("recover", "MIXED") not in verbs
    assert ("complete", "MIXED") in verbs


def test_recover_orders_same_tier_items_by_priority(log, cfg):
    """Within a tier the actions are in value order (lower priority runs sooner), not
    insertion order."""
    log.append("phase.added", "P1", {"title": "phase"})
    _task(log, "P90", parent="P1", priority=90)  # inserted first: would lead insertion order
    _task(log, "P10", parent="P1", priority=10)  # lower number runs sooner
    log.append("item.started", "P90", {})
    log.append("item.started", "P10", {})
    st = fold(log.read_all())
    recovers = [a.item for a in monitor(cfg, st) if a.verb == "recover"]
    assert recovers == ["P10", "P90"]
