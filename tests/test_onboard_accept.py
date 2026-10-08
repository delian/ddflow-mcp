"""The one accept/refuse selection every onboarding stage with an approval list uses."""

from __future__ import annotations

from ddflow.services import onboard as ON


def test_no_names_approves_everything_offered():
    assert ON.select_accepted(["a", "b"], [], lambda n: (n,)) == (["a", "b"], [])


def test_names_pick_by_any_of_an_items_names_and_unmatched_ones_come_back_in_order():
    offered = [("id1", "f1.md"), ("id2", "f2.md")]
    chosen, unmatched = ON.select_accepted(offered, ["f2.md", "nope", "id2", "nada"], lambda o: o)
    assert chosen == [("id2", "f2.md")]
    assert unmatched == ["nope", "nada"]


def test_every_name_missing_chooses_nothing():
    assert ON.select_accepted(["a"], ["z"], lambda n: (n,)) == ([], ["z"])
