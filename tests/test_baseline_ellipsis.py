"""Bug B710b7cc84f: pytest ellipsizes a long repr to a fixed length (``head...tail``), so the same
failure printed from the ci tree and from the base's scratch tree (paths of different lengths)
is cut at different places. After the paths are blanked the two texts still differ, and the
test read as a new offender although main fails it the same way."""

from __future__ import annotations

from _pytest._io.saferepr import _ellipsize

from ddflow.services.gates import baseline_red as BR

ARGS = "args=['{root}/.venv/bin/python', '-c', \"import ddflow.services.swarm.ledger, ddflow.x\"]"
TAIL = " ImportError: cannot import name 'x' from partially initialized module (most likely due to a circular import) ({root}/ddflow/services/swarm/ledger.py)"


def _text(root: str, tail: str = TAIL) -> str:
    return "+  where 1 = " + _ellipsize(
        "CompletedProcess(" + ARGS.format(root=root) + tail.format(root=root) + ")", 240
    )


CI = "/tmp/ddflow-ci.h56ye4wg"
BASE = "/tmp/ddflow-base.k3j2h1x9"


def test_the_same_failure_ellipsized_at_different_places_is_not_a_new_offender():
    a, b = _text(CI), _text(BASE)
    assert "..." in a and a != b
    assert not BR._differs("t::x", {"t::x": a}, {"t::x": b})


def test_a_different_tail_in_an_ellipsized_text_is_still_a_new_offender():
    other = TAIL.replace("circular import", "missing module")
    a, b = _text(CI), _text(BASE, other)
    assert BR._differs("t::x", {"t::x": a}, {"t::x": b})


def test_a_different_head_in_an_ellipsized_text_is_still_a_new_offender():
    a = _text(CI)
    b = _text(BASE).replace("CompletedProcess(", "FailedProcess(")
    assert BR._differs("t::x", {"t::x": a}, {"t::x": b})


def test_whole_texts_that_differ_still_differ():
    assert BR._differs("t::x", {"t::x": "boom 1"}, {"t::x": "boom 2"})


def test_a_changed_visible_value_is_not_read_as_a_cut():
    a, b = {"t::x": "AssertionError x=1...y=2"}, {"t::x": "AssertionError x=10...y=2"}
    assert BR._differs("t::x", a, b)
    assert BR._differs("t::x", b, a)


def test_a_different_text_between_two_ellipses_is_still_a_new_offender():
    assert BR._differs("t::x", {"t::x": "foo...A...bar"}, {"t::x": "foo...B...bar"})
    assert not BR._differs("t::x", {"t::x": "foo...A...bar"}, {"t::x": "foo...A...bar"})


def test_an_equal_length_change_inside_the_visible_text_is_not_read_as_a_cut():
    a, b = {"t::x": "AssertionError abc...def"}, {"t::x": "AssertionError ab...defg"}
    assert BR._differs("t::x", a, b)
    assert BR._differs("t::x", b, a)


def test_a_change_in_the_text_outside_the_cut_is_not_read_as_a_cut():
    a = {"t::x": "/tmp/ddflow-ci.h56ye4wg AssertionError abc...xxdef"}
    b = {"t::x": "/tmp/ddflow-base.k3j2h1x9 AssertionError abc...def"}
    assert len(a["t::x"]) == len(b["t::x"])  # equal raw length, but the cut is not at one place
    assert BR._differs("t::x", a, b)
    assert BR._differs("t::x", b, a)


def test_a_literal_ellipsis_in_the_tail_does_not_hide_a_cut():
    a, b = _text(CI, TAIL + "..."), _text(BASE, TAIL + "...")
    assert a != b
    assert not BR._differs("t::x", {"t::x": a}, {"t::x": b})
