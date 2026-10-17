"""A count both sides re-recorded is not a prose conflict for the pins merge driver
(Be71986b1d3: `git merge main` printed "no file to keep" and left README.md conflicted)."""

from __future__ import annotations

from ddflow.services.pins_text import merge_pin_text

BASE = b"<!-- ddflow:begin t -->\nx\n<!-- ddflow:end t -->\nintro\n\nthe rest of the 320 knobs.\n\nmiddle\n\n(5 of the 320)\n\nend\n"


def test_both_sides_changing_a_count_keeps_our_side_without_a_conflict() -> None:
    ours = BASE.replace(b"320", b"321")
    theirs = BASE.replace(b"320", b"323")
    out, conflicted = merge_pin_text(ours, BASE, theirs)
    assert not conflicted
    assert b"<<<<<<<" not in out
    assert out.count(b"321") == 2


def test_a_prose_edit_both_sides_made_still_conflicts() -> None:
    ours = BASE.replace(b"intro", b"hello")
    theirs = BASE.replace(b"intro", b"howdy")
    out, conflicted = merge_pin_text(ours, BASE, theirs)
    assert conflicted and b"<<<<<<<" in out


def test_a_hand_edited_number_still_conflicts() -> None:
    base = BASE + b"timeout: 3\n"
    out, conflicted = merge_pin_text(
        base.replace(b"3\n", b"5\n"), base, base.replace(b"3\n", b"9\n")
    )
    assert conflicted and b"<<<<<<<" in out
