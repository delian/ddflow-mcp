"""A failure whose text differs only in a memory address (``0x...``) is the SAME failure,
not a new offender (B9252d46a4b).

``test_baseline_reader_gate_accept_next_step`` fails on main and on a branch with an
assertion text that embeds ``<function next_step at 0x...>``. The address changes per
process (ASLR, a per-process id), so ``_differs`` saw ``branch != base`` and recorded a NEW
offender in an already-red test instead of baseline-red. An address-only difference must not
read as a new offender; a difference that is NOT only an address must.
"""

from __future__ import annotations

from ddflow.services.gates import baseline_red as BR


def _sig(text: str) -> str:
    return f"E  assert {text}"


def test_an_address_only_difference_is_not_a_new_offender():
    before = {"t::a": _sig("<function next_step at 0x7f1234>() == 'WHYNOT'")}
    onbase = {"t::a": _sig("<function next_step at 0x7f9876>() == 'WHYNOT'")}
    assert BR._differs("t::a", before, onbase) is False


def test_a_difference_beyond_the_address_is_a_new_offender():
    before = {"t::a": _sig("<function next_step at 0x7f1234>() == 'WHYNOT'")}
    onbase = {"t::a": _sig("<function first at 0x7f9876>() == 'WHYNOT'")}
    assert BR._differs("t::a", before, onbase) is True


def test_a_change_of_a_hex_value_is_still_a_new_offender():
    before = {"t::a": _sig("assert got == 0x10")}
    onbase = {"t::a": _sig("assert got == 0xff")}
    assert BR._differs("t::a", before, onbase) is True


def test_hex_inside_a_string_value_is_still_a_new_offender():
    before = {"t::a": _sig("assert 'arrived at 0x10' == 'ok'")}
    onbase = {"t::a": _sig("assert 'arrived at 0xff' == 'ok'")}
    assert BR._differs("t::a", before, onbase) is True


def test_the_same_text_is_not_a_new_offender():
    before = {"t::a": _sig("<function next_step at 0x7f1234>() == 'WHYNOT'")}
    onbase = {"t::a": _sig("<function next_step at 0x7f1234>() == 'WHYNOT'")}
    assert BR._differs("t::a", before, onbase) is False
