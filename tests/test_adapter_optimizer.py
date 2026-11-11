"""The `Optimizer` port: the default adapter (greedy-v1) and the fake pass the same cases.

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py).
"""

from __future__ import annotations

import pytest

from ddflow.adapters import optimizer as O
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.optimizer import FakeOptimizer
from ddflow.adapters.optimizer import Candidate, Optimizer, Slots


def _c(id_: str, bug: bool = False, prio: int = 100, *globs: str, tree: bool = True) -> Candidate:
    return Candidate(id_, (("bugs_first", not bug), ("priority", prio)), tuple(globs), (), tree)


def _overlap(c: Candidate, taken) -> str:
    for t in taken:
        if set(c.globs) & set(t.globs):
            return t.id
    return ""


def optimizer_cases(o) -> None:
    items = [_c("c", False, 5), _c("a", False, 5), _c("b", True, 50), _c("d", False, 1)]
    # ordered: bugs first, then priority, then id; every item once, each with its terms
    placed = o.order({}, items)
    assert sorted(p.id for p in placed) == list("abcd")
    assert placed[0].id == "b"  # a bug fix is offered first
    assert [p.rank for p in placed] == [0, 1, 2, 3]
    assert all(p.terms for p in placed)
    assert all(o.explain(p) for p in placed)
    # deterministic: the input order does not matter (a total order)
    assert [p.id for p in o.order({}, items[::-1])] == [p.id for p in placed]
    # an unknown ctx key is ignored (open mapping: lock-wait signals arrive later)
    assert [p.id for p in o.order({"lock_wait": {"a": 9}}, items)] == [p.id for p in placed]
    # cut: never more than the flight slots, nor tree-needing items than the tree slots
    ready = [_c("x"), _c("y"), _c("z", tree=False)]
    r = o.cut({}, ready, Slots(2, 1))
    assert r.ids("taken") == ["x", "z"] and r.ids("cut") == ["y"]
    assert o.cut({}, ready, Slots(0, 0)).ids("taken") == []
    # an overlapping item is reported with the taken item it clashes with, not dropped
    lap = [_c("p", False, 1, "src/*"), _c("q", False, 1, "src/*"), _c("r", False, 1, "doc/*")]
    r = o.cut({}, lap, Slots(3, 3), clash=_overlap)
    assert r.ids("taken") == ["p", "r"]
    assert [(v.id, v.other) for v in r.verdicts if v.what == "clash"] == [("q", "p")]
    # a held item takes no slot and is not offered to the overlap check
    r = o.cut({}, lap, Slots(3, 3), hold=lambda c: c.id == "p", clash=_overlap)
    assert r.ids("held") == ["p"] and r.ids("taken") == ["q", "r"]
    # one verdict per item, in walk order
    assert [v.id for v in r.verdicts] == ["p", "q", "r"]


def _real(monkeypatch):
    return O.get_optimizer("greedy-v1")


def _swarm(monkeypatch):
    return O.get_optimizer("swarm-v1")


CONFORMANCE = {"Optimizer": {"real": _real, "cases": optimizer_cases}}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(O.GreedyV1(), Optimizer)
    assert isinstance(FakeOptimizer(), Optimizer)
    assert FAKES["Optimizer"] is FakeOptimizer
    assert O.OptimizerPort is O.Optimizer


def test_the_adapter_passes_the_conformance_cases(monkeypatch):
    optimizer_cases(_real(monkeypatch))


def test_the_fake_passes_the_conformance_cases():
    optimizer_cases(FakeOptimizer())


def test_the_default_is_registered_and_an_unknown_name_lists_those_known():
    assert O.get_optimizer("") is O.get_optimizer("greedy-v1")
    with pytest.raises(KeyError, match="greedy-v1"):
        O.get_optimizer("nope")


def test_greedy_orders_bugs_then_priority_then_id():
    got = O.get_optimizer().order(
        {}, [_c("c", False, 5), _c("a", False, 5), _c("b", True, 50), _c("d", False, 1)]
    )
    assert [p.id for p in got] == ["b", "d", "a", "c"]


def test_explain_itemises_the_terms():
    p = O.get_optimizer().order({}, [_c("b", True, 50)])[0]
    assert O.get_optimizer().explain(p) == "b: rank 0 by bugs_first=0, priority=50"


def test_the_fake_pins_an_order_and_records_calls():
    f = FakeOptimizer(ranking=["z", "a"])
    got = f.order({"k": 1}, [_c("a"), _c("m"), _c("z")])
    assert [p.id for p in got] == ["z", "a", "m"]
    assert f.calls == [("order", {"k": 1})]


def test_swarm_v1_is_a_second_registered_adapter():
    swarm = O.get_optimizer("swarm-v1")
    assert isinstance(swarm, Optimizer)
    assert swarm is not O.get_optimizer("greedy-v1")
    assert "swarm-v1" in O.OPTIMIZERS and "greedy-v1" in O.OPTIMIZERS
    assert O.GreedyV1.facts == frozenset()  # the default reads no ctx facts (fast path)
    assert O.SwarmV1.facts == frozenset({"critical", "waited_s"})


def test_swarm_orders_critical_then_waited_then_priority_then_id():
    items = [_c("c", False, 5), _c("a", False, 5), _c("b", True, 50), _c("d", False, 1)]
    ctx = {"facts": {"a": {"critical": True, "waited_s": 10.0}}}
    # 'a' is on the critical path, so it leads whatever its priority; the rest fall back to
    # the greedy terms (bugs first, then priority, then id).
    got = O.get_optimizer("swarm-v1").order(ctx, items)
    assert [p.id for p in got] == ["a", "b", "d", "c"]
    # deterministic: a total order, the input order does not matter
    assert [p.id for p in O.get_optimizer("swarm-v1").order(ctx, items[::-1])] == [
        p.id for p in got
    ]


def test_swarm_prefers_the_critical_path_over_a_longer_wait():
    # critical and waited DISAGREE: the critical item wins the early rank even though the
    # other waited far longer, so a scorer that swapped the two keys would still differ.
    items = [_c("q", False, 5), _c("p", False, 5)]
    ctx = {
        "facts": {
            "q": {"critical": True, "waited_s": 0.0},
            "p": {"critical": False, "waited_s": 99.0},
        }
    }
    assert [p.id for p in O.get_optimizer("swarm-v1").order(ctx, items)] == ["q", "p"]


def test_swarm_orders_the_longest_waiting_first():
    items = [_c("x", False, 5), _c("y", False, 5)]
    ctx = {"facts": {"x": {"waited_s": 9.0}, "y": {"waited_s": 99.0, "critical": False}}}
    assert [p.id for p in O.get_optimizer("swarm-v1").order(ctx, items)] == ["y", "x"]


def test_swarm_falls_back_to_the_greedy_terms_without_facts():
    # an absent ctx is a miss, not an error: the same order as greedy-v1
    items = [_c("c", False, 5), _c("a", False, 5), _c("b", True, 50), _c("d", False, 1)]
    assert [p.id for p in O.get_optimizer("swarm-v1").order({}, items)] == ["b", "d", "a", "c"]


def test_swarm_shares_the_greedy_overlap_cut():
    # `order` ranks (never filters); the ONE overlap check is `cut`, inherited unchanged --
    # a swarm order still refuses overlapping work at the cut, exactly like greedy-v1.
    lap = [_c("p", False, 1, "src/*"), _c("q", False, 1, "src/*"), _c("r", False, 1, "doc/*")]
    r = O.get_optimizer("swarm-v1").cut(
        {"facts": {"p": {"critical": True}, "q": {"critical": True}}},
        lap,
        Slots(3, 3),
        clash=_overlap,
    )
    assert r.ids("taken") == ["p", "r"]
    # both critical, overlapping items are still ordered (order ranks all) ...
    ordered = O.get_optimizer("swarm-v1").order(
        {"facts": {"p": {"critical": True}, "q": {"critical": True}}}, lap[:2]
    )
    assert [p.id for p in ordered] == ["p", "q"]
    # ... but the inherited cut drops q for overlapping p, so no double-booking is offered.
    assert [(v.id, v.other) for v in r.verdicts if v.what == "clash"] == [("q", "p")]


def test_swarm_treats_an_unmeasured_fact_as_neutral_not_an_error():
    # a present-but-None (or non-numeric/non-finite) fact is an open-ctx miss, not a
    # TypeError: it orders neutrally (0.0) rather than aborting the whole plan.
    items = [_c("t1", False, 5), _c("t2", False, 5)]
    ctx = {"facts": {"t1": {"waited_s": None}, "t2": {"waited_s": None}}}
    got = O.get_optimizer("swarm-v1").order(ctx, items)
    assert [p.id for p in got] == ["t1", "t2"]  # both neutral 0.0, tie falls to id
    # a non-numeric or non-finite waited_s is neutral too, and the reported fact is what
    # was read (0.0), so a quiet NaN does not break the deterministic total order.
    for bad in ("n/a", "nan", "inf", float("nan")):
        ctx2 = {"facts": {"t1": {"waited_s": bad}}}
        placed = O.get_optimizer("swarm-v1").order(ctx2, items)
        assert placed[0].terms[1] == ("waited_s", 0.0), bad
    # an oversized number too big for a float is a miss too (OverflowError), not a crash
    ctx3 = {"facts": {"t1": {"waited_s": 10**400}}}
    placed3 = O.get_optimizer("swarm-v1").order(ctx3, items)
    assert placed3[0].terms[1] == ("waited_s", 0.0)


def test_swarm_explain_itemises_critical_and_wait():
    p = O.get_optimizer("swarm-v1").order({}, [_c("b", True, 50)])[0]
    assert (
        O.get_optimizer("swarm-v1").explain(p).startswith("b: rank 0 by critical=0, waited_s=0.0")
    )
    # a non-zero fact is itemised, so the explain text reports the fact it actually read
    q = O.get_optimizer("swarm-v1").order(
        {"facts": {"a": {"critical": True, "waited_s": 10.0}}}, [_c("a", False, 5)]
    )[0]
    assert (
        O.get_optimizer("swarm-v1").explain(q).startswith("a: rank 0 by critical=1, waited_s=10.0")
    )
