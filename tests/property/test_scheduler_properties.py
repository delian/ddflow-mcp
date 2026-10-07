"""The scheduler, as properties (B-uni-property-tests; D-unify 4).

`schedule.plan` answers `next`, `brief` and `wait`. Whatever log the strategies build and
whoever asks, an offer must be safe to take:

- never before its dependencies: every need of the item and of each ancestor is DONE
  (checked against an oracle written here, not against `dep_status`);
- never two conflicting offers: no two ready items' globs overlap (a shared glob overlaps
  nothing), and no ready item overlaps, or is, another agent's live lease;
- never more offers than free slots under `schedule.max_parallel_tasks`;
- every candidate lands in exactly one bucket, and the same inputs give the same plan.
"""

from __future__ import annotations

import dataclasses

from evstrategies import AGENTS, EPOCH, GLOBS, PHASES, TASKS, event, event_logs
from hypothesis import given, settings
from hypothesis import strategies as st

from ddflow.config import Config
from ddflow.core.model import ABANDONED, BLOCKED, DONE, fold
from ddflow.core.schedule import conflicts, plan

SETTINGS = settings(max_examples=150, deadline=None, database=None)


@st.composite
def projects(draw):
    """A log that DEFINES every task first -- few needs, shared globs -- then runs the
    general event mix over it. A plain random log mostly yields an empty ready set; this
    one makes several items ready at once, which is when offers can collide."""
    events = []
    for pid in PHASES:
        events.append(event("phase.added", pid, {"title": pid}, "a1", len(events) + 1))
    for tid in TASKS:
        data = {
            "title": tid,
            "parent": draw(st.sampled_from(["", *PHASES])),
            "needs": draw(st.lists(st.sampled_from(TASKS), max_size=1))
            if draw(st.booleans())
            else [],
            "globs": draw(st.lists(st.sampled_from(GLOBS), min_size=1, max_size=2, unique=True)),
            "priority": draw(st.integers(0, 3)),
        }
        events.append(event("task.added", tid, data, "a1", len(events) + 1))
    tail = draw(event_logs(max_size=12))
    return events + [dataclasses.replace(e, lamport=e.lamport + len(events), id="") for e in tail]


@st.composite
def scenarios(draw):
    events = draw(st.one_of(event_logs(), projects()))
    state = fold([dataclasses.replace(e, id=e.id or e.compute_id()) for e in events], strict=False)
    base = Config()
    cfg = dataclasses.replace(
        base,
        schedule=dataclasses.replace(
            base.schedule,
            max_parallel_tasks=draw(st.integers(1, 6)),
            bugs_first=draw(st.booleans()),
        ),
        lease=dataclasses.replace(
            base.lease, shared_globs=draw(st.sampled_from([[], ["CHANGELOG.md"]]))
        ),
    )
    now = EPOCH + draw(st.integers(0, 9000))
    agent = draw(st.sampled_from([*AGENTS, "me"]))
    return state, cfg, now, agent


def _deps_met(state, item_id: str) -> bool:
    """The oracle: every need of the item, and every need of an ancestor that does not
    point into the item's own subtree or at one of its ancestors (an umbrella's need on
    its own child is met by running it -- `inherited_deps`, B43447abfc8), names a live
    item that is DONE (the default `unknown_dep_policy = "block"`; no external or
    stacked needs are generated)."""
    own_subtree = state.descendants(item_id) | {item_id} | {a.id for a in state.ancestors(item_id)}
    seen: set[str] = set()
    cur = state.items.get(item_id)
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        for dep in cur.needs:
            if cur.id != item_id and dep in own_subtree:
                continue
            d = state.items.get(dep)
            if d is None or d.removed or d.state != DONE:
                return False
        cur = state.items.get(cur.parent) if cur.parent else None
    return True


@SETTINGS
@given(scenarios())
def test_an_offer_is_never_made_before_its_dependencies(sc):
    state, cfg, now, agent = sc
    p = plan(state, cfg, now=now, agent=agent)
    for it in p.ready:
        assert _deps_met(state, it.id), it.id
        assert not it.removed and it.state not in (DONE, ABANDONED, BLOCKED), it.id
        assert not state.open_descendants(it.id), f"{it.id} is an umbrella"


@SETTINGS
@given(scenarios())
def test_no_two_offers_conflict_and_none_overlaps_another_agents_lease(sc):
    """Checked twice: with `conflicts`, the predicate `plan` uses, and with an oracle that
    does not share its code -- the same non-shared glob on both sides is always a clash, and
    a lease is live while `now - renewed_at <= ttl_s + grace_s`."""
    state, cfg, now, agent = sc
    p = plan(state, cfg, now=now, agent=agent)
    shared = cfg.lease.shared_globs
    for i, a in enumerate(p.ready):
        for b in p.ready[i + 1 :]:
            assert not conflicts(a.globs, b.globs, shared), (a.id, b.id)
            assert not (set(a.globs) & set(b.globs)) - set(shared), (a.id, b.id)
    live = {
        i.id: i.lease
        for i in state.items.values()
        if i.lease is not None and now - i.lease.renewed_at <= i.lease.ttl_s + cfg.lease.grace_s
    }
    assert live == state.active_leases(now, cfg.lease.grace_s)
    for it in p.ready:
        for held_id, lease in live.items():
            if lease.holder == agent:
                continue
            assert held_id != it.id, f"{it.id} is leased by {lease.holder}"
            assert not conflicts(it.globs, lease.globs, shared), (it.id, held_id)
            assert not (set(it.globs) & set(lease.globs)) - set(shared), (it.id, held_id)


@SETTINGS
@given(scenarios())
def test_offers_fit_the_free_slots(sc):
    state, cfg, now, agent = sc
    p = plan(state, cfg, now=now, agent=agent)
    in_flight = sum(
        1
        for i in state.active_leases(now, cfg.lease.grace_s)
        if i in state.items and not state.items[i].removed
    )
    assert len(p.ready) <= max(0, cfg.schedule.max_parallel_tasks - in_flight)


@SETTINGS
@given(scenarios())
def test_every_candidate_lands_in_one_bucket_and_the_plan_is_deterministic(sc):
    state, cfg, now, agent = sc
    p = plan(state, cfg, now=now, agent=agent)
    buckets = [
        [i.id for i in p.ready],
        [i.id for i in p.running],
        [i.id for i in p.review],
        [b.item for b in p.blocked],
    ]
    flat = [x for bucket in buckets for x in bucket]
    assert len(flat) == len(set(flat)), buckets
    open_tasks = {t.id for t in state.tasks("") if t.state not in (DONE, ABANDONED)}
    assert set(flat) == open_tasks
    again = plan(state, cfg, now=now, agent=agent)
    assert [i.id for i in again.ready] == buckets[0]
    assert [(b.item, b.reason, b.detail) for b in again.blocked] == [
        (b.item, b.reason, b.detail) for b in p.blocked
    ]
