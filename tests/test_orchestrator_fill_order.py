"""The orchestrator's wave fill-order: what a free lane claims first (B4e2a112a97).

The wave loop runs `ddflow next -> claim` with no explicit selection policy, so an
interrupted recovery, an open bug and a phase-less task were offered behind generic
ready work. The fill order is now an explicit, testable selection:

  1. recover  -- interrupted/orphaned items (RUNNING with no live lease): recover --plan,
                 then claim --force, so a crash's uncommitted work is salvaged first;
  2. bug      -- open-bug fixes (bugs-first);
  3. unphased -- tasks under no phase;
  4. ready    -- the remaining ready items, by priority then id.

`ddflow next` offers in this order (the schedule's ordering keys), and
`services/orchestrator.fill_order` is the same policy as a named function.
"""

from __future__ import annotations

from ddflow.core.model import fold
from ddflow.core.schedule import plan
from ddflow.services.orchestrator import fill_order


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
    """A mix of the four kinds, priorities chosen so only the fill order explains the rank."""
    log.append("phase.added", "P1", {"title": "phase"})
    _task(log, "FEAT", parent="P1", priority=5)  # generic ready, highest priority
    _task(log, "FIXBUG", parent="P1", priority=60, tags=["bugfix"])
    _task(log, "LOOSE", parent="", priority=10)  # no phase
    _task(log, "STALLED", parent="P1", priority=50)  # interrupted: RUNNING, no lease
    log.append("item.started", "STALLED", {})
    return fold(log.read_all())


def _ready_ids(st, cfg):
    return [i.id for i in plan(st, cfg).ready]


def test_interrupted_bug_unphased_generic_are_offered_in_fill_order(log, cfg):
    st = _state(log)
    assert _ready_ids(st, cfg) == ["STALLED", "FIXBUG", "LOOSE", "FEAT"]


def test_fill_order_is_the_same_policy_as_a_named_function(log, cfg):
    st = _state(log)
    live = st.active_leases(cfg.lease.grace_s)
    items = [st.items[i] for i in ("FEAT", "LOOSE", "STALLED", "FIXBUG")]  # scrambled
    assert [i.id for i in fill_order(cfg, st, items, live=live)] == [
        "STALLED",
        "FIXBUG",
        "LOOSE",
        "FEAT",
    ]


def test_bugs_first_off_still_offers_bugs_by_priority(log, cfg):
    """`bugs_first = false` only drops tier 2; recovery and unphased still lead."""
    cfg.schedule.bugs_first = False
    st = _state(log)
    assert _ready_ids(st, cfg) == ["STALLED", "LOOSE", "FEAT", "FIXBUG"]
