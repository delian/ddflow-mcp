"""DX-claim-guards: a claim refusal is a registered guard in its own file."""

from __future__ import annotations

import pytest

from ddflow.api.lifecycle import claim_guards as G
from ddflow.core import outcome as O
from ddflow.core.registry import DuplicateName


def _ctx(**kw):
    base = {
        "repo": None,
        "cfg": None,
        "events": [],
        "st": None,
        "item": "T1",
        "want": None,
        "me": "a",
    }
    return G.ClaimContext(**{**base, **kw})


@pytest.fixture
def clean():
    G.GUARDS.discover()
    saved = dict(G.GUARDS.table)
    yield
    G.GUARDS.table.clear()
    G.GUARDS.table.update(saved)


def test_the_builtin_guards_are_found_by_convention_in_run_order():
    assert [g.name for g in G.ordered()] == ["looping", "queued", "reserved"]


def test_a_new_guard_refuses_without_claim_py_being_edited(clean):
    seen = []

    @G.register("zz_quota", order=5)
    def quota(ctx):
        seen.append(ctx.item)
        return O.refused("item.claimed", "quota paused", id=ctx.item)

    out = G.first_refusal(_ctx())
    assert out is not None and not out
    assert seen == ["T1"]
    assert G.ordered()[0].name == "zz_quota", "order=5 runs before the built-ins"


def test_every_guard_passing_lets_the_claim_through(clean):
    G.GUARDS.table.clear()
    G.register("zz_ok")(lambda ctx: None)
    assert G.first_refusal(_ctx()) is None


def test_a_taken_name_is_an_error(clean):
    with pytest.raises(DuplicateName):
        G.register("looping")(lambda ctx: None)


def test_force_and_reclaim_are_the_usual_exemptions():
    assert _ctx(force=True).skip and _ctx(held_before=True).skip and not _ctx().skip
