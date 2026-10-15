"""`core.schedule.waves`: what a PLAN can run together, wave by wave (B-gap-plan-lint).

The function is pure and takes the plan it is given: `edges`/`globs_of` say how to read an
item, so these tests use the plan's own shape (id -> {"needs", "globs"}) without the
function knowing anything about `Item` records or the queue.
"""

from __future__ import annotations

from collections import Counter

import pytest

from ddflow.core.schedule import waves


def _plan(*specs: tuple[str, list[str], list[str]]) -> dict[str, dict]:
    return {i: {"needs": list(n), "globs": list(g)} for i, n, g in specs}


def _needs(it: dict) -> list[str]:
    return it["needs"]


def _globs(it: dict) -> list[str]:
    return it["globs"]


def _waves(plan: dict, **kw) -> dict[str, int]:
    return waves(plan, edges=_needs, globs_of=_globs, **kw)


def test_a_dag_gets_the_hand_computed_levels():
    """a -> (b, c) -> d: each wave is one more than the deepest thing it waits on."""
    plan = _plan(
        ("a", [], ["src/a"]),
        ("b", ["a"], ["src/b"]),
        ("c", ["a"], ["src/c"]),
        ("d", ["b", "c"], ["src/d"]),
    )

    assert _waves(plan) == {"a": 1, "b": 2, "c": 2, "d": 3}


def test_a_dependency_LATER_in_the_mapping_still_orders_the_item():
    """The answer may not depend on the order the plan happens to hold: `a` waits on `z`,
    which a single sorted sweep would place as though it were already free."""
    plan = _plan(("a", ["z"], ["src/a"]), ("z", [], ["src/z"]))

    assert _waves(plan) == {"z": 1, "a": 2}


def test_two_independent_items_that_could_not_run_together_are_re_ordered():
    """Re-ordering beats blocking, the same trade the conflict report makes: both COULD
    start, but a claim on one refuses a lease for the other, so one wave is spent on each."""
    clashing = _plan(("a", [], ["docs/**"]), ("b", [], ["docs/guide.md"]))

    assert _waves(clashing) == {"a": 1, "b": 2}

    # ...and with globs that do not overlap both start together, so the re-order above is
    # about the CONFLICT and not about waves being one item wide.
    apart = _plan(("a", [], ["src/a"]), ("b", [], ["src/b"]))
    assert _waves(apart) == {"a": 1, "b": 1}


def test_a_cap_spills_a_wave_and_no_wave_exceeds_it():
    plan = _plan(("a", [], ["x/a"]), ("b", [], ["x/b"]), ("c", [], ["x/c"]))

    got = _waves(plan, cap=2)

    assert got == {"a": 1, "b": 1, "c": 2}
    assert max(Counter(got.values()).values()) <= 2


def test_an_item_on_a_cycle_gets_no_wave_and_neither_does_what_waits_on_it():
    """Neither can ever start, so inventing a wave for them would be a lie a plan acts on."""
    plan = _plan(
        ("a", ["b"], ["x/a"]),
        ("b", ["a"], ["x/b"]),
        ("c", ["a"], ["x/c"]),
    )

    assert _waves(plan) == {}


def test_a_dependency_outside_the_plan_counts_as_satisfied():
    """This judges the plan's own items; the unknown id is `plan check`'s finding."""
    plan = _plan(("a", ["ghost"], ["x/a"]))

    assert _waves(plan) == {"a": 1}


def test_the_same_plan_in_another_order_gives_the_same_waves():
    plan = _plan(("a", [], ["x/a"]), ("b", [], ["x/a"]), ("c", ["b"], ["x/c"]))
    reversed_plan = {k: plan[k] for k in reversed(list(plan))}

    assert _waves(plan) == _waves(reversed_plan)


def test_a_cap_below_one_is_refused():
    """A wave holding no items is not a wave: silently ignoring the cap would be worse."""
    plan = _plan(("a", [], ["x/a"]))

    with pytest.raises(ValueError, match="cap"):
        _waves(plan, cap=0)
