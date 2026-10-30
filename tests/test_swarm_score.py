"""SW-score: the pure scheduler core (hard constraints never overridden by score)."""

from __future__ import annotations

from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

from ddflow.config import Config
from ddflow.services.swarm import ledger as L
from ddflow.services.swarm.score import (
    Item,
    Lane,
    Live,
    World,
    choose,
    evaluate,
    pull,
    rank,
    refusals,
    review_satisfied,
)
from ddflow.services.swarm.score_config import Weights

W = Weights()


def _world(cells=None, **kw):
    return World(ledger=L.Ledger(cells or {}), **kw)


def _cell(items, first_pass, rework=0, bugs=0):
    return L.Cell(items=items, first_pass=first_pass, rework=rework, bugs=bugs)


def test_weights_read_from_config():
    cfg = Config()
    cfg.swarm.w_fit = 7.0
    assert Weights.from_config(cfg).w_fit == 7.0
    assert Weights.from_config(Config()) == Weights()


def test_each_hard_constraint_refuses_with_a_reason():
    lane = Lane("a", "anthropic", "m")
    live = (Live("other", ("src/*.py",), "anthropic"),)
    cases = {
        "open needs": (Item("x", needs=("n",)), lane, _world()),
        "globs overlap": (Item("x", globs=("src/a.py",)), lane, _world(live=live)),
        "at its cap": (Item("x"), replace(lane, live=1), _world()),
        "trust tier": (Item("x", min_trust=2), lane, _world()),
        "window": (Item("x", est_in=10), replace(lane, window=5), _world()),
        "hedge cap": (Item("x", attempts=9), lane, _world()),
        "does not take kind": (Item("x", kind="doc"), replace(lane, kinds=("task",)), _world()),
        "independence": (Item("x"), lane, _world(reviewer_families=("anthropic",))),
    }
    for needle, (it, ln, wd) in cases.items():
        r = refusals(it, ln, wd, W)
        assert any(needle in x for x in r), (needle, r)


def test_done_needs_and_own_lease_do_not_block():
    wd = _world(done=frozenset({"n"}), live=(Live("x", ("src/a.py",)),))
    assert refusals(Item("x", needs=("n",), globs=("src/a.py",)), Lane("a", "f", "m"), wd, W) == []


def test_reviewer_family_must_differ():
    it, ln = Item("x"), Lane("r", "openai", "m", cap=2)
    assert refusals(it, ln, _world(), W, role="reviewer", author_family="openai")
    assert not refusals(it, ln, _world(), W, role="reviewer", author_family="anthropic")
    assert refusals(it, ln, _world(), W, role="reviewer", author_family="")


def test_unavailable_never_counts_as_a_pass():
    assert not review_satisfied("a", [("b", "unavailable"), ("b", "partial"), ("b", "skipped")])
    assert not review_satisfied("a", [("a", "passed")])
    assert review_satisfied("a", [("b", "unavailable"), ("c", "passed")])
    solid = _cell(10, 10)
    thin = L.Cell(items=10, first_pass=0, unverified=10, gates_unavailable=10)
    wd = _world({L.Key("f", "good", "task"): solid, L.Key("f", "weak", "task"): thin})
    it = Item("x")
    good = evaluate(it, Lane("g", "f", "good"), wd, W)
    weak = evaluate(it, Lane("w", "f", "weak"), wd, W)
    assert good.fit > weak.fit


def test_free_local_route_does_not_beat_better_qualified():
    wd = _world(
        {L.Key("cloud", "big", "task"): _cell(20, 19), L.Key("loc", "small", "task"): _cell(20, 8)}
    )
    it = Item("x", est_in=100_000, est_out=20_000)
    big = Lane("big", "cloud", "big", price_in=3.0, price_out=15.0, cap=2)
    loc = Lane("loc", "loc", "small", local=True, cap=2)
    top = choose(it, [loc, big], wd, W)
    assert top.lane == "big"
    ranked = rank(it, [loc, big], wd, W)
    assert "outqualified" in " ".join(ranked[1].reasons)


def test_cost_breaks_ties_inside_the_margin():
    wd = _world()
    it = Item("x", est_in=100_000, est_out=100_000)
    dear = Lane("dear", "a", "m1", price_in=10.0, price_out=30.0)
    cheap = Lane("cheap", "b", "m2", price_in=0.2, price_out=0.8)
    assert choose(it, [dear, cheap], wd, W).lane == "cheap"


def test_pull_prefers_critical_item_and_reports_parts():
    lane = Lane("a", "f", "m")
    d = pull(
        lane,
        [Item("b", critical=0.1), Item("a", critical=0.9), Item("c", needs=("zz",))],
        _world(),
        W,
    )
    assert d.item == "a" and dict(d.parts)["critical"] > 2.0
    assert pull(lane, [Item("c", needs=("zz",))], _world(), W) is None


def test_explore_picks_thin_pair_only_when_drawn():
    wd = _world({L.Key("a", "m1", "task"): _cell(20, 19)})
    lanes = [Lane("x", "a", "m1"), Lane("y", "b", "m2")]
    it = Item("i")
    assert choose(it, lanes, wd, W, draw=0.5).lane == "x"
    d = choose(it, lanes, wd, W, draw=0.01)
    assert d.lane == "y" and "explore" in " ".join(d.reasons)


_items = st.builds(
    Item,
    id=st.sampled_from(["a", "b", "c"]),
    kind=st.sampled_from(["task", "doc"]),
    globs=st.lists(st.sampled_from(["src/*.py", "docs/**", "src/a.py", "*"]), max_size=2).map(
        tuple
    ),
    needs=st.lists(st.sampled_from(["n1", "n2"]), max_size=2).map(tuple),
    critical=st.floats(0, 1),
    waited_s=st.floats(0, 1e5),
    est_in=st.integers(0, 10**5),
    est_out=st.integers(0, 10**5),
    attempts=st.integers(0, 3),
)
_lanes = st.builds(
    Lane,
    name=st.sampled_from(["l1", "l2", "l3"]),
    family=st.sampled_from(["f1", "f2", ""]),
    model=st.sampled_from(["m1", "m2"]),
    trust=st.integers(0, 2),
    price_in=st.one_of(st.none(), st.floats(0, 50)),
    price_out=st.one_of(st.none(), st.floats(0, 50)),
    window=st.one_of(st.none(), st.integers(1, 3 * 10**5)),
    local=st.booleans(),
    live=st.integers(0, 2),
    cap=st.integers(1, 2),
)
_weights = st.builds(
    Weights,
    w_critical=st.floats(0, 1e3),
    w_fit=st.floats(0, 1e3),
    w_cost=st.floats(0, 1e3),
    w_rework=st.floats(0, 1e3),
    w_queue=st.floats(0, 1e3),
    max_lanes=st.integers(0, 4),
    family_cap=st.integers(0, 3),
)
_worlds = st.builds(
    World,
    done=st.sets(st.sampled_from(["n1", "n2"])).map(frozenset),
    live=st.lists(
        st.builds(
            Live,
            item=st.sampled_from(["a", "b", "z"]),
            globs=st.just(("src/*.py",)),
            family=st.sampled_from(["f1", "f2"]),
        ),
        max_size=3,
    ).map(tuple),
)


@settings(max_examples=150, deadline=None)
@given(
    _items,
    st.lists(_lanes, min_size=1, max_size=3, unique_by=lambda lane: lane.name),
    _weights,
    _worlds,
)
def test_property_score_never_overrides_a_constraint_and_is_deterministic(item, lanes, w, wd):
    ranked = rank(item, lanes, wd, w)
    assert ranked == rank(item, list(reversed(lanes)), wd, w)
    assert len(ranked) == len(lanes)
    for d in ranked:
        ln = next(x for x in lanes if x.name == d.lane)
        assert d.ok == (not refusals(item, ln, wd, w))
    chosen = choose(item, lanes, wd, w)
    assert (chosen is None) == (not any(d.ok for d in ranked))
    if chosen:
        assert not refusals(item, next(x for x in lanes if x.name == chosen.lane), wd, w)


def test_hedged_leases_each_count_against_max_lanes_and_zero_refs_do_not_divide():
    w = replace(W, max_lanes=2, cost_ref_usd=0.0, rework_ref=0.0)
    wd = _world(
        {L.Key("f", "m", "task"): _cell(4, 2, rework=2)}, live=(Live("A", ()), Live("A", ()))
    )
    d = evaluate(Item("B", est_in=10), Lane("a", "f", "m", price_in=1.0), wd, w)
    assert not d.ok and any("max_lanes" in r for r in d.reasons)
